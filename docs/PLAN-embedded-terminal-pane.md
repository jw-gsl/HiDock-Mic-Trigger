# Embedded Terminal / Activity Pane

Research date: 2026-06-16
Sources: existing `AppDelegate.openTerminal(initialCommand:)`, `summariseRecording`/`processNextSummary` (serial summarise queue, slice 3 / PR #32), `runTranscription` (subprocess + stdout/stderr streaming), SwiftTerm (github.com/migueldeicaza/SwiftTerm).

## Current State
- "Ask Claude Code…" (row context menu) calls `askClaudeAboutRecording` → `openTerminal(initialCommand:)`, which launches an **external** Terminal.app window running `claude "…"` in the transcript's folder.
- Summarise runs (`processNextSummary` → `runTranscription(["summarize", …])`) stream stdout/stderr internally but surface only as a transient "Summarising" pill + log lines. The user can't see what Claude Code is doing.
- The main window is the recordings table (RecordingsTableView) inside MainWindowView, with toolbar (SyncToolbarSection) above.

## Goal (user, 2026-06-16)
1. **Ask Claude Code opens *inside* the app**, in a right-hand pane — not a separate window. The pane takes horizontal space, narrowing the table (the table can drop trailing columns down to the folder-icon actions).
2. The same pane **shows live activity** while a summary is generated — both auto-summarise and manual (button / row action) runs — so the user sees what's happening.

## Design fork — how "embedded terminal" is realised

### Option A — Real embedded terminal (SwiftTerm)
Add SwiftTerm (SPM) and wrap its `TerminalView` in an `NSViewRepresentable`. Spawn a PTY child (`claude …` for Ask; the summarise subprocess for activity) attached to the view.
- Pros: genuinely interactive — user can type back to Claude Code in-pane; ANSI colour; matches the mental model of "a terminal in the app".
- Cons: new third-party dependency (vendored or SPM); PTY process management; input focus/keyboard handling; more surface area to test. Auto-summarise activity would attach the summarise process's PTY output to the same view.

### Option B — Split: read-only activity pane + in-pane Ask via PTY
A right-hand pane that is primarily a **read-only streaming log** (NSTextView/SwiftUI) fed by the summarise subprocess output we already capture. "Ask Claude Code" still needs interactivity → either keep it external (cheaper) or also embed via SwiftTerm.
- Pros: activity streaming is easy (we already have the bytes); no dependency if Ask stays external.
- Cons: doesn't fully satisfy "Ask Claude Code in the pane" unless we still pull in a PTY/SwiftTerm for that part.

### Recommendation
**Option A (SwiftTerm)** — it's the only one that delivers both asks in one consistent surface (interactive Ask + live summarise activity), which is what was requested. Option B half-solves it and still needs a terminal emulator for the interactive half, so it doesn't save much.

## Decided
- **Pane toggle (2026-06-16):** a **bottom-bar button labelled "CLI"** with a terminal icon toggles the pane open/closed. Pane also auto-opens when an Ask/summarise run starts (so activity is visible), but the user can collapse it via the CLI button.

## Decided (cont.)
- **Approach (2026-06-17): Option A — SwiftTerm.** Real interactive terminal in-pane (Ask Claude Code typeable) + live summarise activity in one surface. Accept the SwiftTerm SPM dependency.
- **Glyph alignment (2026-06-17):** Device column renders **glyph-first, then name** (currently name-first) so glyphs line up down the column. Apply to recording rows + merge-parent rows. Bundled into this slice (same file).
- [ ] Which columns the table sheds as the pane opens (keep through Status/Tagged/Summary + folder actions; drop Created/Length/Size first?).
- [ ] Auto-summarise: stream into the pane automatically (pane auto-opens) or only when the user has the pane open?

## Completed (2026-06-17, slice 4)
- [x] Decided A (SwiftTerm) + bottom-bar CLI toggle.
- [x] `TerminalPaneController` (shared, persistent shell) + `EmbeddedTerminalPane` in `Views/TerminalPane.swift`.
- [x] Right-hand pane in MainWindowView (HStack split, 340–560pt), `cliPaneVisible` on the view model, bottom-bar "CLI" toggle button.
- [x] Ask Claude Code now runs in the pane (`runCommand` → PTY stdin) instead of a separate window; pane auto-opens.
- [x] Summarise activity (auto + manual) feeds event markers into the pane (`appendActivity` → display-only `feed`); pane auto-opens on summarise.
- [x] Glyph-first Device column (recording + merge-parent rows).
- [x] Built (xcodebuild Debug) + deployed.

## Planned / follow-ups
- [ ] Optional: stream the full summarise subprocess output (stderr) into the pane for token-level visibility (currently event-level markers only — authoritative run stays managed for reliable JSON→summaryPath).
- [ ] Windows parity (`Windows-App/`, PyQt6): embedded terminal/activity equivalent — separate follow-up; update PARITY.md.
- [ ] The menu "Open Terminal" still opens the standalone window (kept for `claude auth login`); consider consolidating onto the pane later.

## Rejected / Not Applicable
- (none yet)
