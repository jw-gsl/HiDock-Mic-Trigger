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

## Status
Planned — **slice 5**, not started. Captured here per the user's 2026-06-17 question so it isn't lost.
