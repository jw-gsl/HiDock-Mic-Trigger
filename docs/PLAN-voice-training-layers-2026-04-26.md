# Voice Training: layered approach
Research date: 2026-04-26
Sources: feature/voice-training branch; user discussion 2026-04-26 evening; transcribe.py / shared/diarize_lite.py / TranscriptViewerView.swift

## Current State

We diarize transcripts at segment granularity using Silero VAD + TitaNet
embeddings + hierarchical clustering (`shared/diarize_lite.py`). Output
labels each Whisper segment as `Speaker N`. Users rename `Speaker N` to a
real name in the Transcript Viewer; on rename, the first segment from
that speaker becomes a voice-library enrolment sample. Users can:

- Rename speakers (bulk-applies to every segment with that ID).
- Merge one speaker into another (re-runs the consecutive-merge pass).
- Map a speaker's segments to a different speaker via context menu.

What does NOT work today:
- **Mid-segment speaker boundaries.** When two people talk inside what
  Whisper recorded as one segment, diarization labels the whole thing
  as one speaker. The user has no way to mark "from this word onward
  is Speaker B" — the only operations are segment-level.
- **Cross-recording learning.** The voice library stores samples but
  isn't used to seed future diarization runs. Same speaker across two
  recordings = two unrelated `Speaker N` clusters.
- **Re-clustering with corrections.** Once a user renames speakers,
  there's no way to feed those labels back as constraints for a fresh
  diarization pass.

## Findings

The user surfaced this with a concrete case: a segment containing
two speakers' words got labelled monolithically. They asked if the UI
could let them highlight the words spoken by each and feed the
correction back into diarization.

Current Whisper output shape: segments have `{start, end, text}` only —
no word-level timestamps. So a precise word-time split would require
either re-transcribing with `word_timestamps=True` or accepting linear
time interpolation across the segment's words. Linear interpolation is
the pragmatic call: a 30 s segment with 100 words gives ~0.3 s/word
accuracy, well within TitaNet's effective resolution.

Diarization with anchors is a clean extension of `diarize_lite`:
- The pipeline already computes a 192-d TitaNet embedding per segment.
- If a subset of segments has a user-confirmed name attached, those
  embeddings are anchor centroids.
- Every other segment can be re-assigned to its nearest anchor by
  cosine similarity (with a confidence threshold for "leave as Unknown
  Speaker N" so we don't force-fit obvious strangers).

Cross-recording learning needs: persistent voice library on disk,
loaded as initial centroids for every new diarization run. The
enrolment plumbing exists; the load-into-diarization plumbing
doesn't yet.

## Completed

- [x] Per-segment speaker rename in Transcript Viewer (renames + saves
  to JSON, regenerates .md / .srt on save).
- [x] Speaker merge (one speaker absorbed into another, consecutive
  segments re-merged).
- [x] Voice library enrolment sample written on first rename of each
  speaker (audio range + name).
- [x] Voice Library window listing enrolled voices.
- [x] Auto-detected merge candidates + multi-row merge UI (separate
  feature, mentioned because it's the same UI surface).
- [x] **Layer 1 — Word-level split in Transcript Viewer.** Shipped
  2026-04-27 in commit `82b0024` as a context-menu → sheet flow
  ("Split segment at a word…"). **Superseded 2026-04-28 by the
  inline range UX below** (user feedback: the sheet felt clunky and
  the context menu was shadowed by the native text-selection menu
  whenever the user selected text first). The `applySplit` /
  enrolment-on-second-half logic from this version is reused by the
  new UX.
- [x] **Layer 2 — Re-diarize with anchors.** Shipped 2026-04-27 in
  commit `82b0024`. `shared/recluster_with_anchors.py` loads the
  TitaNet embedding model, treats every user-named segment as an
  anchor centroid (averaged per name), reassigns every other segment
  to its nearest anchor by cosine similarity (≥0.55), final
  consecutive-same-speaker merge. Exposed as
  `transcribe.py recluster-with-anchors <diarized.json>` and via the
  "Re-cluster from my labels" button in the Transcript Viewer
  (visible only when at least one speaker has been renamed away from
  the auto "Speaker N").

## In Progress (this session — 2026-04-28)

- [ ] **Layer 1 v2 — Inline word-token range selection.** Replaces
  the sheet shipped on 2026-04-27. New UX:
  - Each segment's text renders as a flow of clickable word tokens
    (FlowLayout reused — was previously sheet-only).
  - User **drag-selects** a range of words inside one segment. Tokens
    inside the range tint blue. Single-click selects one word. The
    selection lives in row-local state; only one segment can have an
    active range at a time.
  - The moment a range exists, a thin inline speaker bar slides in
    **below that segment** (chosen over above so the text doesn't
    shift): existing speaker pills + "New speaker" + a small ✕ to
    cancel the selection.
  - Click a pill → the words in the range become a new sub-segment
    assigned to that speaker. Up to a 3-way split:
    - range = whole segment → just reassign speakerId, no structural
      split
    - range starts at word 0, doesn't reach the end → 2 parts
      (range first, then original tail)
    - range starts mid-segment, reaches the end → 2 parts
      (original head, then range)
    - range strictly in the middle → 3 parts
      (original head, range, original tail)
  - Time boundaries on each new sub-segment via linear interpolation
    over word indices (consistent with the old sheet's behaviour).
  - Enrolment fires on the **range sub-segment** (cleaner provenance
    than the whole-segment sample we used to take from the second
    half of a single-cut split).
  - The old sheet, the context-menu "Split segment at a word…" item,
    and the `splittingSegmentIndex` / `splitWordIndex` state are
    removed entirely.
  - `.textSelection(.enabled)` on segment text is removed (replaced
    by tokens). Plain-text copy stays available via the existing
    "Copy All" button in the toolbar; per-segment copy can be added
    later if asked.

- [ ] **Parity update.** Layer 1 v2 is macOS-only on landing. Document
  the gap in `PARITY.md`. Windows-side port is a separate piece of
  work — flagged but not in scope for this commit.

## Planned (Layer 3 — separate sessions)

- [ ] **Persistent voice library indexed by speaker name.**
  `~/HiDock/voice_library/<name>/<sample_id>.{wav,emb}` with one
  TitaNet embedding per sample.
- [ ] **Pre-load library as initial centroids in `diarize_lite`.**
  When clustering, every library entry's embedding becomes a candidate
  centroid before clustering starts. New segments above the
  recognition threshold get the library name; below it, they cluster
  into fresh `Speaker N` IDs as today.
- [ ] **Confidence threshold tuning.** Needs a small eval set
  (couple of hand-tagged recordings with known speakers) to land on
  a sensible default. Coupled to PLAN-eval-suite-2026-04-23.
- [ ] **Library hygiene UI.** Voice Library window already lists
  enrolled voices; add per-sample preview/play, sample count, delete.
- [ ] **Multi-recording cluster review.** Across the user's last N
  recordings, show a graph of detected clusters and let the user
  collapse "these are the same person" decisions in bulk. Powers a
  "found 7 different Speaker 1's that are actually James" flow.

## Rejected / Not Applicable

- **Per-word Whisper timestamps**: would give exact boundary times for
  Layer 1's split, but doubles transcription latency and changes the
  pipeline's data shape. Linear interpolation is good enough for
  embedding purposes (the audio range fed to TitaNet is wide enough
  that a one-word miss doesn't change the centroid materially).
- **Anchor-aware clustering as a single algorithm**: tempting to
  re-write the clustering pass to know about anchors directly, but
  the simpler "embed everything → assign to nearest anchor"
  post-process is sufficient and easier to reason about.
- **Live-update voice library while user is in the Viewer**: each
  rename already enrols the first segment; doing it more aggressively
  would just contaminate the library with edge-case samples before
  the user has confirmed.

## Recommended order

1. **Layer 1 first.** It's a UI-only change with no risk to the
   diarization pipeline, and gives the user immediate fix-it power
   for mis-split segments. Also produces cleaner enrolment samples
   for everything downstream.
2. **Layer 2 next** — same session if there's bandwidth. Builds on
   Layer 1 because Layer 1 produces the cleanest possible anchors.
3. **Layer 3** lives behind eval-suite work. We need to measure DER
   before/after persistent-library bootstrapping or we're flying blind.

## Implementation notes

### Layer 1 UI surface
- Segment row currently: play button · timestamp · speaker pill · text.
- Add: render `text` as a series of word `Button`s in a wrapping
  HStack/FlowLayout. Click a word → `confirmationDialog` with the
  existing speaker pills + "New speaker".
- Pick speaker → split segment at that word's index; insert new
  segment with chosen speakerId, adjust times by linear interpolation,
  re-render.
- Save to JSON; trigger enrolment on the new sub-segment.

### Layer 2 algorithm
```
def recluster_with_anchors(diarized_json_path):
    data = load(diarized_json_path)
    audio = load_audio(data["audio_file"])
    segments = data["segments"]
    names = data["speaker_names"]   # {"0": "James", "1": "Speaker 2", ...}

    # Anchors: every segment whose speaker has a non-default name.
    # Default name = "Speaker {id+1}". Treat anything else as confirmed.
    anchors_by_name = defaultdict(list)
    for seg in segments:
        sid = seg["speaker_id"]
        nm = names.get(str(sid), "")
        if nm and not nm.startswith("Speaker "):
            anchors_by_name[nm].append(seg)

    if not anchors_by_name:
        # Nothing to anchor against; bail.
        return

    # Compute centroids.
    centroids = {}
    for name, segs in anchors_by_name.items():
        embs = [embed_audio_range(audio, s["start"], s["end"]) for s in segs]
        centroids[name] = mean(embs, axis=0)

    # Reassign each segment.
    THRESHOLD = 0.6   # cosine similarity; tune via eval suite later
    for seg in segments:
        emb = embed_audio_range(audio, seg["start"], seg["end"])
        best_name, best_sim = max(
            ((nm, cosine(emb, c)) for nm, c in centroids.items()),
            key=lambda x: x[1],
        )
        if best_sim >= THRESHOLD:
            seg["speaker_id"] = name_to_id(best_name)
        # else: leave the existing speaker_id alone

    save(diarized_json_path, data)
```

### Status as of writing
Plan written; Layer 1 + Layer 2 implementation begins immediately
after this commit.

### Update 2026-04-28
Layer 1 (sheet) and Layer 2 (recluster) shipped in `82b0024`. User
hit Layer 1 by selecting text and right-clicking, which surfaced the
native text-selection menu instead of the SwiftUI contextMenu — the
Split affordance was effectively invisible. Decision: rip the sheet
out, render words as inline tokens, drag-select a range, show a
speaker bar below the segment. Captured above as "Layer 1 v2".
