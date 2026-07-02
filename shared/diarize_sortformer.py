"""NeMo Sortformer diarization backend.

Selectable alternative to `diarize_lite`. End-to-end neural speaker
diarization via NVIDIA's Sortformer (up to 4 speakers). Expected to be
substantially more accurate on per-turn attribution than our
Silero+TitaNet+clustering pipeline, at the cost of a ~2 GB NeMo
install and CPU-only inference on macOS (torch MPS doesn't support
Sortformer's conv2d stack).

Exposes `diarize(audio_path, whisper_segments, n_speakers) -> dict`
matching `shared.diarize_lite.diarize`'s signature. The Sortformer
model returns its own speaker turns without needing Whisper
segments; we still accept `whisper_segments` so we can emit the
same consumer-friendly output shape (per-segment speaker labels
aligned to Whisper's text).

Reference implementation: `~/Downloads/transcribe.py` (Chris Laidler),
commit 3498342 registry entry.

Raises ModuleNotFoundError at call time if NeMo isn't installed so
selecting the Lite diarizer stays functional in envs without NeMo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


_DIAR_MODEL_NAME = "nvidia/diar_sortformer_4spk-v1"
_WINDOW_SEC = 300.0
_OVERLAP_SEC = 30.0


def _load_diarizer():
    """Load Sortformer once and return a CPU-bound model handle.

    Imports inside the function so that `shared.diarize_sortformer` is
    import-safe in environments without NeMo — the error surfaces only
    when the user actually selects Sortformer as active.
    """
    try:
        from nemo.collections.asr.models import SortformerEncLabelModel
    except ImportError as e:
        raise ModuleNotFoundError(
            "nemo-toolkit is not installed. Install it via the Model "
            "Manager (NeMo Sortformer row > Install) before selecting "
            "Sortformer as the active diarization backend."
        ) from e

    model = SortformerEncLabelModel.from_pretrained(model_name=_DIAR_MODEL_NAME)
    # Force CPU — Sortformer's conv2d stack hits
    # `convolution_overrideable` on torch MPS and either fails or silently
    # produces garbage. MPS would help inference speed, but correctness
    # beats latency here.
    try:
        import torch
        model = model.to(torch.device("cpu"))
    except Exception:
        pass
    model.eval()
    return model


def _run_window(model, audio_window: np.ndarray, offset_s: float):
    """Diarize one 300s window of audio, returning turns offset to the
    global timeline.

    Args:
        model: loaded Sortformer model.
        audio_window: float32 mono at 16 kHz.
        offset_s: start of this window within the full recording.

    Returns:
        list of (start_s, end_s, speaker_id) tuples, timestamps absolute.
    """
    # Sortformer takes a file path; write the window to a temp wav.
    import soundfile as sf
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    try:
        sf.write(tmp_path, audio_window, 16000)
        # `diarize` returns a list-of-lists of predicted segments;
        # each segment is [start_s, end_s, speaker_id].
        raw = model.diarize(audio=[tmp_path])
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    turns: list[tuple[float, float, str]] = []
    if not raw:
        return turns
    window_turns = raw[0] if isinstance(raw[0], list) else raw
    for item in window_turns:
        if isinstance(item, str):
            # NeMo Sortformer returns RTTM-style strings:
            # "<start_s> <end_s> <speaker_id>" (space-separated).
            parts = item.split()
            if len(parts) < 3:
                continue
            try:
                s, e, spk = float(parts[0]), float(parts[1]), parts[2]
            except ValueError:
                continue
        elif isinstance(item, dict):
            try:
                s = float(item["start"])
                e = float(item["end"])
                spk = str(item.get("speaker") or item.get("speaker_id"))
            except (KeyError, TypeError, ValueError):
                continue
        else:
            try:
                s, e, spk = float(item[0]), float(item[1]), str(item[2])
            except (IndexError, TypeError, ValueError):
                continue
        turns.append((s + offset_s, e + offset_s, spk))
    return turns


def _stitch_windows(
    windows: list[tuple[float, list[tuple[float, float, str]]]],
    overlap_sec: float = _OVERLAP_SEC,
) -> list[tuple[float, float, str]]:
    """Join per-window Sortformer turns into one globally-labelled list.

    Sortformer assigns speaker IDs independently per window — window 2's
    `speaker_0` may be window 1's `speaker_1`. This performs the
    majority-overlap join the windowing scheme relies on:

    1. **Label remapping** — for each window after the first, pair its raw
       labels with the previous windows' (already-remapped) global labels
       by maximum total temporal overlap of same-speaker turns inside the
       `overlap_sec` region at the window start (greedy one-to-one, largest
       overlap first). Raw labels with no overlap evidence get fresh global
       labels — genuinely new speakers stay distinct.
    2. **De-duplication** — both windows diarized the overlap region, so
       turns there would otherwise be emitted twice. Earlier windows keep
       the region up to the overlap midpoint; the new window keeps it from
       the midpoint on (turns straddling the midpoint are clipped). Each
       moment of audio is covered exactly once; the same-speaker merge in
       `diarize()` re-joins turns split at the midpoint.

    Args:
        windows: list of (offset_s, turns) per window, in chronological
            order. `turns` use absolute timestamps and raw per-window
            speaker labels (as returned by `_run_window`).
        overlap_sec: size of the inter-window overlap region.

    Returns:
        list of (start_s, end_s, global_label) tuples sorted by start.
        Global labels are synthetic (`"spk0"`, `"spk1"`, …) — `diarize()`
        renames them to "Speaker N" by first appearance, so only their
        cross-window consistency matters.
    """
    if not windows:
        return []

    next_global = 0

    def fresh() -> str:
        nonlocal next_global
        label = f"spk{next_global}"
        next_global += 1
        return label

    first_turns = sorted(windows[0][1])
    mapping: dict[str, str] = {}
    for _, _, raw in first_turns:
        if raw not in mapping:
            mapping[raw] = fresh()
    stitched: list[tuple[float, float, str]] = [
        (s, e, mapping[raw]) for s, e, raw in first_turns
    ]

    for offset, turns in windows[1:]:
        turns = sorted(turns)
        ov_start = offset
        ov_end = offset + overlap_sec
        mid = (ov_start + ov_end) / 2.0

        # Total same-time overlap between each (raw label, global label)
        # pair inside the overlap region.
        scores: dict[tuple[str, str], float] = {}
        for gs, ge, glab in stitched:
            cs, ce = max(gs, ov_start), min(ge, ov_end)
            if ce <= cs:
                continue
            for ns, ne, raw in turns:
                o = min(ce, ne) - max(cs, ns)
                if o > 0:
                    scores[(raw, glab)] = scores.get((raw, glab), 0.0) + o

        # Greedy one-to-one assignment, largest overlap first
        # (deterministic tie-break on labels).
        mapping = {}
        used_globals: set[str] = set()
        for (raw, glab), _score in sorted(
            scores.items(), key=lambda kv: (-kv[1], kv[0])
        ):
            if raw in mapping or glab in used_globals:
                continue
            mapping[raw] = glab
            used_globals.add(glab)
        for _, _, raw in turns:
            if raw not in mapping:
                mapping[raw] = fresh()

        # De-duplicate the overlap: earlier windows own [.., mid),
        # this window owns [mid, ..).
        stitched = [
            (gs, min(ge, mid), glab) for gs, ge, glab in stitched if gs < mid
        ]
        stitched.extend(
            (max(ns, mid), ne, mapping[raw]) for ns, ne, raw in turns if ne > mid
        )

    stitched.sort(key=lambda t: (t[0], t[1]))
    return stitched


def _pick_speaker_by_overlap(span_start: float, span_end: float, turns) -> str | None:
    """Return the speaker label whose turn overlaps `[span_start, span_end]`
    the most. Falls back to nearest turn centre when nothing overlaps —
    same philosophy as diarize_lite's no-overlap branch. Returns None
    only when `turns` is empty."""
    best_overlap = 0.0
    best_speaker: str | None = None
    for ts, te, spk in turns:
        overlap = max(0.0, min(span_end, te) - max(span_start, ts))
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = spk
    if best_speaker is not None:
        return best_speaker
    if not turns:
        return None
    mid = (span_start + span_end) / 2
    best_dist = float("inf")
    for ts, te, spk in turns:
        d = abs((ts + te) / 2 - mid)
        if d < best_dist:
            best_dist = d
            best_speaker = spk
    return best_speaker


def _assign_speakers_segment_level(whisper_segments, turns):
    """Whole-segment overlap match. Used when Whisper segments don't
    carry word-level timestamps (legacy path). Returns a list of dicts
    with start/end/text/speaker keys (no speaker_id yet — that's added
    once names are resolved)."""
    out = []
    for seg in whisper_segments:
        s, e = float(seg["start"]), float(seg["end"])
        spk = _pick_speaker_by_overlap(s, e, turns) or "Speaker 1"
        out.append({
            "start": s,
            "end": e,
            "text": seg.get("text", "").strip(),
            "speaker": spk,
        })
    return out


def _assign_speakers_word_level(whisper_segments, turns):
    """Per-word overlap match. Walks each Whisper segment's `words`
    list, assigns a speaker to each word, then breaks the segment
    wherever the speaker changes. This is the second-biggest lever
    flagged in PLAN-sortformer-diarization-2026-04-23.md: without it,
    multi-speaker Whisper segments get a single label and long
    mono-speaker runs survive whenever a real switch happens mid-
    sentence.

    Falls back to segment-level matching for any Whisper segment
    that's missing per-word timestamps (or whose word list is empty)."""
    out: list[dict] = []
    for seg in whisper_segments:
        words = seg.get("words") or []
        seg_start = float(seg["start"])
        seg_end = float(seg["end"])
        if not words:
            spk = _pick_speaker_by_overlap(seg_start, seg_end, turns) or "Speaker 1"
            out.append({
                "start": seg_start,
                "end": seg_end,
                "text": seg.get("text", "").strip(),
                "speaker": spk,
            })
            continue

        # Build per-word (start, end, text, speaker) then collapse runs
        runs: list[dict] = []
        for w in words:
            try:
                ws = float(w.get("start", seg_start))
                we = float(w.get("end", seg_end))
            except (TypeError, ValueError):
                continue
            wtext = (w.get("word") or w.get("text") or "").strip()
            if not wtext:
                continue
            spk = _pick_speaker_by_overlap(ws, we, turns) or "Speaker 1"
            if runs and runs[-1]["speaker"] == spk:
                runs[-1]["end"] = we
                runs[-1]["text"] = (runs[-1]["text"] + " " + wtext).strip()
            else:
                runs.append({"start": ws, "end": we, "text": wtext, "speaker": spk})

        if not runs:
            spk = _pick_speaker_by_overlap(seg_start, seg_end, turns) or "Speaker 1"
            out.append({
                "start": seg_start,
                "end": seg_end,
                "text": seg.get("text", "").strip(),
                "speaker": spk,
            })
        else:
            out.extend(runs)
    return out


def _collect_speaker_audio(audio: np.ndarray, turns, label: str, sr: int = 16000,
                           max_seconds: float = 10.0, min_turn_seconds: float = 1.0) -> np.ndarray:
    """Concatenate up to `max_seconds` of audio for one speaker, drawn
    from their longest turns first. Used to compute a stable speaker
    embedding for voice-library matching. Returns an empty array if
    the speaker has no turn at least `min_turn_seconds` long — too
    short to embed reliably (same minimum-duration logic as
    diarize_lite's `_MIN_EMBEDDING_DURATION`)."""
    spk_turns = [t for t in turns if t[2] == label and (t[1] - t[0]) >= min_turn_seconds]
    if not spk_turns:
        return np.zeros(0, dtype=np.float32)
    spk_turns.sort(key=lambda t: t[1] - t[0], reverse=True)
    pieces: list[np.ndarray] = []
    collected = 0.0
    for ts, te, _ in spk_turns:
        if collected >= max_seconds:
            break
        start_idx = max(0, int(ts * sr))
        end_idx = min(len(audio), int(te * sr))
        if end_idx <= start_idx:
            continue
        piece = audio[start_idx:end_idx]
        pieces.append(piece)
        collected += (end_idx - start_idx) / sr
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(pieces).astype(np.float32)


def _resolve_speaker_names(
    audio: np.ndarray, turns, internal_labels: list[str], sr: int = 16000,
) -> dict[str, str]:
    """Try to match each Sortformer speaker against the voice library.
    Returns a mapping from internal label ("Speaker 1", "Speaker 2", …)
    to display name — the enrolled name when there's a confident match,
    or the same "Speaker N" label otherwise. Silently returns identity
    mapping if TitaNet or the voice library aren't available."""
    fallback = {label: label for label in internal_labels}
    try:
        from shared.audio_utils import extract_embedding
        from shared.voice_library_lite import identify_speaker
        from shared.models import ensure_speaker_embed
        import onnxruntime as ort
    except Exception as e:
        print(f"Sortformer: voice library hooks unavailable ({e}); using generic labels", file=sys.stderr)
        return fallback

    try:
        model_path = ensure_speaker_embed()
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    except Exception as e:
        print(f"Sortformer: TitaNet load failed ({e}); using generic labels", file=sys.stderr)
        return fallback

    names: dict[str, str] = {}
    for label in internal_labels:
        chunk = _collect_speaker_audio(audio, turns, label, sr=sr)
        if chunk.size == 0:
            names[label] = label
            continue
        try:
            emb = extract_embedding(chunk, sr=sr, onnx_session=session)
            norm = float(np.linalg.norm(emb))
            if norm > 1e-10:
                emb = (emb / norm).astype(np.float32)
            matched, confidence = identify_speaker(emb, threshold=0.55)
        except Exception as e:
            print(f"Sortformer: embed/match failed for {label}: {e}", file=sys.stderr)
            names[label] = label
            continue
        if matched:
            names[label] = matched
            print(f"  Auto-matched {label} → {matched} ({confidence:.0%})", file=sys.stderr)
        else:
            names[label] = label
    return names


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
) -> dict:
    """Diarize with NeMo Sortformer.

    Signature and return shape match `shared.diarize_lite.diarize` so
    callers can swap backends without code changes. `n_speakers` is
    accepted but Sortformer caps at 4; the hint is informational only.
    """
    from shared.audio_utils import load_audio
    from shared.diarize_lite import (
        _anonymize_non_speech,
        _split_long_segments,
        _MAX_MERGED_SEGMENT_SECONDS,
    )

    audio_path = Path(audio_path)
    audio = load_audio(audio_path, sr=16000)
    total_dur = len(audio) / 16000.0

    model = _load_diarizer()

    # Window long audio — Sortformer runs out of memory on multi-hour
    # files in one shot. 300s windows with 30s overlap; per-window
    # speaker labels are then reconciled and de-duplicated by
    # `_stitch_windows` (majority-overlap join in the overlap region).
    all_turns: list[tuple[float, float, str]] = []
    step = int((_WINDOW_SEC - _OVERLAP_SEC) * 16000)
    win_samples = int(_WINDOW_SEC * 16000)
    if len(audio) <= win_samples:
        all_turns = _run_window(model, audio, 0.0)
    else:
        windows: list[tuple[float, list[tuple[float, float, str]]]] = []
        for start in range(0, len(audio), step):
            end = min(len(audio), start + win_samples)
            window = audio[start:end]
            offset = start / 16000.0
            turns = _run_window(model, window, offset)
            windows.append((offset, turns))
            if end >= len(audio):
                break
        all_turns = _stitch_windows(windows, overlap_sec=_OVERLAP_SEC)

    if not all_turns:
        # Sortformer returned nothing — fall through to a single-speaker
        # result rather than crashing the pipeline.
        print("Sortformer: no turns detected, returning single-speaker result", file=sys.stderr)
        segments_out = []
        for ws in whisper_segments:
            text = (ws.get("text") or "").strip()
            if not text:
                continue
            segments_out.append({
                "start": float(ws["start"]),
                "end": float(ws["end"]),
                "text": text,
                "speaker": "Speaker 1",
                "speaker_id": 0,
            })
        return {
            "version": 1,
            "audio_file": str(audio_path),
            "segments": segments_out,
            "speaker_names": {"0": "Speaker 1"},
            "backend": "sortformer",
        }

    # Merge consecutive same-speaker turns across window boundaries.
    all_turns.sort(key=lambda t: t[0])
    merged: list[list] = []
    for s, e, spk in all_turns:
        if merged and merged[-1][2] == spk and s - merged[-1][1] < 1.0:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, spk])
    all_turns = [(m[0], m[1], m[2]) for m in merged]

    # Normalize raw Sortformer IDs to stable "Speaker 1/2/…" labels in
    # order of first appearance (matches diarize_lite's behaviour).
    label_map: dict[str, str] = {}
    for _, _, spk in all_turns:
        if spk not in label_map:
            label_map[spk] = f"Speaker {len(label_map) + 1}"
    renamed_turns = [(s, e, label_map[spk]) for s, e, spk in all_turns]
    internal_labels = list(label_map.values())

    # Voice-library matching: per-speaker, concatenate up to 10s of
    # their longest turns and try identify_speaker against the user's
    # enrolled library. Adds enrolled-name auto-tagging parity with the
    # lite path (PLAN-diarization-improvements.md, step 10 in lite).
    display_names = _resolve_speaker_names(audio, renamed_turns, internal_labels, sr=16000)

    # Assign speakers per Whisper segment. Word-level alignment when
    # the Whisper output carries per-word timestamps; falls back to
    # whole-segment overlap matching otherwise.
    has_word_timestamps = any((seg.get("words") or []) for seg in whisper_segments)
    if has_word_timestamps:
        raw_segments = _assign_speakers_word_level(whisper_segments, renamed_turns)
        align_mode = "word-level"
    else:
        raw_segments = _assign_speakers_segment_level(whisper_segments, renamed_turns)
        align_mode = "segment-level"

    # Build the integer speaker_id space + speaker_names dict that
    # downstream consumers (RecordingsTableView, voice library tagging,
    # rediarize stats) expect. Keyed by str(spk_id) to match diarize_lite.
    label_to_id: dict[str, int] = {label: i for i, label in enumerate(internal_labels)}
    speaker_names: dict[str, str] = {
        str(spk_id): display_names.get(label, label)
        for label, spk_id in label_to_id.items()
    }

    for seg in raw_segments:
        label = seg.get("speaker") or internal_labels[0]
        spk_id = label_to_id.get(label, 0)
        seg["speaker_id"] = spk_id
        seg["speaker"] = speaker_names.get(str(spk_id), label)

    # Anonymise non-speech tokens (e.g. "[laughter]") — mirrors lite.
    raw_segments = _anonymize_non_speech(raw_segments)

    # Same-speaker merge so the final segments line up with how
    # consumers expect to read them (one block per turn).
    segments_out: list[dict] = []
    for seg in raw_segments:
        if not seg.get("text"):
            continue
        if segments_out and segments_out[-1]["speaker_id"] == seg["speaker_id"]:
            segments_out[-1]["end"] = seg["end"]
            segments_out[-1]["text"] = (segments_out[-1]["text"] + " " + seg["text"]).strip()
        else:
            segments_out.append(dict(seg))

    # Cap monster blocks. Two passes — the first split can still leave
    # chunks slightly over `max_duration` near sentence boundaries.
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)

    max_dur = max((s["end"] - s["start"] for s in segments_out), default=0)
    print(
        f"Sortformer: {len(renamed_turns)} turns, {len(internal_labels)} speakers, "
        f"{total_dur:.0f}s audio, {align_mode} alignment, "
        f"{len(segments_out)} output segments (max {max_dur:.0f}s)",
        file=sys.stderr,
    )

    return {
        "version": 1,
        "audio_file": str(audio_path),
        "segments": segments_out,
        "speaker_names": speaker_names,
        "backend": "sortformer",
    }
