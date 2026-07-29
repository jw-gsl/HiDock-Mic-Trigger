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

import json
import re
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

# Turn reassignment. A turn's speaker is otherwise decided once, by overlap with
# Sortformer's output, and never revisited against the voice evidence that
# accumulates as clusters take shape. This is a bounded Lloyd/EM refinement:
# reassign turns to the closest pooled cluster voice, re-pool, repeat.
# Hysteresis is what makes it safe — a turn moves only when it beats its current
# cluster by a clear margin, so the loop cannot oscillate between two near-ties.
_REASSIGN_MAX_ITERATIONS = 5
_REASSIGN_MIN_MARGIN = 0.04
_REASSIGN_MIN_TURN_SECONDS = 1.5

# Two-sided partition search. Modularity gains below this are noise, not
# structure. A newly split voice must also hold this much speech: the measured
# failure is under-counting, but the cure must not be a pipeline that invents
# participants out of a few scraps of audio.
_PARTITION_MIN_GAIN = 0.01
_SPLIT_MIN_NEW_VOICE_SECONDS = 20.0

# Anchor-seeded extraction. A human-confirmed segment says where a voice *is*,
# which is the one thing two-means splitting cannot work out for a participant
# who barely speaks. Calibrated against the Rec79 Part 2 measurements: the
# confirmed speaker self-matched at 0.888 while sitting at most 0.335 from anyone
# else, so a 0.55 floor is comfortably clear of the between-person range on this
# data without being so loose that a neighbouring voice gets claimed.
_ANCHOR_EXTRACT_MIN_SIMILARITY = 0.55
_ANCHOR_EXTRACT_MIN_MARGIN = 0.10

# Both refinements change every diarisation, so their defaults come from what
# `shared.diarisation_eval` measures on the reviewed corpus, not from how
# convincing the reasoning sounds.
#
# Two-sided partition: OFF, reverted 2026-07-29 on evidence.
#
# It was enabled on 16 meetings x 420 s, which showed exact counts 37.5% -> 68.8%
# and confusion 19.0% -> 8.9%. A 60-meeting, FULL-LENGTH run contradicted that:
#
#     exact        28.3% -> 33.3%   (+5 pts, not +31)
#     count MAE    1.683 -> 1.483
#     count bias  -0.483 -> +1.150  (over-counting, and badly)
#     name recall  35.4% -> 7.8%    (collapsed)
#
# Counts got *more* wrong in 28/60 meetings and name recall fell in 39/60. The
# splitting runs away on long audio: a 3-speaker meeting was given 17 speakers,
# a 4-speaker one 10. The 420 s window had masked it — a short window physically
# limits how many speakers can appear.
#
# Confusion still improved (16.1% -> 7.0%), which is why this looked good: with a
# one-to-one mapping, extra clusters can make each mapped cluster purer while the
# transcript as a whole becomes unusable. Confusion alone was the wrong headline;
# count error and name recall are what the user actually sees.
_TWO_SIDED_PARTITION_DEFAULT = False
# Turn reassignment: OFF, reverted with the above. Suspected as the main driver
# of the over-counting: it lets a label that owned no segments acquire some, so it
# rescues clusters that pruning would otherwise have removed. Measured together,
# so which of the two is responsible is not yet separated — that is the next
# experiment, not an assumption to ship.
_REFINE_ASSIGNMENTS_DEFAULT = False


_GENERIC_LABEL = re.compile(r"Speaker \d+")


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


def _naming_backend():
    """(session, match_fn, label) for automatic speaker naming, or None.

    Naming used TitaNet against the main library unconditionally. On this user's
    data that space is saturated — every enrolled voice sits at 0.97–0.99 cosine
    to any speaker — so `identify_speaker`'s margin rule almost never fires and
    measured name recall was 2%. When a stronger candidate model is active *and*
    has been promoted out of review-only, name from that library instead, using
    the threshold, margin, and scorer it was calibrated with.

    `match_fn(embedding) -> (name | None, confidence)`.
    """
    try:
        from shared.voice_candidate_review import _rank_library, load_candidate_config
        from shared.voice_library_lite import _get_speaker_embed_session

        config = load_candidate_config()
        if (
            config.get("available")
            and not config.get("review_only", True)
            and config.get("model_path")
            and config.get("library_path")
        ):
            from pathlib import Path as _Path
            session = _get_speaker_embed_session(
                str(config.get("model_key")), _Path(config["model_path"])
            )
            if session is not None:
                library = json.loads(
                    _Path(config["library_path"]).read_text(encoding="utf-8")
                )
                threshold = float(config.get("threshold", 0.5))
                margin = float(config.get("min_margin", 0.23))
                scorer = str(config.get("scorer", "top3_median"))

                def match(embedding, allowed_names=None):
                    ranked = _rank_library(library, embedding, scorer)
                    if allowed_names:
                        allowed = {str(n) for n in allowed_names}
                        ranked = [r for r in ranked if r["name"] in allowed] or ranked
                    if not ranked:
                        return None, 0.0
                    best = ranked[0]
                    runner_up = ranked[1]["score"] if len(ranked) > 1 else -1.0
                    if best["score"] >= threshold and best["score"] - runner_up >= margin:
                        return best["name"], float(best["score"])
                    return None, 0.0

                return session, match, str(config.get("model_key"))
    except Exception as exc:  # noqa: BLE001 - never block diarisation on naming
        print(f"Sortformer: candidate naming unavailable ({exc})", file=sys.stderr)

    try:
        import onnxruntime as ort

        from shared.models import ensure_speaker_embed
        from shared.voice_library_lite import identify_speaker

        session = ort.InferenceSession(
            str(ensure_speaker_embed()), providers=["CPUExecutionProvider"]
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Sortformer: TitaNet load failed ({exc}); using generic labels", file=sys.stderr)
        return None

    def match(embedding, allowed_names=None):
        return identify_speaker(embedding, threshold=0.65, allowed_names=allowed_names)

    return session, match, "titanet"


def _resolve_speaker_names(
    audio: np.ndarray,
    turns,
    internal_labels: list[str],
    sr: int = 16000,
    allowed_names=None,
) -> tuple[dict[str, dict], str]:
    """Try to match each Sortformer speaker against the voice library.

    Returns `(info, model_key)`. The model key travels with the result because
    the embeddings it produced are persisted, and two of the available models are
    both 192-dim — a consumer that compares across their cosine spaces gets
    confident-looking nonsense rather than a dimension error.

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
    except Exception as e:
        print(f"Sortformer: voice library hooks unavailable ({e}); using generic labels", file=sys.stderr)
        return fallback, "unknown"

    backend = _naming_backend()
    if backend is None:
        return fallback, "unknown"
    session, identify_speaker, naming_model = backend
    print(f"Sortformer: naming speakers via {naming_model}", file=sys.stderr)

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
            matched, confidence = identify_speaker(emb, allowed_names=allowed_names)
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
    return info, naming_model


def _label_speech_seconds(turns: list[tuple[float, float, str]]) -> dict[str, float]:
    """Total speaking time per label."""
    speech: dict[str, float] = {}
    for start, end, label in turns:
        speech[label] = speech.get(label, 0.0) + max(0.0, end - start)
    return speech


def _pooled_embedding(members: list[str], speaker_info: dict, speech: dict) -> list | None:
    """Duration-weighted, L2-normalised mean of members' embeddings.

    A merged cluster's voice is better represented by all of its audio than by
    whichever member happened to survive. Weighting by speaking time keeps a
    noisy few-second fragment from dragging the centroid off a well-evidenced
    voice.
    """
    vectors, weights = [], []
    for member in members:
        embedding = (speaker_info.get(member) or {}).get("embedding")
        if embedding is None:
            continue
        vectors.append(np.asarray(embedding, dtype=np.float32))
        weights.append(max(speech.get(member, 0.0), 1e-6))
    if not vectors:
        return None
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        return None
    stacked = np.asarray(vectors, dtype=np.float32)
    pooled = np.average(stacked, axis=0, weights=np.asarray(weights, dtype=np.float32))
    norm = float(np.linalg.norm(pooled))
    if norm <= 1e-10:
        return None
    return [float(value) for value in (pooled / norm)]


def _label_groups(
    before_turns: list[tuple[float, float, str]],
    after_turns: list[tuple[float, float, str]],
) -> dict[str, list[str]]:
    """Which original labels ended up under each surviving label.

    Relies on the reconciliation passes preserving turn order and count, which
    they all do — they rewrite a label per turn and never add or drop turns.
    """
    groups: dict[str, list[str]] = {}
    for (_, _, before), (_, _, after) in zip(before_turns, after_turns):
        members = groups.setdefault(after, [])
        if before not in members:
            members.append(before)
    return groups


def _repool_merged_speakers(
    speaker_info: dict,
    before_turns: list[tuple[float, float, str]],
    after_turns: list[tuple[float, float, str]],
    surviving_labels: list[str],
    *,
    allowed_names=None,
    also_resolve: set[str] | None = None,
) -> dict:
    """Re-derive identity for any label that absorbed others.

    Names, confidences, and embeddings are resolved once *before* the count
    reconciliation, per stitched label. Every merge therefore inherited the
    surviving member's identity and threw the rest away — including a confident
    voice-library match on the member with most of the speech, and the pooled
    audio evidence that would have made the persisted embedding trustworthy.
    (That embedding matters beyond display: `rematch` re-identifies from it
    without touching audio, so a fragment's vector poisons every later pass.)

    For each merged cluster this pools the members' embeddings by speaking time
    and re-runs the library match on the pooled vector. If the library is
    unavailable the pooled embedding is still stored, and identity falls back to
    the best-evidenced member rather than the surviving label's own.
    """
    groups = _label_groups(before_turns, after_turns)
    speech = _label_speech_seconds(before_turns)
    # A label that absorbed others needs re-deriving; so does one the partition
    # search invented, which has a pooled voice but no name yet.
    extra = also_resolve or set()
    merged = {
        label: members
        for label, members in groups.items()
        if label in surviving_labels and (len(members) > 1 or label in extra)
    }
    if not merged:
        return speaker_info

    identify = scores_for = None
    try:
        from shared.voice_library_lite import identify_speaker as identify
        from shared.voice_library_lite import library_scores as scores_for
    except Exception as exc:  # noqa: BLE001 - pooling still helps without it
        print(f"Sortformer: pooled re-match unavailable ({exc})", file=sys.stderr)

    updated = dict(speaker_info)
    for label, members in merged.items():
        best_member = max(members, key=lambda m: speech.get(m, 0.0))
        own = speaker_info.get(label) or {}
        if label in extra:
            # A split-off voice must not inherit the name of the cluster it was
            # separated from — that name belongs to the other half. Use the
            # pooled voice the partition search computed for this group and let
            # the library speak for itself.
            pooled = own.get("embedding") or _pooled_embedding(members, speaker_info, speech)
            info = dict(own)
        else:
            pooled = _pooled_embedding(members, speaker_info, speech)
            # Start from the best-evidenced member: already better than inheriting
            # whichever label survived, and the only option without a library.
            info = dict(speaker_info.get(best_member) or own or {})
        info.setdefault("name", label)
        info.setdefault("source", "generic")
        info.setdefault("confidence", None)
        if pooled is not None:
            info["embedding"] = pooled
        if pooled is not None and identify is not None:
            try:
                matched, confidence = identify(
                    pooled, threshold=0.65, allowed_names=allowed_names,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"Sortformer: pooled re-match failed for {label}: {exc}", file=sys.stderr)
            else:
                if matched:
                    info.update(
                        name=matched, source="auto", confidence=float(confidence),
                    )
                    print(
                        f"  Pooled re-match {label} ({len(members)} labels, "
                        f"{speech.get(best_member, 0.0):.0f}s best) → {matched} "
                        f"({confidence:.0%})",
                        file=sys.stderr,
                    )
                elif info.get("source") == "auto":
                    # No match. Only treat that as evidence *against* the name if
                    # the library actually had candidates to compare: it returns
                    # zero scores when it is empty or the embedding dimension
                    # does not match its model, and demoting on that would throw
                    # away a good match for an unrelated reason.
                    comparable = False
                    try:
                        comparable = bool(scores_for(pooled, allowed_names=allowed_names))
                    except Exception:  # noqa: BLE001 - treat as "cannot tell"
                        comparable = False
                    if comparable:
                        # Forcing two people together is the likely cause, so
                        # demote for review rather than asserting a name.
                        print(
                            f"  Pooled voice for {label} no longer matches "
                            f"'{info.get('name')}'; demoting to review",
                            file=sys.stderr,
                        )
                        info.update(name=label, source="generic", confidence=None)
        # A generic identity must carry the surviving label's own name, not the
        # absorbed member's, or the transcript shows a speaker that no longer exists.
        if info.get("source") == "generic":
            info["name"] = label
        updated[label] = info
    return updated


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

    The surviving label of each cluster is its **longest-speaking** member, not
    the earliest one. Identity travels with the surviving label downstream, so
    picking by first appearance let a four-second fragment absorb a
    six-minute speaker and donate its name, confidence, and persisted
    embedding to the result. Ties keep the earliest member, so the choice stays
    deterministic.
    """
    labels: list[str] = []
    for _, _, lab in turns:
        if lab not in labels:
            labels.append(lab)
    if count < 1 or len(labels) <= count:
        return turns
    speech = _label_speech_seconds(turns)

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

    mapping = {}
    for cluster in clusters:
        survivor = max(cluster, key=lambda lab: (speech.get(lab, 0.0), -labels.index(lab)))
        for member in cluster:
            mapping[member] = survivor
    return [(s, e, mapping[lab]) for s, e, lab in turns]


def _extract_anchored_voice(
    audio: np.ndarray,
    turns: list[tuple[float, float, str]],
    anchors: list[tuple[float, float, str]],
    *,
    min_similarity: float = _ANCHOR_EXTRACT_MIN_SIMILARITY,
    min_margin: float = _ANCHOR_EXTRACT_MIN_MARGIN,
) -> tuple[list[tuple[float, float, str]], dict[str, int]]:
    """Pull a confirmed speaker's turns out of whatever cluster swallowed them.

    Two-means splitting cannot recover a quiet participant. It looks *within* a
    label for two balanced voices, so 128 s of one person hidden inside a label
    holding ~1,000 s of another never separates — the clusters are far too
    lopsided. Measured on Rec79 Part 2: Jeevan Dulai spoke 3.8% of a 56-minute
    meeting and no amount of splitting or threshold tuning found him, even though
    he is the *most* acoustically distinct voice present (≤0.335 cosine to anyone
    else, where the two speakers that did separate sit at 0.449).

    A human-confirmed segment removes the discovery problem: it says exactly
    where the voice is. This builds a centroid from those segments and claims any
    turn that matches it clearly — a plain similarity floor plus a margin over the
    turn's current cluster, so a turn only moves on positive evidence for the
    anchored speaker rather than mere absence of evidence for its current one.

    Anchored turns themselves are never reassigned; they are ground truth.
    Returns the turns and a per-anchor-label count of how many were claimed.
    """
    if not anchors:
        return turns, {}
    embedding_for = _turn_embedder(audio, turns, min_seconds=_SPLIT_MIN_TURN_SECONDS)
    if embedding_for is None:
        return turns, {}

    # Anchor centroids, duration-weighted so a long confirmed stretch counts more.
    centroids: dict[str, np.ndarray] = {}
    for name in sorted({label for _, _, label in anchors}):
        spans = [
            (start, end) for start, end, label in anchors
            if label == name and end - start >= _SPLIT_MIN_TURN_SECONDS
        ]
        if not spans:
            continue
        pooled = _pooled_span_embedding(
            audio, spans, [end - start for start, end in spans],
        )
        if pooled is not None:
            centroids[name] = pooled
    if not centroids:
        return turns, {}

    anchored_indices = {
        index for index, (start, end, _) in enumerate(turns)
        if any(min(end, a_end) - max(start, a_start) > 0 for a_start, a_end, _ in anchors)
    }

    claimed: dict[str, int] = {}
    out = list(turns)
    for index, (start, end, label) in enumerate(turns):
        if index in anchored_indices:
            continue
        emb = embedding_for(index)
        if emb is None:
            continue
        scored = sorted(
            ((_cosine(emb, centroid), name) for name, centroid in centroids.items()),
            reverse=True,
        )
        best_score, best_name = scored[0]
        if best_score < min_similarity or best_name == label:
            continue
        # Require a clear lead over the cluster the turn currently sits in, so a
        # turn is claimed on evidence *for* the anchored voice.
        own = centroids.get(label)
        if own is not None and best_score - _cosine(emb, own) < min_margin:
            continue
        out[index] = (start, end, best_name)
        claimed[best_name] = claimed.get(best_name, 0) + 1

    for name, count in claimed.items():
        print(
            f"Sortformer: anchor '{name}' claimed {count} turn(s) from other clusters",
            file=sys.stderr,
        )
    return out, claimed


def _pooled_span_embedding(
    audio: np.ndarray,
    spans: list[tuple[float, float]],
    weights: list[float],
) -> np.ndarray | None:
    """Duration-weighted centroid over arbitrary audio spans."""
    from shared.audio_utils import extract_embedding

    linker = _CrossWindowLinker(audio)
    session = linker._session_or_none()
    if session is None:
        return None
    vectors, used = [], []
    for (start, end), weight in zip(spans, weights):
        sample = audio[int(start * 16000):int(end * 16000)]
        try:
            emb = extract_embedding(sample, sr=16000, onnx_session=session)
        except Exception:
            continue
        norm = float(np.linalg.norm(emb))
        if norm <= 1e-10:
            continue
        vectors.append(emb / norm)
        used.append(max(weight, 1e-6))
    if not vectors:
        return None
    pooled = np.average(
        np.asarray(vectors, dtype=np.float32), axis=0,
        weights=np.asarray(used, dtype=np.float32),
    )
    norm = float(np.linalg.norm(pooled))
    return None if norm <= 1e-10 else pooled / norm


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

    turn_embedding = _turn_embedder(audio, turns, min_seconds=_SPLIT_MIN_TURN_SECONDS)
    if turn_embedding is None:
        return turns, 0

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
            # Both halves must own real speech. Without this, "make it 2 speakers"
            # could satisfy itself with a scrap: Rec82 was given a second speaker
            # holding a single zero-duration segment ("to"), because a calendar
            # attendee count of 2 was treated as proof that two people spoke.
            # An invitee who says nothing is still an invitee.
            seconds = {0: 0.0, 1: 0.0}
            for index, group in zip(indices, groups):
                start, end, _ = turns[index]
                seconds[group] += max(0.0, end - start)
            if min(seconds.values()) < _SPLIT_MIN_NEW_VOICE_SECONDS:
                continue
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


def _turn_embedder(audio: np.ndarray, turns, *, min_seconds: float):
    """Lazy per-turn embedding lookup, or None if no model is available.

    Shared by the split and reassignment passes so a turn's audio is embedded at
    most once per diarisation.
    """
    linker = _CrossWindowLinker(audio)
    session = linker._session_or_none()
    if session is None:
        return None
    try:
        from shared.audio_utils import extract_embedding
    except Exception:
        return None

    cache: dict[int, np.ndarray | None] = {}

    def embedding_for(index: int):
        if index not in cache:
            start, end, _ = turns[index]
            if end - start < min_seconds:
                cache[index] = None
            else:
                sample = audio[int(start * 16000):int(end * 16000)]
                try:
                    emb = extract_embedding(sample, sr=16000, onnx_session=session)
                except Exception:
                    emb = None
                if emb is not None and float(np.linalg.norm(emb)) <= 1e-10:
                    emb = None
                cache[index] = emb
        return cache[index]

    return embedding_for


def _reassign_turns_to_pooled_voices(
    audio: np.ndarray,
    turns: list[tuple[float, float, str]],
    *,
    pinned_intervals: list[tuple[float, float]] | None = None,
    max_iterations: int = _REASSIGN_MAX_ITERATIONS,
    min_margin: float = _REASSIGN_MIN_MARGIN,
) -> tuple[list[tuple[float, float, str]], int]:
    """Move turns to the cluster whose pooled voice they actually match.

    Sortformer assigns a turn once, from its own frame-level output; the voice
    evidence for a *cluster* only exists after stitching and merging, and is
    never fed back. A turn that was borderline at inference time is therefore
    stuck, even when the accumulated evidence says otherwise.

    Each iteration pools every cluster's turn embeddings (duration-weighted) and
    moves a turn only when some other cluster beats its current one by
    `min_margin`. That hysteresis is what guarantees termination in practice as
    well as bounding it by `max_iterations`.

    `pinned_intervals` are stretches a human has already confirmed: any turn
    overlapping one is frozen. Without that, re-diarising a reviewed transcript
    could quietly move confirmed speech to another speaker — the failure that
    prompted transcript version history in the first place.
    """
    labels = list(dict.fromkeys(label for _, _, label in turns))
    if len(labels) < 2:
        return turns, 0
    embedding_for = _turn_embedder(audio, turns, min_seconds=_REASSIGN_MIN_TURN_SECONDS)
    if embedding_for is None:
        return turns, 0

    pinned: set[int] = set()
    for index, (start, end, _) in enumerate(turns):
        for p_start, p_end in pinned_intervals or ():
            if min(end, p_end) - max(start, p_start) > 0:
                pinned.add(index)
                break

    movable = [
        index for index in range(len(turns))
        if index not in pinned and embedding_for(index) is not None
    ]
    if not movable:
        return turns, 0

    current = list(turns)
    total_moves = 0
    for _ in range(max_iterations):
        pooled: dict[str, np.ndarray] = {}
        weights: dict[str, float] = {}
        for index, (start, end, label) in enumerate(current):
            emb = embedding_for(index)
            if emb is None:
                continue
            weight = max(end - start, 1e-6)
            norm = float(np.linalg.norm(emb))
            unit = emb / norm if norm > 1e-10 else emb
            if label in pooled:
                pooled[label] = pooled[label] + unit * weight
            else:
                pooled[label] = unit * weight
            weights[label] = weights.get(label, 0.0) + weight
        centroids = {}
        for label, vector in pooled.items():
            norm = float(np.linalg.norm(vector))
            if norm > 1e-10:
                centroids[label] = vector / norm
        if len(centroids) < 2:
            break

        moves = 0
        for index in movable:
            emb = embedding_for(index)
            start, end, label = current[index]
            scored = sorted(
                ((_cosine(emb, centroid), other) for other, centroid in centroids.items()),
                reverse=True,
            )
            best_score, best_label = scored[0]
            if best_label == label:
                continue
            own = next(
                (score for score, other in scored if other == label), None
            )
            # A cluster the turn has left entirely has no centroid to beat; treat
            # that as a free move rather than skipping it.
            if own is not None and best_score - own < min_margin:
                continue
            current[index] = (start, end, best_label)
            moves += 1
        total_moves += moves
        if moves == 0:
            break

    if total_moves:
        print(
            f"Sortformer: reassigned {total_moves} turn(s) to their closest pooled "
            f"voice ({len(pinned)} pinned by confirmed labels)",
            file=sys.stderr,
        )
    return current, total_moves


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


def _turn_affinity_graph(
    embeddings: dict[str, np.ndarray], neighbours: int = 5,
) -> dict[tuple[str, str], float]:
    """kNN voice-affinity graph over individual turns.

    `_speaker_affinity_graph` builds the same structure over labels, which is
    fine for choosing among merges but cannot compare a *split* — splitting
    changes the node set, and modularity is only meaningful relative to a fixed
    graph. Turn nodes stay put no matter how they are grouped, so one graph
    scores merges and splits on the same footing.
    """
    return _speaker_affinity_graph(list(embeddings), embeddings, neighbours=neighbours)


def _refine_partition_by_turn_graph(
    audio: np.ndarray,
    turns: list[tuple[float, float, str]],
    *,
    max_speakers: int = 8,
    min_gain: float = _PARTITION_MIN_GAIN,
    min_split_seconds: float = _SPLIT_MIN_NEW_VOICE_SECONDS,
) -> tuple[list[tuple[float, float, str]], str | None]:
    """Hill-climb the speaker partition, allowing splits as well as merges.

    Every automatic path in this file could previously only *reduce* the speaker
    count: the count merge and the label-graph pass both merge down from the
    stitched labels, and only a ≥24 s blended turn or an explicit user count
    could ever add a speaker. Measured on 16 reviewed meetings, that showed up as
    a -1.25 speaker bias with just 25% of counts correct.

    Here both directions are candidates, scored by modularity on one fixed
    turn-level graph, and applied only when they beat the current partition by
    `min_gain`. Splitting is held to a higher bar than merging — a new voice must
    also hold `min_split_seconds` of speech and clear the two-means separation
    floor — because inventing a participant is worse than merging two.
    """
    labels = list(dict.fromkeys(label for _, _, label in turns))
    if not labels:
        return turns, None, {}
    embedding_for = _turn_embedder(audio, turns, min_seconds=_SPLIT_MIN_TURN_SECONDS)
    if embedding_for is None:
        return turns, None, {}

    node_embeddings: dict[str, np.ndarray] = {}
    for index in range(len(turns)):
        emb = embedding_for(index)
        if emb is not None:
            node_embeddings[str(index)] = emb
    if len(node_embeddings) < _SPLIT_MIN_TURNS_PER_VOICE * 2:
        return turns, None, {}
    edges = _turn_affinity_graph(node_embeddings)
    if not edges:
        return turns, None, {}

    nodes = list(node_embeddings)
    assignment = {node: turns[int(node)][2] for node in nodes}
    duration = {node: turns[int(node)][1] - turns[int(node)][0] for node in nodes}

    def score(mapping: dict[str, str]) -> float:
        return _graph_partition_score(nodes, mapping, edges)

    def group_seconds(mapping: dict[str, str], label: str) -> float:
        return sum(duration[n] for n, lab in mapping.items() if lab == label)

    best_score = score(assignment)
    applied: list[str] = []

    for _ in range(max_speakers):
        groups: dict[str, list[str]] = {}
        for node, label in assignment.items():
            groups.setdefault(label, []).append(node)
        candidates: list[tuple[float, str, dict[str, str]]] = []

        # Merge candidates: every pair of groups.
        if len(groups) > 2:
            group_labels = sorted(groups)
            for i in range(len(group_labels)):
                for j in range(i + 1, len(group_labels)):
                    keep, drop = group_labels[i], group_labels[j]
                    trial = {
                        node: (keep if label == drop else label)
                        for node, label in assignment.items()
                    }
                    candidates.append((score(trial), f"merged {drop} into {keep}", trial))

        # Split candidates: any group whose own turns divide into two voices.
        if len(groups) < max_speakers:
            for label, members in groups.items():
                if len(members) < _SPLIT_MIN_TURNS_PER_VOICE * 2:
                    continue
                partition = _two_voice_partition(
                    [node_embeddings[node] for node in members],
                    min_per_group=_SPLIT_MIN_TURNS_PER_VOICE,
                    min_separation=_SPLIT_MIN_SEPARATION,
                )
                if partition is None:
                    continue
                groups_of, _separation = partition
                primary = groups_of[0]
                new_label = f"{label}__graph_voice_{len(groups)}"
                trial = dict(assignment)
                for node, community in zip(members, groups_of):
                    if community != primary:
                        trial[node] = new_label
                # A new voice must own real speech, not a couple of scraps.
                if min(
                    group_seconds(trial, label), group_seconds(trial, new_label)
                ) < min_split_seconds:
                    continue
                candidates.append((score(trial), f"split {label}", trial))

        if not candidates:
            break
        candidates.sort(key=lambda item: item[0], reverse=True)
        gain = candidates[0][0] - best_score
        if gain < min_gain:
            break
        best_score, description, assignment = candidates[0]
        applied.append(description)

    if not applied:
        return turns, None, {}
    # Turns without a usable embedding never moved; they keep their label.
    refined = [
        (start, end, assignment.get(str(index), label))
        for index, (start, end, label) in enumerate(turns)
    ]
    # A split invents a label that never went through name resolution, so hand
    # back each resulting group's pooled voice. Without it the caller has no
    # embedding for the new speaker and no way to name or persist it.
    pooled_by_label: dict[str, list] = {}
    grouped: dict[str, list[str]] = {}
    for node, label in assignment.items():
        grouped.setdefault(label, []).append(node)
    for label, members in grouped.items():
        weights = np.asarray([duration[n] for n in members], dtype=np.float32)
        stacked = np.asarray(
            [
                node_embeddings[n] / max(float(np.linalg.norm(node_embeddings[n])), 1e-10)
                for n in members
            ],
            dtype=np.float32,
        )
        centroid = np.average(stacked, axis=0, weights=np.maximum(weights, 1e-6))
        norm = float(np.linalg.norm(centroid))
        if norm > 1e-10:
            pooled_by_label[label] = [float(v) for v in (centroid / norm)]
    summary = "; ".join(applied)
    print(
        f"Sortformer: turn-graph partition {len(labels)} → "
        f"{len({lab for _, _, lab in refined})} speakers ({summary})",
        file=sys.stderr,
    )
    return refined, summary, pooled_by_label


def _count_reconciliation_plan(
    effective_n_speakers: int | None,
    label_count: int,
    two_sided_enabled: bool,
) -> str:
    """Which count-reconciliation pass should run: the precedence, isolated.

    Returns "merge-down", "two-sided", "legacy-graph", or "none".

    Worth being a function rather than an if/elif chain, because the precedence
    is the part that goes wrong. An external count is an *answer*: reconcile down
    to it and otherwise leave the labels alone. Running the partition search when
    a count was supplied re-litigates it — that is how "Redetect at 4" came back
    with 3, the search having merged the fresh split back down.
    """
    if effective_n_speakers is not None:
        return "merge-down" if label_count > effective_n_speakers else "none"
    if two_sided_enabled:
        return "two-sided"
    return "legacy-graph" if label_count > 2 else "none"


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


def _renumber_speakers(
    speaker_names: dict,
    speaker_meta: dict,
    speaker_embeddings: dict,
    segments: list[dict],
) -> tuple[dict, dict, dict]:
    """Make speaker ids contiguous from 0, and keep generic names in step.

    Ids and display names are assigned *before* pruning, so a label that ends up
    owning no segments leaves a hole: Rec82 came out of a calendar-triggered
    re-diarisation with ids {0, 2} and names {"0": "Speaker 1", "2": "Speaker 4"}.
    Id 2 being called "Speaker 4" is not merely untidy — a reviewer sees a speaker
    numbered for a cluster that no longer exists, and anything keyed by a dense
    0..n-1 range disagrees with the sidecar about who is present.

    Real names are never touched; only generic "Speaker N" labels are restated to
    match the new id. Provenance and embeddings move with their speaker.
    """
    used = sorted({str(seg.get("speaker_id")) for seg in segments}, key=lambda k: int(k) if k.isdigit() else 1 << 30)
    remap = {old: index for index, old in enumerate(used)}
    if all(old == str(new) for old, new in remap.items()):
        return speaker_names, speaker_meta, speaker_embeddings

    for seg in segments:
        old = str(seg.get("speaker_id"))
        if old in remap:
            seg["speaker_id"] = remap[old]

    new_names, new_meta, new_embeddings = {}, {}, {}
    for old, new in remap.items():
        key = str(new)
        name = speaker_names.get(old, f"Speaker {new + 1}")
        # A generic label names its own id, so it has to be restated on a move.
        new_names[key] = f"Speaker {new + 1}" if _GENERIC_LABEL.fullmatch(str(name).strip()) else name
        if old in speaker_meta:
            new_meta[key] = speaker_meta[old]
        if old in speaker_embeddings:
            new_embeddings[key] = speaker_embeddings[old]

    for seg in segments:
        key = str(seg.get("speaker_id"))
        if key in new_names:
            seg["speaker"] = new_names[key]
    return new_names, new_meta, new_embeddings


def _absorb_degenerate_segments(segments: list[dict]) -> int:
    """Give zero-length segments to a real neighbour instead of a new speaker.

    Word-level alignment can emit a segment whose start equals its end — a single
    word with no duration. That is not a participant, but it is enough to create
    one: Rec82 came out of a calendar-triggered re-diarisation reading
    "1 word is Speaker 2, all the rest Speaker 1", where Speaker 2's entire
    existence was the zero-duration word "to".

    Pruning the speaker map alone does not fix it — the segment still carries the
    orphaned id, and renumbering then recreates the speaker from it. So repair the
    segment: keep the word, and attribute it to the nearest speaker that actually
    spoke. Returns the number of segments moved.
    """
    def duration(seg: dict) -> float:
        try:
            return float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))
        except (TypeError, ValueError):
            return 0.0

    substantive = [index for index, seg in enumerate(segments) if duration(seg) > 0.0]
    if not substantive:
        return 0
    moved = 0
    for index, seg in enumerate(segments):
        if duration(seg) > 0.0:
            continue
        # Nearest substantive segment by position, preferring the one before so a
        # trailing fragment stays with the speaker who was talking.
        nearest = min(substantive, key=lambda other: (abs(other - index), other > index))
        donor = segments[nearest]
        if seg.get("speaker_id") != donor.get("speaker_id"):
            seg["speaker_id"] = donor.get("speaker_id")
            if donor.get("speaker") is not None:
                seg["speaker"] = donor["speaker"]
            moved += 1
    return moved


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
    # "Owns a segment" is not the same as "spoke". Rec82 kept a speaker whose only
    # segment was zero-duration, which is a phantom participant in the UI and in
    # every downstream count.
    owners: set[str] = set()
    spoken: dict[str, float] = {}
    for seg in segments:
        key = str(seg.get("speaker_id"))
        owners.add(key)
        if "start" not in seg or "end" not in seg:
            continue
        try:
            duration = float(seg["end"]) - float(seg["start"])
        except (TypeError, ValueError):
            continue
        spoken[key] = spoken.get(key, 0.0) + max(0.0, duration)
    # Drop a speaker only when its segments *are* timed and sum to nothing.
    # Absent timing means unknown, not silent — punishing missing metadata would
    # delete real speakers from any caller that omits it.
    used = {key for key in owners if spoken.get(key, None) is None or spoken[key] > 0.0}
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
    *,
    pinned_intervals: list[tuple[float, float]] | None = None,
    two_sided_partition: bool = _TWO_SIDED_PARTITION_DEFAULT,
    refine_assignments: bool = _REFINE_ASSIGNMENTS_DEFAULT,
) -> dict:
    """Diarize with NeMo Sortformer.

    Signature and return shape match `shared.diarize_lite.diarize` so
    callers can swap backends without code changes. `n_speakers` is
    accepted but Sortformer caps at 4; the hint is informational only.

    `pinned_intervals` are stretches whose speaker a human has already
    confirmed; refinement never moves them. `two_sided_partition` and
    `refine_assignments` gate the graph search and the reassignment loop so
    `shared.diarisation_eval` can measure each against the reviewed corpus
    instead of them being switched on by assertion.
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
    speaker_info, naming_model = _resolve_speaker_names(
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
    # Identity is resolved above, per stitched label. The reconciliation passes
    # below rewrite labels, so keep the pre-reconciliation turns to re-pool
    # evidence for any label that ends up absorbing others.
    pre_reconcile_turns = list(renamed_turns)
    # Labels invented by the partition search, which need naming from scratch.
    split_labels: set[str] = set()

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
    plan = _count_reconciliation_plan(
        effective_n_speakers, len(internal_labels), two_sided_partition
    )
    if plan == "merge-down":
        before = len(internal_labels)
        renamed_turns = _merge_labels_to_count(
            renamed_turns, label_embs, effective_n_speakers
        )
        surviving = {lab for _, _, lab in renamed_turns}
        internal_labels = [lab for lab in internal_labels if lab in surviving]
        print(
            f"Sortformer: merged {before} labels down to {len(internal_labels)} "
            f"at speaker count {effective_n_speakers}",
            file=sys.stderr,
        )
    elif plan == "two-sided":
        # No external count to trust, so search the partition in both directions
        # on a fixed turn-level graph. Replaces the merge-only label-graph pass,
        # which could never correct the under-counting the harness measured.
        refined_turns, partition_summary, partition_voices = _refine_partition_by_turn_graph(
            audio, renamed_turns,
        )
        if partition_summary:
            renamed_turns = refined_turns
            surviving = {lab for _, _, lab in renamed_turns}
            # A split introduces labels that were never in internal_labels.
            internal_labels = [lab for lab in internal_labels if lab in surviving] + [
                lab for lab in dict.fromkeys(l for _, _, l in renamed_turns)
                if lab not in internal_labels
            ]
            # A split creates a label that never went through name resolution.
            # Seed it from the group's pooled voice so it has an embedding to be
            # named and persisted from; re-pooling below does the matching.
            for new_label in internal_labels:
                if new_label in speaker_info:
                    continue
                speaker_info[new_label] = {
                    "name": new_label,
                    "source": "generic",
                    "confidence": None,
                    "embedding": partition_voices.get(new_label),
                }
                split_labels.add(new_label)
            count_strategy = "turn-graph-two-sided"
    elif plan == "legacy-graph":
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
    # Only defend a requested count when the labels it would keep are real. A
    # requested count is evidence about the *meeting*, not proof that every
    # attendee spoke, so it must never justify keeping a scrap of audio as a
    # participant.
    substantive = sum(1 for secs in talk_seconds.values() if secs >= _MICRO_LABEL_MAX_SECONDS)
    if (
        n_speakers is not None
        and absorbed_count < n_speakers <= len(talk_seconds)
        and substantive >= n_speakers
    ):
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

    # With the speaker set settled, revisit individual turns against the pooled
    # voice of each cluster. Confirmed stretches are pinned so a re-diarisation
    # can never move speech a human has already signed off.
    if refine_assignments:
        renamed_turns, _moved = _reassign_turns_to_pooled_voices(
            audio, renamed_turns, pinned_intervals=pinned_intervals,
        )
        surviving = {lab for _, _, lab in renamed_turns}
        internal_labels = [lab for lab in internal_labels if lab in surviving]
    # Every structural change above (count merge, graph merge, micro absorption)
    # is now settled, so re-derive identity from each surviving cluster's pooled
    # audio rather than from whichever single label survived.
    speaker_info = _repool_merged_speakers(
        speaker_info,
        pre_reconcile_turns,
        renamed_turns,
        internal_labels,
        allowed_names=getattr(calendar_context, "candidate_names", None),
        also_resolve=split_labels,
    )
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

    # A zero-duration segment is a word-timing artifact, not a speaker. Repair
    # those first, or pruning removes the name while the orphaned id survives on
    # the segment and renumbering recreates the phantom from it.
    degenerate = _absorb_degenerate_segments(segments_out)
    if degenerate:
        print(
            f"Sortformer: reattached {degenerate} zero-length segment(s) to a "
            "speaker that actually spoke",
            file=sys.stderr,
        )

    # Labels whose segments all got filtered out would show as phantom
    # people in the app — drop them from the speaker maps.
    speaker_names, speaker_meta, speaker_embeddings = _prune_empty_speakers(
        speaker_names, speaker_meta, speaker_embeddings, segments_out
    )
    # Pruning leaves holes in the id space; close them so the sidecar presents a
    # dense 0..n-1 set of speakers whose generic names match their ids.
    speaker_names, speaker_meta, speaker_embeddings = _renumber_speakers(
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
        # Which model produced those vectors. TitaNet and ReDimNet2 are both
        # 192-dim, so without this a consumer can compare across two unrelated
        # cosine spaces and get confident-looking nonsense rather than an error.
        "speaker_embedding_model": naming_model,
        "backend": "sortformer",
        "speaker_count_strategy": count_strategy,
    }
