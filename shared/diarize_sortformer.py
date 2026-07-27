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

from shared.word_timing import timed_words, words_to_text


_DIAR_MODEL_NAME = "nvidia/diar_sortformer_4spk-v1"
_WINDOW_SEC = 300.0
_OVERLAP_SEC = 30.0

# Cross-window voice linking. Sortformer re-labels speakers independently per
# window; temporal overlap in the 30 s handover region only identifies people
# who speak during the handover. On sparse calls one party is often silent
# there, and used to be re-minted as a new speaker per window. Embeddings give
# an identity signal that survives overlap silence. Conservative by design: a
# link needs both a strong similarity and a clear margin over the runner-up,
# because a wrong merge is far more destructive than an extra label (extra
# labels stay recoverable via the review/merge tools).
_LINK_MIN_SPEECH_SECONDS = 3.0   # below this a window label's embedding is unreliable
_LINK_SIMILARITY_THRESHOLD = 0.70        # TitaNet cosine space
_LINK_SIMILARITY_THRESHOLD_WESPEAKER = 0.65  # WeSpeaker cosine space (calibrated
# 2026-07-25 on the frozen 134-person benchmark library: 0.27% false-merge vs
# 19% missed-link at 0.65; the margin rule guards the heavy between-person tail)
_LINK_MARGIN = 0.05

# Micro-label absorption. After stitching, a label with only a few seconds of
# speech is almost always a diarizer tail-fragment, not a real participant
# (Rec07: a 9.1 s "Speaker 3" of goodbye smalltalk). Its embedding is too
# noisy for a margin check, so it is absorbed into its closest full-size
# voice when the plain similarity clears the link threshold; a fragment that
# matches nothing stays its own label.
_MICRO_LABEL_MAX_SECONDS = 12.0
_ABSORB_SIMILARITY_THRESHOLD = 0.70
# Auto-merging is allowed only when the affinity graph has a clear community
# structure. A weak graph must surface as an ambiguity, not silently turn a
# five-person call into a three-person result.
_GRAPH_AUTOMERGE_MIN_SCORE = 0.15

# A Sortformer turn can occasionally contain an entire conversation (Rec80's
# opening did): this is *under*-diarisation inside one window, not the more
# common cross-window duplicate-label problem.  Inspect only very long turns,
# and split only with strong repeated two-community evidence.  The high bar is
# important: an uncertain graph must never invent a new participant.
_MIXED_TURN_MIN_SECONDS = 24.0
_MIXED_TURN_CHUNK_SECONDS = 4.0
_MIXED_TURN_MAX_CHUNKS = 48
_MIXED_TURN_MIN_CHUNKS_PER_COMMUNITY = 3
_MIXED_TURN_MIN_SEPARATION = 0.14

# Splitting a label back apart when the user asks for MORE speakers than
# stitching produced. Sortformer under-splits same-channel calls (two people on
# one phone line share a label), and until now an explicit count could only ever
# merge labels down — so "Redetect at 4" on a 3-label result did nothing at all.
# The human-supplied count is the guardrail that makes this safe, exactly as it
# is for _merge_labels_to_count: we split at the strongest available voice
# evidence and stop short (with a log line) when there is none, rather than
# inventing a participant out of noise.
_SPLIT_MIN_TURN_SECONDS = 1.5      # shorter turns give unusable embeddings
_SPLIT_MIN_TURNS_PER_VOICE = 2     # a single turn is not a participant
_SPLIT_MIN_SEPARATION = 0.05       # below this the two groups are one voice


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na <= 1e-10 or nb <= 1e-10:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _mixed_turn_graph_partition(embeddings: list[np.ndarray]) -> list[int] | None:
    """Return a conservative two-community partition for one long turn.

    This is a tiny deterministic voice-affinity graph: nodes are consecutive
    four-second chunks and edge weight is cosine similarity.  Farthest-pair
    seeded two-means gives the partition; graph separation and temporal
    alternation decide whether it represents two people rather than normal
    within-voice variation.  Kept NumPy-only so it is testable and does not
    add a clustering dependency to the transcription install.
    """
    if len(embeddings) < _MIXED_TURN_MIN_CHUNKS_PER_COMMUNITY * 2:
        return None
    vectors = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms <= 1e-10):
        return None
    vectors = vectors / norms
    affinity = vectors @ vectors.T
    # Deterministic seeds: least-similar pair in the upper triangle.
    masked = affinity + np.eye(len(vectors), dtype=np.float32) * 2.0
    first, second = np.unravel_index(np.argmin(masked), masked.shape)
    if first == second:
        return None
    labels = np.zeros(len(vectors), dtype=np.int8)
    labels[second] = 1
    for _ in range(12):
        centroids = []
        for group in (0, 1):
            members = vectors[labels == group]
            if not len(members):
                return None
            center = members.mean(axis=0)
            length = np.linalg.norm(center)
            if length <= 1e-10:
                return None
            centroids.append(center / length)
        scores = vectors @ np.stack(centroids).T
        next_labels = np.argmax(scores, axis=1).astype(np.int8)
        # Ensure the initial seeds cannot collapse into one arbitrary group.
        next_labels[first] = 0
        next_labels[second] = 1
        if np.array_equal(next_labels, labels):
            break
        labels = next_labels
    counts = np.bincount(labels, minlength=2)
    if np.any(counts < _MIXED_TURN_MIN_CHUNKS_PER_COMMUNITY):
        return None
    same, cross = [], []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            (same if labels[i] == labels[j] else cross).append(float(affinity[i, j]))
    if not same or not cross or float(np.mean(same) - np.mean(cross)) < _MIXED_TURN_MIN_SEPARATION:
        return None
    # A real blended turn should change communities more than once.  A single
    # transition is usually one person entering after another, already a
    # boundary the diarizer may reasonably have missed but too weak to relabel.
    transitions = int(np.count_nonzero(labels[1:] != labels[:-1]))
    return labels.tolist() if transitions >= 2 else None


def _repair_mixed_turns_with_graph(audio: np.ndarray, turns):
    """Split grossly overlong Sortformer turns when their local voice graph
    proves they contain two alternating speakers.  Returns unchanged turns if
    embeddings are unavailable or evidence is ambiguous."""
    linker = _CrossWindowLinker(audio)
    session = linker._session_or_none()
    if session is None:
        return turns, 0
    try:
        from shared.audio_utils import extract_embedding
    except Exception:
        return turns, 0
    repaired, repairs = [], 0
    for turn_index, (start, end, label) in enumerate(turns):
        duration = end - start
        if duration < _MIXED_TURN_MIN_SECONDS:
            repaired.append((start, end, label))
            continue
        chunk_count = min(_MIXED_TURN_MAX_CHUNKS, int(np.ceil(duration / _MIXED_TURN_CHUNK_SECONDS)))
        chunk_seconds = duration / chunk_count
        chunks, embeddings = [], []
        failed = False
        for index in range(chunk_count):
            cs, ce = start + index * chunk_seconds, min(end, start + (index + 1) * chunk_seconds)
            sample = audio[int(cs * 16000):int(ce * 16000)]
            try:
                embedding = extract_embedding(sample, sr=16000, onnx_session=session)
            except Exception:
                failed = True
                break
            if embedding is None or np.linalg.norm(embedding) <= 1e-10:
                failed = True
                break
            chunks.append((cs, ce))
            embeddings.append(embedding)
        partition = None if failed else _mixed_turn_graph_partition(embeddings)
        if partition is None:
            repaired.append((start, end, label))
            continue
        # Retain the original label for its first community; the second raw
        # label is normalised later alongside all Sortformer labels.
        primary = partition[0]
        alternate = f"{label}__graph_split_{turn_index}"
        for (cs, ce), community in zip(chunks, partition):
            repaired.append((cs, ce, label if community == primary else alternate))
        repairs += 1
        print(
            f"Sortformer: graph split mixed {label} turn {start:.1f}-{end:.1f}s "
            f"into two voice communities",
            file=sys.stderr,
        )
    return repaired, repairs


class _CrossWindowLinker:
    """Per-window raw-label voice embeddings for cross-window linking.

    Lazily loads the strongest available speaker-embedding model on first
    use — WeSpeaker (the review-candidate model) when configured, TitaNet
    otherwise — and degrades to None (no link evidence) whenever the model
    or an extraction fails, so the stitcher falls back to legacy
    overlap-only behaviour. Linking decides same/different-person only; it
    names nobody, so using the review model here does not change its
    review-only role.
    """

    def __init__(self, audio: np.ndarray, sr: int = 16000):
        self.audio = audio
        self.sr = sr
        self.threshold = _LINK_SIMILARITY_THRESHOLD
        self.model_key: str | None = None
        self._session = None
        self._session_failed = False

    def _session_or_none(self):
        if self._session is None and not self._session_failed:
            try:
                self._session, self.model_key, self.threshold = self._load_best_session()
                print(
                    f"Sortformer: cross-window linking via {self.model_key}",
                    file=sys.stderr,
                )
            except Exception:
                self._session_failed = True
        return self._session

    def _load_best_session(self):
        """Prefer WeSpeaker (far stronger voice separation on this user's
        data); fall back to TitaNet when no review candidate is configured."""
        from pathlib import Path as _Path
        try:
            from shared.voice_candidate_review import load_candidate_config
            from shared.voice_library_lite import _get_speaker_embed_session
            config = load_candidate_config()
            if config.get("available") and config.get("model_path"):
                model_key = str(config.get("model_key") or "wespeaker_resnet293")
                session = _get_speaker_embed_session(model_key, _Path(config["model_path"]))
                if session is not None:
                    return session, model_key, _LINK_SIMILARITY_THRESHOLD_WESPEAKER
        except Exception:
            pass
        from shared.models import ensure_speaker_embed
        import onnxruntime as ort
        session = ort.InferenceSession(
            str(ensure_speaker_embed()), providers=["CPUExecutionProvider"]
        )
        return session, "titanet", _LINK_SIMILARITY_THRESHOLD

    def embed(self, turns, label: str, window_index: int | None = None):
        """Embedding for one raw label within one window, or None.

        `turns` are that window's turns with absolute timestamps; the linker
        slices the full recording with them. `window_index` is unused in
        production but lets tests key canned embeddings per window.
        """
        session = self._session_or_none()
        if session is None:
            return None
        chunk = _collect_speaker_audio(self.audio, turns, label, sr=self.sr)
        if chunk.size < int(self.sr * _LINK_MIN_SPEECH_SECONDS):
            return None
        try:
            from shared.audio_utils import extract_embedding
            emb = extract_embedding(chunk, sr=self.sr, onnx_session=session)
        except Exception:
            return None
        norm = float(np.linalg.norm(emb))
        if norm <= 1e-10:
            return None
        return (emb / norm).astype(np.float32)


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
    linker=None,
) -> list[tuple[float, float, str]]:
    """Join per-window Sortformer turns into one globally-labelled list.

    Sortformer assigns speaker IDs independently per window — window 2's
    `speaker_0` may be window 1's `speaker_1`. Two reconciliation passes:

    1. **Temporal overlap** — for each window after the first, pair its raw
       labels with the previous windows' (already-remapped) global labels
       by maximum total temporal overlap of same-speaker turns inside the
       `overlap_sec` region at the window start (greedy one-to-one, largest
       overlap first).
    2. **Voice embedding** (when `linker` is provided) — raw labels with no
       overlap evidence are compared against the seeded embeddings of
       existing global labels. A match needs similarity >=
       `_LINK_SIMILARITY_THRESHOLD` and a margin >= `_LINK_MARGIN` over the
       runner-up; anything weaker still gets a fresh global label. This is
       what stops a speaker who is silent through the handover region (a
       near-certainty on two-person calls) being re-minted as a new person
       every window. Overlap evidence always wins over embeddings, and no
       two raw labels in the same window may share a global label.
    3. **De-duplication** — both windows diarized the overlap region, so
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
        linker: optional `_CrossWindowLinker`-compatible object with an
            `embed(turns, label, window_index=...)` method returning a voice
            embedding (or None). When None, behaviour is exactly the legacy
            overlap-only stitch.

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

    global_embeddings: dict[str, np.ndarray] = {}

    def _embed(window_index: int, turns, raw: str):
        if linker is None:
            return None
        try:
            return linker.embed(turns, raw, window_index=window_index)
        except Exception:  # noqa: BLE001 - link evidence is best-effort
            return None

    def _raw_labels(turns) -> list[str]:
        seen: list[str] = []
        for _, _, raw in turns:
            if raw not in seen:
                seen.append(raw)
        return seen

    def _link(embedding, claimed: set[str]) -> str | None:
        """Best existing global label for an embedding, or None.

        Requires the similarity threshold and, with two or more candidates,
        a clear margin over the runner-up. Never links to a global already
        claimed by a different raw label in this window.
        """
        if embedding is None:
            return None
        threshold = getattr(linker, "threshold", None) or _LINK_SIMILARITY_THRESHOLD
        scored = sorted(
            (
                (glab, _cosine(embedding, gemb))
                for glab, gemb in global_embeddings.items()
                if glab not in claimed
            ),
            key=lambda kv: kv[1],
            reverse=True,
        )
        if not scored:
            return None
        best_label, best = scored[0]
        if best < threshold:
            return None
        if len(scored) > 1 and best - scored[1][1] < _LINK_MARGIN:
            return None
        return best_label

    first_turns = sorted(windows[0][1])
    mapping: dict[str, str] = {}
    for _, _, raw in first_turns:
        if raw not in mapping:
            mapping[raw] = fresh()
    if linker is not None:
        for raw in _raw_labels(first_turns):
            emb = _embed(0, first_turns, raw)
            if emb is not None:
                global_embeddings[mapping[raw]] = emb
    stitched: list[tuple[float, float, str]] = [
        (s, e, mapping[raw]) for s, e, raw in first_turns
    ]

    for window_index, (offset, turns) in enumerate(windows[1:], start=1):
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

        # Embedding pass: raw labels with no overlap evidence try to link
        # back to an existing voice instead of always minting a new person.
        window_embs: dict[str, object] = {}
        if linker is not None:
            for raw in _raw_labels(turns):
                window_embs[raw] = _embed(window_index, turns, raw)
            claimed = set(used_globals)
            for raw in _raw_labels(turns):
                if raw in mapping:
                    continue
                linked = _link(window_embs[raw], claimed)
                if linked is not None:
                    mapping[raw] = linked
                    claimed.add(linked)
                    print(
                        f"Sortformer: linked window-{window_index} {raw} → {linked} by voice",
                        file=sys.stderr,
                    )

        # Genuinely new voices (or unverifiable ones) get fresh global labels.
        for raw in _raw_labels(turns):
            if raw not in mapping:
                mapping[raw] = fresh()

        # Seed embeddings for labels that don't have one yet — both freshly
        # minted globals and overlap-mapped globals that previously had no
        # embeddable speech.
        if linker is not None:
            for raw in _raw_labels(turns):
                glab = mapping[raw]
                if glab not in global_embeddings and window_embs.get(raw) is not None:
                    global_embeddings[glab] = window_embs[raw]

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
        output = {
            "start": s,
            "end": e,
            "text": seg.get("text", "").strip(),
            "speaker": spk,
        }
        words = timed_words(seg)
        if words:
            output["words"] = words
        out.append(output)
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
        words = timed_words(seg)
        seg_start = float(seg["start"])
        seg_end = float(seg["end"])
        if not words:
            spk = _pick_speaker_by_overlap(seg_start, seg_end, turns) or "Speaker 1"
            # No `words` key here by definition — this is the segment-level
            # fallback for a Whisper segment that carried no word timings.
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
            ws = float(w["start"])
            we = float(w["end"])
            wtext = w["word"]
            spk = _pick_speaker_by_overlap(ws, we, turns) or "Speaker 1"
            if runs and runs[-1]["speaker"] == spk:
                runs[-1]["end"] = we
                runs[-1]["words"].append(w)
                runs[-1]["text"] = words_to_text(runs[-1]["words"])
            else:
                runs.append({
                    "start": ws,
                    "end": we,
                    "text": wtext,
                    "words": [w],
                    "speaker": spk,
                })

        if not runs:
            spk = _pick_speaker_by_overlap(seg_start, seg_end, turns) or "Speaker 1"
            output = {
                "start": seg_start,
                "end": seg_end,
                "text": seg.get("text", "").strip(),
                "speaker": spk,
            }
            out.append(output)
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
        # Truncate the final piece at the remaining budget: the cap used to
        # only stop adding *further* pieces, so one long turn could produce a
        # multi-minute chunk — and TitaNet's ONNX graph fails with a
        # broadcast error on very long inputs.
        remaining = int((max_seconds - collected) * sr)
        piece = audio[start_idx:min(end_idx, start_idx + remaining)]
        pieces.append(piece)
        collected += len(piece) / sr
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(pieces).astype(np.float32)


def _resolve_speaker_names(
    audio: np.ndarray,
    turns,
    internal_labels: list[str],
    sr: int = 16000,
    allowed_names=None,
) -> dict[str, dict]:
    """Try to match each Sortformer speaker against the voice library.

    Returns a mapping from internal label ("Speaker 1", "Speaker 2", …) to a
    per-speaker info dict:
        {"name": str, "source": "auto"|"generic", "confidence": float|None,
         "embedding": list[float]|None}
    - `name` is the enrolled name on a confident match, else the "Speaker N"
      label.
    - `source` is "auto" when matched from the voice library, else "generic".
    - `embedding` is the L2-normalised TitaNet embedding (persisted so a later
      `rematch` can re-identify without touching the audio again).
    Silently returns generic identity info if TitaNet or the voice library
    aren't available."""
    fallback = {
        label: {"name": label, "source": "generic", "confidence": None, "embedding": None}
        for label in internal_labels
    }
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

    info: dict[str, dict] = {}
    for label in internal_labels:
        chunk = _collect_speaker_audio(audio, turns, label, sr=sr)
        if chunk.size == 0:
            info[label] = {"name": label, "source": "generic", "confidence": None, "embedding": None}
            continue
        try:
            emb = extract_embedding(chunk, sr=sr, onnx_session=session)
            norm = float(np.linalg.norm(emb))
            if norm > 1e-10:
                emb = (emb / norm).astype(np.float32)
            matched, confidence = identify_speaker(
                emb,
                threshold=0.65,
                allowed_names=allowed_names,
            )
            emb_list = [float(x) for x in emb]
        except Exception as e:
            print(f"Sortformer: embed/match failed for {label}: {e}", file=sys.stderr)
            info[label] = {"name": label, "source": "generic", "confidence": None, "embedding": None}
            continue
        if matched:
            info[label] = {"name": matched, "source": "auto",
                           "confidence": float(confidence), "embedding": emb_list}
            print(f"  Auto-matched {label} → {matched} ({confidence:.0%})", file=sys.stderr)
        else:
            info[label] = {"name": label, "source": "generic",
                           "confidence": None, "embedding": emb_list}
    return info


def _merge_labels_to_count(
    turns: list[tuple[float, float, str]],
    label_embeddings: dict,
    count: int,
) -> list[tuple[float, float, str]]:
    """Merge global labels down to `count` by repeatedly combining the two
    most similar clusters (single-linkage max cosine over member embeddings).

    Only ever used when the user explicitly requests a speaker count: on
    same-channel calls different people can sit at 0.94 cosine while
    fragments of one person sit at 0.97+, so no fixed threshold is safe —
    the human-supplied count is the guardrail that makes aggressive merging
    correct here. Labels without embeddings are never force-merged; if too
    many remain, the merge stops short rather than guessing.
    """
    labels: list[str] = []
    for _, _, lab in turns:
        if lab not in labels:
            labels.append(lab)
    if count < 1 or len(labels) <= count:
        return turns

    clusters: list[list[str]] = [[lab] for lab in labels]
    while len(clusters) > count:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                sims = [
                    _cosine(label_embeddings[a], label_embeddings[b])
                    for a in clusters[i]
                    for b in clusters[j]
                    if label_embeddings.get(a) is not None
                    and label_embeddings.get(b) is not None
                ]
                if not sims:
                    continue
                sim = max(sims)
                if best is None or sim > best[0]:
                    best = (sim, i, j)
        if best is None:
            break
        _, i, j = best
        clusters[i].extend(clusters[j])
        del clusters[j]

    mapping = {member: cluster[0] for cluster in clusters for member in cluster}
    return [(s, e, mapping[lab]) for s, e, lab in turns]


def _two_voice_partition(
    embeddings: list[np.ndarray],
    *,
    min_per_group: int,
    min_separation: float,
) -> tuple[list[int], float] | None:
    """Split `embeddings` into two voice groups, or None if they are one voice.

    Farthest-pair-seeded two-means, the same deterministic core
    `_mixed_turn_graph_partition` uses on sub-turn chunks. Here the nodes are
    whole turns, so the temporal-alternation rule does not apply — a person's
    turns need not interleave with anyone else's. Returns the group per input
    plus the separation (mean within-group minus mean cross-group similarity),
    which lets a caller compare split candidates and pick the most convincing.
    """
    if len(embeddings) < min_per_group * 2:
        return None
    vectors = np.asarray(embeddings, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms <= 1e-10):
        return None
    vectors = vectors / norms
    affinity = vectors @ vectors.T
    masked = affinity + np.eye(len(vectors), dtype=np.float32) * 2.0
    first, second = np.unravel_index(np.argmin(masked), masked.shape)
    if first == second:
        return None
    labels = np.zeros(len(vectors), dtype=np.int8)
    labels[second] = 1
    for _ in range(12):
        centroids = []
        for group in (0, 1):
            members = vectors[labels == group]
            if not len(members):
                return None
            center = members.mean(axis=0)
            length = np.linalg.norm(center)
            if length <= 1e-10:
                return None
            centroids.append(center / length)
        scores = vectors @ np.stack(centroids).T
        next_labels = np.argmax(scores, axis=1).astype(np.int8)
        next_labels[first] = 0
        next_labels[second] = 1
        if np.array_equal(next_labels, labels):
            break
        labels = next_labels
    if np.any(np.bincount(labels, minlength=2) < min_per_group):
        return None
    same, cross = [], []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            (same if labels[i] == labels[j] else cross).append(float(affinity[i, j]))
    if not same or not cross:
        return None
    separation = float(np.mean(same) - np.mean(cross))
    if separation < min_separation:
        return None
    return labels.tolist(), separation


def _split_labels_to_count(
    audio: np.ndarray,
    turns: list[tuple[float, float, str]],
    count: int,
) -> tuple[list[tuple[float, float, str]], int]:
    """Split global labels up to `count` when the user asked for more speakers
    than stitching produced.

    Each round scores every existing label by how convincingly its own turns
    divide into two voices, then splits the single best one. Working at turn
    granularity means we never invent a boundary inside a turn — genuinely
    blended turns are already handled by `_repair_mixed_turns_with_graph`
    upstream. Returns the turns (unchanged if nothing could be split) and the
    number of splits performed.
    """
    def ordered_labels(rows):
        seen = []
        for _, _, lab in rows:
            if lab not in seen:
                seen.append(lab)
        return seen

    labels = ordered_labels(turns)
    if count < 2 or len(labels) >= count:
        return turns, 0

    linker = _CrossWindowLinker(audio)
    session = linker._session_or_none()
    if session is None:
        return turns, 0
    try:
        from shared.audio_utils import extract_embedding
    except Exception:
        return turns, 0

    cache: dict[int, np.ndarray | None] = {}

    def turn_embedding(index: int):
        if index not in cache:
            start, end, _ = turns[index]
            if end - start < _SPLIT_MIN_TURN_SECONDS:
                cache[index] = None
            else:
                sample = audio[int(start * 16000):int(end * 16000)]
                try:
                    emb = extract_embedding(sample, sr=16000, onnx_session=session)
                except Exception:
                    emb = None
                if emb is not None and np.linalg.norm(emb) <= 1e-10:
                    emb = None
                cache[index] = emb
        return cache[index]

    splits = 0
    while len(labels) < count:
        best = None  # (separation, label, {turn index: group})
        for label in labels:
            indices = [
                i for i, (_, _, lab) in enumerate(turns)
                if lab == label and turn_embedding(i) is not None
            ]
            if len(indices) < _SPLIT_MIN_TURNS_PER_VOICE * 2:
                continue
            partition = _two_voice_partition(
                [turn_embedding(i) for i in indices],
                min_per_group=_SPLIT_MIN_TURNS_PER_VOICE,
                min_separation=_SPLIT_MIN_SEPARATION,
            )
            if partition is None:
                continue
            groups, separation = partition
            if best is None or separation > best[0]:
                best = (separation, label, dict(zip(indices, groups)))
        if best is None:
            print(
                f"Sortformer: no voice evidence to reach {count} speakers; "
                f"keeping {len(labels)}",
                file=sys.stderr,
            )
            break
        separation, label, assignment = best
        # The first group keeps the original label so any name already resolved
        # for it stays attached; only the split-off voice gets a new label.
        new_label = f"{label}__voice_split_{splits + 1}"
        primary = assignment[min(assignment)]
        turns = [
            (s, e, new_label)
            if i in assignment and assignment[i] != primary
            else (s, e, lab)
            for i, (s, e, lab) in enumerate(turns)
        ]
        labels = ordered_labels(turns)
        splits += 1
        print(
            f"Sortformer: split {label} into two voices at requested speaker "
            f"count {count} (separation {separation:.3f})",
            file=sys.stderr,
        )
    return turns, splits


def _speaker_affinity_graph(
    labels: list[str], label_embeddings: dict,
    neighbours: int = 3,
) -> dict[tuple[str, str], float]:
    """Build a sparse voice-affinity graph between provisional labels.

    Long recordings create the same person under new Sortformer labels in
    different windows.  A complete similarity matrix is dominated by weak,
    unhelpful cross-person links, so retain only each label's strongest local
    voice neighbours.  This graph is used to score candidate community counts,
    never to name a person or override an explicit user count.
    """
    edges: dict[tuple[str, str], float] = {}
    for label in labels:
        emb = label_embeddings.get(label)
        if emb is None:
            continue
        ranked = sorted(
            (
                (_cosine(emb, other_emb), other)
                for other, other_emb in label_embeddings.items()
                if other != label and other in labels and other_emb is not None
            ),
            reverse=True,
        )[:neighbours]
        for similarity, other in ranked:
            # Below this, an edge carries no more evidence than background
            # similarity between unrelated speakers.
            if similarity < 0.50:
                continue
            key = tuple(sorted((label, other)))
            edges[key] = max(edges.get(key, 0.0), similarity)
    return edges


def _graph_partition_score(
    labels: list[str], mapping: dict[str, str], edges: dict[tuple[str, str], float]
) -> float:
    """Weighted modularity of a provisional speaker-community partition."""
    if not edges:
        return -1.0
    degrees = {label: 0.0 for label in labels}
    for (left, right), weight in edges.items():
        degrees[left] += weight
        degrees[right] += weight
    total_weight = sum(edges.values())
    if total_weight <= 0:
        return -1.0
    communities: dict[str, list[str]] = {}
    for label in labels:
        communities.setdefault(mapping.get(label, label), []).append(label)
    score = 0.0
    for members in communities.values():
        member_set = set(members)
        internal = sum(
            weight for (left, right), weight in edges.items()
            if left in member_set and right in member_set
        )
        degree_sum = sum(degrees[label] for label in members)
        score += internal / total_weight - (degree_sum / (2.0 * total_weight)) ** 2
    return score


def _auto_merge_labels_by_graph(
    turns: list[tuple[float, float, str]],
    label_embeddings: dict,
    max_speakers: int = 8,
) -> tuple[list[tuple[float, float, str]], int | None, float | None]:
    """Choose a global speaker count from a sparse voice-affinity graph.

    This is deliberately a *count-selection* pass over the already-created
    Sortformer labels, not repeated diarization. For each feasible community
    count we apply the existing conservative voice merge and score its partition
    by graph modularity. Near-ties prefer fewer communities, because the known
    failure mode is window-label fragmentation rather than under-splitting.
    """
    labels = list(dict.fromkeys(label for _, _, label in turns))
    usable = [label for label in labels if label_embeddings.get(label) is not None]
    if len(usable) < 3 or len(labels) <= 2:
        return turns, None, None
    edges = _speaker_affinity_graph(labels, label_embeddings)
    if not edges:
        return turns, None, None

    upper = min(max_speakers, len(usable))
    candidates: list[tuple[float, int, list[tuple[float, float, str]]]] = []
    for count in range(2, upper + 1):
        merged = _merge_labels_to_count(turns, label_embeddings, count)
        mapping = {}
        for (_, _, original), (_, _, resolved) in zip(turns, merged):
            mapping[original] = resolved
        actual = len({label for _, _, label in merged})
        # A small penalty breaks modularity's tendency to preserve singleton
        # communities in sparse graphs. It is intentionally weak: clear voice
        # evidence still wins over a lower count.
        score = _graph_partition_score(labels, mapping, edges) - 0.015 * actual
        candidates.append((score, actual, merged))

    best_score = max(score for score, _, _ in candidates)
    # Counts within 0.025 modularity are indistinguishable at this signal level;
    # choose the smaller, safer result instead of exposing window fragments.
    near_best = [item for item in candidates if item[0] >= best_score - 0.025]
    _, chosen_count, chosen_turns = min(near_best, key=lambda item: item[1])
    return chosen_turns, chosen_count, best_score


def _expected_speakers_from_calendar(calendar_context) -> int | None:
    """Expected speaker count from the recording's calendar event, if any.

    Non-declined attendees of the selected event. Used as a soft cap for the
    post-hoc count merge: over-splitting is the common diarization failure,
    so when more labels than attendees are found, merging down to the
    attendee count is right far more often than wrong. Never applied for
    ambiguous events or sub-2-person counts (a guest may be uninvited).
    """
    if calendar_context is None or getattr(calendar_context, "ambiguous", False):
        return None
    event_id = getattr(calendar_context, "selected_event_id", None)
    if not event_id:
        return None
    for event in getattr(calendar_context, "events", None) or ():
        if getattr(event, "id", None) == event_id:
            count = sum(
                1 for attendee in getattr(event, "attendees", ())
                if not getattr(attendee, "declined", False)
            )
            return count if count >= 2 else None
    return None


def _prune_empty_speakers(
    speaker_names: dict,
    speaker_meta: dict,
    speaker_embeddings: dict,
    segments: list[dict],
) -> tuple[dict, dict, dict]:
    """Drop speaker entries that own no surviving segments.

    A label can end up segment-less when all of its text was filtered out
    (empty segments, non-speech anonymisation); keeping it in
    `speaker_names`/`speaker_meta` shows a phantom extra person in the app.
    Ids are left as-is (sparse str keys are fine downstream) — nothing is
    renumbered, so stored embeddings keep matching their sidecar ids.
    """
    used = {str(seg.get("speaker_id")) for seg in segments}
    return (
        {k: v for k, v in speaker_names.items() if k in used},
        {k: v for k, v in speaker_meta.items() if k in used},
        {k: v for k, v in speaker_embeddings.items() if k in used},
    )


def _absorb_micro_labels(
    turns: list[tuple[float, float, str]],
    label_embeddings: dict,
    talk_seconds: dict[str, float],
    *,
    max_seconds: float = _MICRO_LABEL_MAX_SECONDS,
    threshold: float = _ABSORB_SIMILARITY_THRESHOLD,
) -> list[tuple[float, float, str]]:
    """Reassign micro labels (total speech < `max_seconds`) to their closest
    full-size voice when the embedding similarity clears `threshold`.

    Diarizer tail-fragments — a few seconds of goodbye smalltalk or a
    misheard interjection — are not real participants, but their short audio
    makes embeddings too noisy for the margin rule used in cross-window
    linking. Absorption uses a plain threshold against full-size labels
    only; a fragment that matches nothing keeps its own label, and anything
    without an embedding is left untouched.
    """
    micro = {lab for lab, secs in talk_seconds.items() if secs < max_seconds}
    full = [lab for lab in talk_seconds if lab not in micro]
    if not micro or not full:
        return turns
    out: list[tuple[float, float, str]] = []
    for s, e, lab in turns:
        if lab not in micro:
            out.append((s, e, lab))
            continue
        emb = label_embeddings.get(lab)
        best_label = None
        best_sim = threshold
        for cand in full:
            cand_emb = label_embeddings.get(cand)
            if emb is None or cand_emb is None:
                continue
            sim = _cosine(emb, cand_emb)
            if sim >= best_sim:
                best_label, best_sim = cand, sim
        out.append((s, e, best_label if best_label is not None else lab))
    return out


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
    calendar_context=None,
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
        # Cross-window voice linking: overlap-only stitching re-mints a new
        # "speaker" whenever someone is silent through a handover region.
        # The linker degrades to overlap-only if TitaNet is unavailable.
        linker = _CrossWindowLinker(audio)
        all_turns = _stitch_windows(windows, overlap_sec=_OVERLAP_SEC, linker=linker)

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
            "speaker_meta": {"0": {"source": "generic", "confidence": None, "verified": False}},
            "speaker_embeddings": {},
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

    # Repair within-window under-splits before labels are normalised and before
    # any count merge.  A graph can merge duplicate labels later, but it cannot
    # recover two people hidden inside one label unless we create local voice
    # nodes at this point.
    all_turns, mixed_turn_repairs = _repair_mixed_turns_with_graph(audio, all_turns)

    # An explicit count is a two-way instruction, not just a cap. Splitting has
    # to happen here — before labels are normalised and before names are
    # resolved — so a split-off voice gets its own "Speaker N" and its own
    # voice-library lookup. Only an explicit user count splits upward: the
    # calendar attendee count stays a downward-only soft cap, because invitees
    # who never speak would otherwise manufacture empty participants.
    voice_splits = 0
    if n_speakers is not None:
        all_turns, voice_splits = _split_labels_to_count(audio, all_turns, n_speakers)

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
    speaker_info = _resolve_speaker_names(
        audio,
        renamed_turns,
        internal_labels,
        sr=16000,
        allowed_names=getattr(calendar_context, "candidate_names", None),
    )
    label_embs = {
        label: (speaker_info.get(label) or {}).get("embedding")
        for label in internal_labels
    }

    # Honour an explicitly requested speaker count post-hoc: Sortformer's
    # inference API is fixed-topology, but the stitched global labels can be
    # merged down by voice similarity. Without an explicit count, fall back
    # to the calendar event's non-declined attendee count — over-splitting
    # is the common failure, so the attendee count is a safe soft cap.
    # Either way the count is external evidence, which is what makes this
    # aggressive merging safe on same-channel calls.
    effective_n_speakers = n_speakers
    count_strategy = "manual" if effective_n_speakers is not None else "unconstrained"
    if mixed_turn_repairs:
        count_strategy += "+mixed-turn-graph-repair"
    if voice_splits:
        count_strategy += "+voice-split-up"
    if effective_n_speakers is None:
        effective_n_speakers = _expected_speakers_from_calendar(calendar_context)
        if effective_n_speakers is not None:
            count_strategy = "calendar"
            print(
                f"Sortformer: calendar expects {effective_n_speakers} attendees",
                file=sys.stderr,
            )
    if effective_n_speakers is not None and len(internal_labels) > effective_n_speakers:
        before = len(internal_labels)
        renamed_turns = _merge_labels_to_count(renamed_turns, label_embs, effective_n_speakers)
        surviving = {lab for _, _, lab in renamed_turns}
        internal_labels = [lab for lab in internal_labels if lab in surviving]
        print(
            f"Sortformer: merged {before} labels down to {len(internal_labels)} "
            f"at speaker count {effective_n_speakers}",
            file=sys.stderr,
        )
    elif len(internal_labels) > 2:
        graph_turns, graph_count, graph_score = _auto_merge_labels_by_graph(
            renamed_turns, label_embs,
        )
        if (
            graph_count is not None
            and graph_score is not None
            and graph_score >= _GRAPH_AUTOMERGE_MIN_SCORE
            and graph_count < len(internal_labels)
        ):
            before = len(internal_labels)
            renamed_turns = graph_turns
            surviving = {lab for _, _, lab in renamed_turns}
            internal_labels = [lab for lab in internal_labels if lab in surviving]
            count_strategy = "voice-affinity-graph"
            print(
                f"Sortformer: voice-affinity graph merged {before} labels down to "
                f"{len(internal_labels)} communities (score {graph_score:.3f})",
                file=sys.stderr,
            )
        elif graph_count is not None:
            count_strategy = "voice-affinity-graph-inconclusive"
            print(
                f"Sortformer: voice-affinity graph suggests {graph_count} communities "
                f"but score {graph_score:.3f} is inconclusive; keeping labels for a "
                "calendar hint or user-selected count",
                file=sys.stderr,
            )

    # Absorb micro tail-fragments (a few seconds of speech) into their
    # closest full-size voice — a fragment is not a real participant, and
    # the margin rule used for cross-window linking is too strict for its
    # noisy short-clip embedding.
    talk_seconds: dict[str, float] = {}
    for ts, te, lab in renamed_turns:
        talk_seconds[lab] = talk_seconds.get(lab, 0.0) + (te - ts)
    print(
        "Sortformer: talk per label: "
        + ", ".join(f"{lab}={secs:.1f}s" for lab, secs in sorted(talk_seconds.items())),
        file=sys.stderr,
    )
    absorbed_turns = _absorb_micro_labels(renamed_turns, label_embs, talk_seconds)
    # Absorption exists to delete tail-fragments, not to overrule the person who
    # typed the count. A quiet-but-real participant can easily hold under the
    # micro threshold, and letting absorption run would hand back the very
    # "asked for 4, got 3, no changes" result this count is meant to fix.
    absorbed_count = len({lab for _, _, lab in absorbed_turns})
    if n_speakers is not None and absorbed_count < n_speakers <= len(talk_seconds):
        print(
            f"Sortformer: skipped micro-label absorption — it would drop to "
            f"{absorbed_count} speakers below the requested {n_speakers}",
            file=sys.stderr,
        )
        absorbed_turns = renamed_turns
    for lab in sorted(set(talk_seconds) - {lab for _, _, lab in absorbed_turns}):
        print(
            f"Sortformer: absorbed micro label {lab} ({talk_seconds[lab]:.1f}s) into a fuller voice",
            file=sys.stderr,
        )
    renamed_turns = absorbed_turns
    surviving = {lab for _, _, lab in renamed_turns}
    internal_labels = [lab for lab in internal_labels if lab in surviving]
    display_names = {label: speaker_info[label]["name"] for label in internal_labels}

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
    # Provenance + review state per speaker (see PLAN-speaker-tagging-loop.md).
    # Auto-matched names start unverified so the app can flag them for the user
    # to confirm; generic "Speaker N" labels are untouched.
    speaker_meta: dict[str, dict] = {}
    speaker_embeddings: dict[str, list] = {}
    for label, spk_id in label_to_id.items():
        inf = speaker_info.get(label, {})
        speaker_meta[str(spk_id)] = {
            "source": inf.get("source", "generic"),
            "confidence": inf.get("confidence"),
            "verified": False,
        }
        if inf.get("embedding") is not None:
            speaker_embeddings[str(spk_id)] = inf["embedding"]

    # Don't let two speakers auto-match the same enrolled voice (over-clustering
    # splitting one person). Keep the best; revert the rest to generic.
    try:
        from shared.speaker_meta import resolve_name_collisions
        resolve_name_collisions(speaker_names, speaker_meta)
    except Exception as e:  # noqa: BLE001 - best effort, never break diarization
        print(f"Sortformer: name-collision resolve skipped ({e})", file=sys.stderr)

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
            previous_words = timed_words(segments_out[-1])
            current_words = timed_words(seg)
            if previous_words and current_words:
                segments_out[-1]["words"] = previous_words + current_words
                segments_out[-1]["text"] = words_to_text(segments_out[-1]["words"])
            else:
                segments_out[-1]["text"] = (segments_out[-1]["text"] + " " + seg["text"]).strip()
        else:
            segments_out.append(dict(seg))

    # Cap monster blocks. Two passes — the first split can still leave
    # chunks slightly over `max_duration` near sentence boundaries.
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)

    # Labels whose segments all got filtered out would show as phantom
    # people in the app — drop them from the speaker maps.
    speaker_names, speaker_meta, speaker_embeddings = _prune_empty_speakers(
        speaker_names, speaker_meta, speaker_embeddings, segments_out
    )

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
        "speaker_meta": speaker_meta,
        "speaker_embeddings": speaker_embeddings,
        "backend": "sortformer",
        "speaker_count_strategy": count_strategy,
    }
