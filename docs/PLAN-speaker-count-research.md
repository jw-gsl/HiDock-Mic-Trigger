# Speaker Count & Diarization Strategy Research

Research date: 2026-04-13

---

## Industry Consensus: Over-Segment Then Merge

Every source — academic, commercial, and open-source — agrees: **overestimate speakers first, then let the user merge.**

> "It's much easier to combine speakers' utterances if the model breaks them up into different speaker labels than it is to disentangle two speakers being combined into one."
> — [AssemblyAI, 2026](https://www.assemblyai.com/blog/what-is-speaker-diarization-and-how-does-it-work)

### How the Market Leaders Do It

| Product | Approach | Max Speakers | Speaker Merge UX |
|---------|----------|-------------|-------------------|
| **HiNotes/HiDock** | Stereo mic array for L/R separation. Over-segments, user merges. Multiple labels for same person is normal. | Unknown | Rename speaker → updates all instances |
| **Plaud Note** | Dual-mic beamforming. Auto-detects speakers. "Manually assigning names once improves future diarization." | Unknown | Rename + learns for future |
| **Otter.ai** | Voice pattern analysis + calendar cross-reference. 89-95% accuracy optimal. Learns over time. | 10 | Rename, auto-learns |
| **Fireflies** | Recent algorithm updates for similar voices and cross-talk. ~30% reduction in manual corrections. | 50 | Manual correction + learning |
| **minutes (OSS)** | pyannote-rs segmentation + running-average templates. Post-clustering merge pass. Voice enrollment blending. | Configurable | CLI + MCP tools |

### Key Insight from HiNotes

HiNotes tends to have **multiple speaker labels for the same person** — not loads, but a few. This is deliberate over-segmentation. The user picks a name for each label and all instances update. This is faster than trying to split one label into two people.

### Key Insight from Otter.ai

Otter cross-references with **calendar invites** to get the expected attendee list, then maps speaker embeddings to known attendees. This is the gold standard for accuracy — the system knows who should be in the meeting before it starts.

### Key Insight from Plaud

"Manually assigning the correct names **once** can improve future diarization." Same running-average enrollment approach we use. The feedback loop is the product.

---

## Decision: Flip to Over-Segment Strategy

### What to change:
1. **Remove the penalty** on higher speaker counts — let the silhouette score pick naturally
2. **Bias toward MORE speakers** — better to have Speaker 1 and Speaker 1a than to merge two different people
3. **Cap at reasonable maximum** — don't detect 15 speakers, but 3-5 for a meeting expected to have 2 is fine
4. **Make merge easy** — renaming a speaker updates ALL instances throughout the transcript. This already works.
5. **Add "same as" merge** — in addition to right-click merge, a dropdown "This is the same person as [Speaker X]" per speaker pill

### What NOT to change:
- The 30s segment cap — keeps blocks readable regardless of speaker count
- The voice library enrollment — already does running-average blending
- The Re-diarize stepper — still useful when user knows the exact count

---

## Implementation Plan

- [x] Research industry approaches
- [ ] Remove speaker count penalty (or invert it to bias toward more)
- [ ] Lower clustering distance threshold to produce more clusters
- [ ] Test on 7 recordings — verify over-segmentation
- [ ] Update merge UI hint: "Tap speaker names to merge — it's normal to see the same person split across labels"
- [ ] Document in PLAN-diarization-improvements.md

---

## Sources

- [AssemblyAI: What is Speaker Diarization](https://www.assemblyai.com/blog/what-is-speaker-diarization-and-how-does-it-work)
- [HiDock P1 vs Plaud Note Pro](https://www.hidock.com/blogs/productivity-hacks/hidock-p1-vs-plaud-note-pro-which-ai-voice-recorder-is-better-in-2026)
- [Otter.ai Speaker Identification Guide](https://summarizemeeting.com/en/faq/does-otter-ai-identify-speakers)
- [Fireflies Speaker Diarization Review](https://summarizemeeting.com/en/app-reviews/fireflies-speaker-diarization)
- [Plaud Intelligence](https://www.plaud.ai/pages/plaud-intelligence)
- [silverstein/minutes](https://github.com/silverstein/minutes)
