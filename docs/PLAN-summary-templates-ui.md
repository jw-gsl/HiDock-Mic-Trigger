# Summary Templates UI / Management

Research date: 2026-06-17
Sources: `shared/typed_summarize.py` (`available_templates()`, `classify()`, `summarise_typed()`), `~/HiDock/Summary Templates/*.md` (14 templates with "Extraction guidance"), CoworkPromptView 12-type taxonomy, embedded CLI pane (slice 4 / PLAN-embedded-terminal-pane.md).

## Current State
- Typed summaries read templates from `~/HiDock/Summary Templates/*.md`; `available_templates()` strips the emoji prefix; `classify()` picks one per recording; `summarise_typed()` applies it and writes `~/HiDock/Summaries/<stem> - <Type> - <Area> - <Desc>.md`.
- **There is no in-app way to view, import, create, edit, or have Claude Code iterate templates.** Users must edit the markdown files in the folder by hand. (Confirmed 2026-06-17 — this was an explicit user question.)

## Goal (user, 2026-06-17)
A templates management UI/mechanism so the user can:
1. **Import** templates (drop in / pick a .md → copy into the Templates folder).
2. **Have Claude Code iterate** a template (refine the extraction guidance) and **save** the result back.
3. (Implied) View/list existing templates and edit them.

## Findings / design sketch (to be confirmed before building)
- A "Templates" manager window/section listing `available_templates()` with view/edit/duplicate/delete.
- **Import:** file picker → copy into `~/HiDock/Summary Templates/`.
- **Claude Code iterate:** reuse the embedded CLI pane (slice 4) — open it `cd`'d into the Templates folder with a prompt like "improve <template>.md's extraction guidance"; the user reviews and saves in-pane. This keeps the no-API-keys contract (Claude Code CLI only).
- **Save:** templates are just .md files; writing is a local file op. New/edited templates are picked up automatically by `available_templates()` on the next summarise.

## Open decisions (ask before building)
- [ ] Separate window vs. a section in an existing manager (Models/Voice Library live as separate windows — likely a "Templates" window to match).
- [ ] In-app markdown editor vs. "edit via Claude Code in the CLI pane" vs. "Reveal in Finder / open in default editor". Cheapest faithful option: list + Import + "Iterate with Claude Code" (pane) + Reveal in Finder, deferring a full in-app editor.
- [ ] Whether to seed/restore the default 14 templates if the folder is empty.

## Decided + built (2026-06-17, slice 5)
- **Lean option chosen** (no in-app markdown editor). `TemplatesManagerView` (separate window, matching Models/Voice Library) lists `~/HiDock/Summary Templates/*.md` with:
  - **Import…** (NSOpenPanel → copy into the folder, de-duped name).
  - **New via Claude Code** + per-row **Iterate** → opens the embedded CLI pane `cd`'d into the folder with a Claude Code prompt (no API keys); brings the main window forward so the pane is visible.
  - Per-row **Reveal in Finder** / **Open in Editor** / **Delete** (confirm).
- All file ops are native Swift (FileManager / NSOpenPanel) — no Python round-trip needed; `available_templates()` picks up new/edited files on the next summarise.
- Footer **Templates** button opens the window.

### Also shipped in slice 5 — full streaming summarise output
- `shared/llm_cli.query_streaming()`: for `claude`, runs `--print --output-format stream-json --include-partial-messages --verbose` via Popen, forwards `content_block_delta` text to an `on_text` callback live, returns the authoritative `result` text. Other engines fall back to a single blocking `query()`.
- `summarise_typed()` now streams: emits `STAGE:` markers + Claude's live output to **stderr**, and uses a headered text response (`AREA:`/`TITLE:`/`---`/markdown) so the streamed output is human-readable (structured fields recovered from the header). stdout stays clean JSON for the caller.
- Swift: `runTranscription` gained an `onLine` callback; `processNextSummary` routes the summarise subprocess's stderr lines into the CLI pane, so Claude's summary streams in live (STAGE markers rendered as `› …`).

## Status
**Built (slice 5)** — pending build/deploy verification + PR.
