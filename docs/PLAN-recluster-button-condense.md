# Condense the "Re-cluster from my labels" button
Planning date: 2026-07-04
Status: PLAN ONLY (no code changes yet)
Source: hidock-mic-trigger/Sources/Views/TranscriptViewerView.swift ~360–368

## Problem
The button in the transcript viewer's speaker toolbar reads
**"Re-cluster from my labels"** — too long, and it wraps/truncates in the
control row (reported as "Relus-cter from my…"). It sits alongside "Re-diarize"
and a Stepper, so horizontal space is tight.

## Current code
```swift
if onReclusterWithLabels != nil, hasUserNamedSpeakers {
    Button { onReclusterWithLabels?(filePath) } label: {
        Label("Re-cluster from my labels", systemImage: "person.crop.circle.badge.checkmark")
    }
    .buttonStyle(.bordered).controlSize(.small)
    .help("Use the speakers you've named as anchors and re-assign every other segment to its closest match. The pieces of the conversation you've already corrected stay put.")
}
```

## Plan
- **Shorten the label to `"Re-cluster"`** (icon carries the rest of the meaning).
  The `person.crop.circle.badge.checkmark` icon already implies "from my named
  speakers". Alternative if a hint is wanted: `"Re-cluster ▸ labels"` — but plain
  `"Re-cluster"` is cleanest next to "Re-diarize".
- **Keep the explanation in the tooltip** (it's already good). Tighten slightly:
  > "Re-assign every un-named segment to its closest match, using the speakers
  >  you've named as anchors. Segments you've already corrected stay put."
- Consider `.fixedSize()` on the button (as other toolbar controls use) so it
  never truncates regardless of window width.
- Sanity-check the sibling "Re-diarize" and the Stepper still fit on one row at
  the viewer's min width after the change; if the row is still cramped, drop the
  icon text entirely and rely on icon + tooltip.

## Files to touch
- Views/TranscriptViewerView.swift (label text + `.help` copy + optional
  `.fixedSize()`). One-line change; no logic/behaviour change.
