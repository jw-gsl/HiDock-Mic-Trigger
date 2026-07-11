# In-app panels + tabs (consolidating windows)
Planning date: 2026-07-11
Status: PROPOSAL — needs steer before building

## Audit — windows the app opens (NSWindow + NSHostingView)
Main: **HiDock** (`showSyncWindow`, `syncWindow`).
Secondary (each a separate top-level window today):
1. Transcript viewer (`openTranscriptViewer`) — HIGH frequency, multiple at once wanted
2. Summary viewer (`openSummaryViewer`) — HIGH frequency
3. Voice Library (`openVoiceLibrary`)
4. Voice Training (`showVoiceTraining`)
5. Device Manager (`openDeviceManager`)
6. Models (`openModelManager`)
7. Summary Templates (`openTemplatesManager`)
8. Transcription Queue
9. Terminal (`openTerminalMenu`)
10. My Feedback (`showFeedbackHistory`)
11. Trim Audio (`showTrimDialog`) — modal-ish
12. Plaud login (`PlaudLoginWindowController`) — modal auth

Partial grouping exists via `applyPanelTabbing` (macOS-native window tabs) but it's
ad-hoc, and transcripts/summaries each spawn their own window — so juggling several
meetings means several windows.

## The ask (2026-07-11)
- Host these views **inside the app** (reuse the CLI sidebar area as a panel host)
  instead of separate windows.
- A **tabbed bar** so you can open **multiple meetings** at once as tabs.

## Proposed design — in-app detail pane with tabs
- View model owns `openTabs: [DetailTab]` + `activeTabId`, where
  `DetailTab = {id, kind}` and `kind ∈ .transcript(path) | .summary(path) |
  .voiceLibrary | .deviceManager | …`.
- MainWindowView gains a **detail region** (the right pane the CLI already lives in,
  generalised) with a **tab strip** across the top: one tab per open item, close
  buttons, click to switch. The CLI becomes one tab kind among others.
- `open*` actions push/focus a tab instead of `NSWindow(...).makeKeyAndOrderFront`.
  The existing SwiftUI views (TranscriptViewerView, VoiceLibraryView, …) embed
  as-is — they don't depend on being in a window.

### Phasing (each shippable)
1. **Tab host + transcripts/summaries first.** Build the detail pane + tab strip;
   route `openTranscriptViewer` / `openSummaryViewer` into tabs. This alone
   delivers "open multiple meetings." Keep everything else as windows.
2. **Fold in the tool views** (Voice Library, Device Manager, Models, Templates,
   Voice Training, Queue) as tab kinds.
3. **Retire `applyPanelTabbing`** once the tab host covers those.
- Leave truly modal things as windows/sheets: Trim, Plaud login, Terminal
  (arguably), and keep an "Open in new window" escape hatch for pop-out.

### Tradeoffs
- Pro: one window, multiple meetings as tabs, consistent, less window sprawl.
- Con: sizable refactor; need to handle per-tab titles/close/persistence; a few
  views may assume window sizing. Recommend Phase 1 first to prove the pattern.

## Open questions
- Layout: tabs in the existing right sidebar, or a full-width detail area below the
  list? (Meetings probably want width → maybe a dedicated detail area, with the CLI
  as one of the tabs.)
- Should closing the last tab collapse the pane?
- Pop-out to a real window as an escape hatch?
