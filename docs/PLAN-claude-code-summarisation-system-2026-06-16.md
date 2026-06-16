# Claude-Code summarisation system — design + phasing

Date: 2026-06-16
Principle (user): **no API keys.** All LLM work rides the user's **Claude Code (`claude` CLI)**
login, via the existing pipeline + the embedded terminal.

## What already exists (build on, don't rebuild)
- `shared/summarize.py` — runs `claude` and extracts a structured summary (title, action_items,
  decisions, key_points, tags, summary_text); map-reduce for long transcripts. The prompt is
  currently **hard-coded** (`_SYSTEM_INSTRUCTION`).
- `shared/llm_cli.py` — detects + shells to `claude`/codex/gemini/ollama (no keys). Engine is now
  selectable (#26 config + #27 menu / `--summarize-engine`).
- Summarisation is **already auto** (the app always passes `--summarize`).
- `EmbeddedTerminalView` + `openTerminal(initialCommand:)` — a SwiftTerm login-shell terminal,
  purpose-built for Claude Code in-app.
- `RecordingsTableView` — rows with a context menu (`entryContextMenu`) and inline buttons; this is
  where a per-line button goes.

## Target architecture
1. **Templates** — named prompt templates, each: `name`, optional `match` (transcript types it
   applies to), `prompt` body, `fields` (output schema). User-editable in-app. The active/matched
   template's prompt replaces the hard-coded one in `summarize.py`.
2. **Type detection** — a small `claude` classification step tags the transcript type
   (meeting / interview / 1:1 / lecture / call / standup / …) → selects the matching template
   (fallback = a default template).
3. **Execution** — `summarize.py` runs `claude` with the chosen template's prompt. Two entry points:
   - **Auto** (on transcription complete; gated by an auto-summarise toggle).
   - **Manual** (per-row button: one-shot summary, or open interactive Claude Code on the transcript).
4. **UI** — per-row button on each recording; a **Templates editor** screen; an **auto-summarise**
   toggle; provider menu already shipped (#27).

## Phases (each independently buildable + testable)
1. **Row button + Claude Code actions.** Add a per-line button (and/or keep context-menu items):
   **Summarise** (one-shot via the pipeline) and **Ask Claude Code…** (opens the embedded terminal
   running `claude` on that transcript — interactive, your login). Uses the *current* prompt; no
   templates yet. Immediately testable.
2. **Template store + editor.** JSON store (e.g. `~/HiDock/summary_templates.json`, mirroring
   `merge_groups.json`/`imported_recordings.json`); a screen to create/edit templates;
   `summarize.py` accepts a `--template`/prompt and uses it.
3. **Type detection.** A `claude` classify pass → transcript type → auto-pick the matching template.
4. **Auto-summarise wiring.** A toggle; on transcription-complete run classify → template →
   summarise automatically (extends the existing summarize hook).

## Decisions to confirm
- **Template storage:** JSON in `~/HiDock/` (consistent with existing app-data files) vs the
  `config_store` TOML. Recommend JSON (richer, multi-entry, matches existing pattern).
- **Templates editor location:** a dedicated window (like Voice Library / Model Manager menu items)
  vs a section in the main window. Recommend a dedicated window for v1.
- **Row button scope for Phase 1:** a single "Summarise" button + a "⋯/Ask Claude Code" affordance,
  or just one. 
- **Start at Phase 1?** (Ships a testable per-line Claude Code button first.)

## Notes
- This is a layered build on the existing claude-CLI pipeline — no API keys, no new services.
- Each phase is its own PR; nothing speculative gets built without the phase before it proving out.

---

# REVISED PLAN (post-investigation, 2026-06-16)

Investigation finding: there are **two** summarisation systems. (A) the in-app Python pipeline
(auto, **flat**, one-size prompt → transcript frontmatter → knowledge graph). (B) **Claude Cowork**
(external scheduled agent) — produced the typed `DATE - TYPE - TITLE.md` files; its taxonomy +
template-selection live in `CoworkPromptView.swift:7-59` and the 14 templates in
`~/HiDock/Summary Templates/`. The app **never surfaces summaries** (`summaryPath` set-but-unused;
`findSummaryPath` matches mp3-basename, so it can't see the Cowork files).

**User decision: bring it in-app, run by Claude Code; the app writes to a folder Cowork/Obsidian
can map to.** So we don't depend on external Cowork.

## Reuse, don't rebuild
- **Taxonomy + classify + template-select + naming** already specified in `CoworkPromptView.swift`.
- **14 templates** already on disk in `~/HiDock/Summary Templates/` (each with "Extraction guidance").
- So the engine = **invoke `claude` with that same logic against one transcript**, write the typed
  summary to `~/HiDock/Summaries/`.

## Naming fix (elegant — no findSummaryPath change)
Name output `"<mp3-base> - <Template> - <Area> - <Desc>.md"`. Because `findSummaryPath` matches
`filename.contains(mpBase)`, the app **finds it**, it's human/typed, and the folder still maps to
Cowork/Obsidian. (Also persist mp3→summaryPath in state for robustness.)

## Revised phases
1. **Engine + per-line button + status + view.**
   - Engine: app runs `claude` (headless `-p` for one-shot/auto; embedded terminal for "Ask")
     with the Cowork-style prompt scoped to one transcript + the templates dir → writes the typed
     summary file. Reuses `shared/llm_cli.py`.
   - UI: per-row **Summarise** (one-shot) + **Ask Claude Code…** (interactive terminal); a
     **Summarised** status tier (+ colour) and transient **Summarising**; a **View Summary** action
     (the app currently never shows summaries at all).
2. **Auto-summarise** — toggle; on transcription-complete, run the engine (type+template).
3. **In-app template editing** — the templates already exist as `.md`; add an editor for
   `~/HiDock/Summary Templates/` (create/edit/duplicate).

Type detection (c) and templates (d) are now **reuse**, not new builds. Per-line button + surfacing
(a) is the genuinely-new UI. Flat auto (b) already runs (we may supersede it with the typed engine).
