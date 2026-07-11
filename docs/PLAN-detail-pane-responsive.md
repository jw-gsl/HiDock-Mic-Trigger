# Detail pane + main table responsiveness
Planning date: 2026-07-11
Status: IN PROGRESS

## Debug (what overflows and why)
With the right detail pane open (HSplitView), the window splits ~half/half:
1. **Recordings table** — fixed column widths sum to ~1141px
   (`checkbox36 + Device120 + Status110 + Tagged90 + Summary80 + Recording220 +
   Created155 + Transcribed140 + Length70 + Size70 + actions50`). When the main
   column is narrower than that, the rightmost columns (Length/Size/actions) clip
   and there's no way to reach them.
2. **Transcript pane toolbars** at ~640px:
   - Speakers strip (Rematch / Reassign / Detect Speakers + Count stepper) is a
     single-line HStack → runs off both edges.
   - Top bar filename is long; Copy All / Show File get squeezed.
   - Speaker legend + verify rows are wide (chips + 2 buttons per row).

## Responsive strategy
### Main recordings table → 2-axis scroll
Wrap the header + rows in a horizontal ScrollView with content width =
`max(availableWidth, 1141)`: fills when wide, scrolls when narrow. Header and
rows share the same width so they scroll together; vertical scrolling of rows
stays inside. Keep the List (bounded width) rather than rewriting to LazyVStack.

### Transcript pane toolbars → contain within the pane width
- **Speakers strip**: horizontal ScrollView so the buttons scroll instead of
  clipping (lossless, no layout guesswork).
- **Top bar**: filename truncates (middle), Copy All / Show File become compact
  (icon + tooltip) so they always fit.
- **Speaker legend**: already a horizontal ScrollView — fine.
- **Verify rows**: keep but let the provenance/confidence chips truncate and the
  actions stay pinned right; the row is already inside the panel's vertical
  ScrollView.

### Layout
- HSplitView main min 560, detail min 480 / ideal 640 (already in place).
- Table scroll makes 560 usable; toolbars scroll/compak make 480 usable.

## Decisions
- Horizontal scroll for the table (per request) rather than hiding columns.
- Toolbars: scroll (Speakers strip) + compact (top bar) rather than wrap, to keep
  a predictable single-row height.
