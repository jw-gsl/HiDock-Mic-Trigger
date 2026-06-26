# Formatted CLI / Agent view (headless + native streaming UI)
Research date: 2026-06-26
Sources: hidock-mic-trigger/Sources/Views/TerminalView.swift, TerminalPane.swift,
AppDelegate.swift (askClaudeAboutRecording, summariseRecording/reclassifySummary,
aiCliBinary, runTranscription onLine), shared/llm_cli.py (query/query_streaming),
Windows-App/ (PyQt6). claude 2.1.193 headless flags confirmed via `claude --help`.

## Goal
Replace the raw SwiftTerm output for AI interactions with a native, nicely
formatted view that streams the engine's output (markdown text + tool activity).
Run the CLI **headless** and render the event stream ourselves. Keep the raw
terminal only for interactive auth (`claude auth login`). Support **all engines**
(claude, codex, gemini, ollama) — Claude gets full tool-activity detail; others
stream text-only (their CLIs don't emit a structured event stream).

## Current State (as built today)
Two distinct flows feed the single SwiftTerm "CLI" pane (`TerminalPaneController`):

1. **Auto-summary / Summarise Selected / Reclassify** — *headless, one-shot.*
   - App runs `transcribe.py summarize <transcript> [--template T] [--summarize-engine E]`
     via `runTranscription(onLine:)`.
   - Pipeline calls `llm_cli.query_streaming()`. For `claude` it already uses
     `--print --output-format stream-json --include-partial-messages --verbose`
     and forwards `content_block_delta` text. For other engines it falls back to
     a single blocking `query()`.
   - App only sees the pipeline's `STAGE:` markers + a final JSON
     `{summarized, summary_path}`; the LLM's streaming text is NOT forwarded to
     the app today. Each stdout line is dumped raw into the PTY via
     `appendActivity()`. Result is a saved `.md` rendered in `SummaryViewerView`.

2. **Ask AI / Create template / Improve template** — *interactive, multi-turn.*
   - `askClaudeAboutRecording` etc. build `cd "<dir>" && <aiCliBinary> "<prompt>"`
     and type it into the persistent PTY (`runCommand`), launching the full
     interactive Claude Code **TUI** inside the embedded terminal. User types
     follow-ups in the TUI. This is the rawest/ugliest surface.

Engine inventory (`shared/llm_cli.py`): `claude --print`, `codex --quiet`,
`gemini`, `ollama run <model>`. Only `claude` supports realtime stream-json.

## Findings — interaction model per flow
- **Auto-summary**: inherently non-interactive. Formatted view = a **live
  generation readout** — STAGE steps as a checklist, streamed assistant text as
  markdown, Claude tool activity as compact chips, then the saved summary. No
  input box.
- **Ask AI / templates**: inherently conversational. Formatted view = a **native
  chat** — message bubbles, an input box, streamed responses, tool-activity
  chips, multi-turn via session resume.

## Design — one normalized event stream, one renderer
Centralize all engine-specific parsing in the shared Python layer; the native
apps consume a single normalized newline-delimited JSON (NDJSON) event schema and
render it. This is what makes "all engines" tractable and keeps macOS/Windows in
parity (both reuse the same Python emitter).

### Normalized event schema (NDJSON on stdout, one JSON object per line)
```
{"t":"stage","label":"Summarising"}          // pipeline progress step
{"t":"text","delta":"partial assistant text"} // streamed assistant prose
{"t":"tool","id":"..","name":"Read","input":{...}}   // claude only
{"t":"tool_result","id":"..","ok":true,"preview":"..."} // claude only
{"t":"usage","input_tokens":..,"output_tokens":..}   // optional, claude
{"t":"error","message":".."}
{"t":"done","summary_path":"..","session_id":".."}   // terminal event
```
- `shared/llm_cli.py` already parses Claude's `stream-json`; extend
  `query_streaming` to emit these normalized events (not just `on_text`).
- For codex/gemini/ollama: emit `stage` + a single (or line-buffered) `text`
  event + `done`. No `tool` events — documented limitation, surfaced in the UI as
  "tool activity not available for <engine>".

### Two entry points into the same emitter
- **Summary flow**: `transcribe.py summarize …` switches its stdout to the
  normalized event stream (behind a `--events` flag so existing callers/tests are
  unaffected). The app already reads this subprocess line-by-line.
- **Ask AI flow (NEW)**: add a `chat`/`ask` subcommand (or have the app spawn the
  engine directly for claude) that:
  - claude: `claude --print --output-format stream-json --include-partial-messages
    --verbose` with `--input-format stream-json` + `--resume <session_id>` for
    multi-turn. Normalize to the schema above.
  - other engines: one-shot per turn (re-send conversation context); text-only.
  This routes Ask AI through the same normalized stream so the Swift/Qt side has
  ONE renderer.

### Native rendering (macOS, SwiftUI)
- New `AgentTranscriptView` (replaces SwiftTerm for AI flows):
  - assistant text → markdown (code blocks, lists, tables). Evaluate
    `swift-markdown-ui` (SwiftPM) vs `AttributedString(markdown:)` — start with
    `AttributedString`, escalate only if code blocks/tables look poor.
  - tool activity → compact, collapsible chips (icon + tool name + short arg,
    e.g. `📄 Read transcript.md`), expandable to show input/result preview.
  - stage steps → a slim checklist header.
  - streaming caret while `text` deltas arrive.
- `AgentChatView` wraps it for Ask AI: scrollback of turns + input box + Stop.
- Auto-summary uses `AgentTranscriptView` read-only inside the existing pane.
- `cliPaneVisible` / `showCLIWhileSummarising` semantics unchanged.

### Keep the raw terminal for auth
`EmbeddedTerminalView` / `TerminalPaneController` stay for `claude auth login` and
power use. Add a "Open raw terminal" affordance. Only the AI summarise/ask
surfaces move to the formatted view.

## Windows parity (PyQt6) — required by CLAUDE.md
- The Python emitter (`llm_cli.py` + `summarize` events) is shared, so Windows
  reuses it unchanged.
- Build the Qt equivalent of `AgentTranscriptView`/`AgentChatView` (QTextBrowser
  for markdown + a tool-activity list). Mirror layout/behavior.
- Update `PARITY.md` with the new "Formatted AI view" row once both ship.

## Completed
- [x] Audited both flows + engine inventory; confirmed claude headless flags
      (`--print`, `--output-format stream-json`, `--input-format stream-json`,
      `--include-partial-messages`, `--resume`, `--json-schema`).
- [x] Confirmed `llm_cli.query_streaming` already parses claude stream-json —
      reuse as the normalization point.

## Planned (build order)
- [x] Define + document the normalized NDJSON event schema in `shared/`
      (`agent_events.py`, 0x1f-prefixed NDJSON on stderr).
- [x] Extend `llm_cli.query_streaming` to emit normalized events (claude full;
      others text-only). Unit tests (`test_agent_events.py`) with captured
      claude stream-json fixtures. 346 shared tests green.
- [x] `transcribe.py` / `transcribe_cpp.py` `summarize --events` → normalized
      stream. Verified end-to-end against real claude.
- [x] macOS: `AgentEvent` parser + `AgentTranscriptView` (MarkdownUI + tool
      chips + stages + usage footer); summary/reclassify flow wired to it.
- [x] macOS: `AgentChatView` + `ask` subcommand + `chat_streaming` (multi-turn
      via claude `--resume`, read-only tools). `runTranscription` gained stdin.
- [x] Engine fallbacks (codex/gemini/ollama): text-only via `chat_streaming` /
      `query_streaming` (no tool events).
- [x] Keep raw terminal for auth + template authoring; `openRawTerminalPane`.
- [x] macOS build succeeds (Debug), MarkdownUI 2.4.1 linked, app deployed.
- [ ] Windows: Qt `AgentTranscriptView`/`AgentChatView` equivalents (consume the
      same shared event stream).
- [ ] Update `PARITY.md`.
- [ ] Runtime GUI smoke (user): click Summarise / Ask AI in the running app.

## Decision log (in-build)
- Template create/iterate stay on the **raw terminal** — headless can't surface
  the interactive file-write approval those flows rely on. Only read-only Ask AI
  moved to the formatted chat.
- Event channel = **stderr**, 0x1f-prefixed NDJSON; stdout still carries the
  final result JSON unchanged (backward compatible).
- Ask AI runs with `--allowedTools Read,Grep,Glob` (read-only).

## Decisions (confirmed 2026-06-26)
- **Markdown lib: `swift-markdown-ui`** (SwiftPM dependency) — chosen for proper
  code blocks / tables / syntax highlighting.
- **Engine scope: all engines** — claude full detail, others text-only.
- **Build order: foundation-first** — shared Python event schema + `llm_cli`
  emitter (+tests) before any UI.

## Open questions / decisions to confirm
- Multi-turn for non-claude engines: re-send context each turn (simple) vs skip
  multi-turn for them. Lean re-send.
- Do we still want the saved `.md` summary opened in `SummaryViewerView` after a
  live readout, or is the readout enough? (Keep both for now.)

## Rejected / Not Applicable
- Parsing each engine's format natively in Swift/Qt — duplicates engine logic per
  platform. Rejected in favor of one Python normalizer.
- Fully replacing the SwiftTerm terminal — still needed for interactive auth.
