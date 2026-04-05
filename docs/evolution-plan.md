# Evolution Plan: From Hardware Tool to Knowledge System

**Date**: 2026-04-05
**Status**: Phases 1-3 backend complete. UI integration pending.

## Vision

Transform from "HiDock Mic Trigger" (a USB recording companion) into a **conversation intelligence platform** — a system that captures, understands, connects, and surfaces knowledge from every meeting and voice memo.

The hardware integration remains a differentiator, but the product identity shifts from the device to the knowledge it produces.

## Key Architectural Decisions

### 1. LLM Integration via CLI Subscriptions (No API Keys)

Instead of requiring users to set up API keys and pay per-token, leverage existing AI subscriptions through their installed CLI tools:

**Detection chain** (same pattern as minutes):
```
claude (Claude Pro/Max) → codex (OpenAI) → gemini → ollama (free, local)
```

**How it works**:
- Check `which claude`, `which codex`, `which gemini` at startup
- Pipe transcript text via stdin to avoid OS argument length limits
- Parse structured output (JSON) from stdout
- Fallback gracefully: if no CLI found, skip summarization (don't block the workflow)

**Why this is better than API integration**:
- Zero additional cost for users with existing subscriptions
- No API key management, no token counting, no billing surprises
- Users choose their own provider implicitly
- Ollama fallback means fully offline is always possible

**Implementation**: New `shared/summarize.py` module callable from both platforms.

### 2. Markdown with YAML Frontmatter as Source of Truth

**Current state**: Plain markdown transcripts + separate state.json for metadata.

**Target state**: Self-contained markdown files with YAML frontmatter. State.json becomes a processing status tracker only (not the metadata store).

```markdown
---
title: "Weekly sync with Sarah and Dev team"
type: meeting
date: 2026-04-05T14:00:00-07:00
duration: 2340
speakers:
  - Sarah Chen
  - James Walsh
  - Speaker_3
source_device: HiDock H1
source_file: 2026Apr05-140000-Rec87.mp3
action_items:
  - task: "Review Q2 roadmap draft"
    assignee: Sarah Chen
    due: 2026-04-10
    status: open
  - task: "Set up staging environment"
    assignee: James Walsh
    status: open
decisions:
  - text: "Ship v2.0 by end of April"
    topic: release
key_points:
  - "Budget approved for contractor hire"
  - "Moving standup to 10am starting next week"
open_questions:
  - "Who owns the API migration?"
tags: [engineering, planning]
---

## Transcript

[00:00-00:45] **Sarah Chen:** Let's start with the roadmap...
```

**Migration**: Existing transcripts get frontmatter added retroactively (date/duration from state.json, speakers from content parsing).

### 3. SQLite Knowledge Graph (Rebuildable Cache)

Following minutes' pattern: **Markdown files are the source of truth. SQLite is a derived, rebuildable index.**

```
~/[AppName]/knowledge.db

Tables:
- people (id, name, aliases)
- meetings (id, date, title, duration, path)
- meeting_people (meeting_id, person_id)
- action_items (id, meeting_id, assignee_id, task, due, status)
- decisions (id, meeting_id, text, topic)
- topics (id, name)
- meeting_topics (meeting_id, topic_id, depth)
```

Rebuild command: parse all markdown frontmatter → populate tables. Should take <1s for hundreds of meetings.

### 4. Obsidian Vault Integration

**Sync strategies** (user-configurable):
- **Symlink** (default): Zero duplication, instant sync, but won't work with iCloud/Dropbox
- **Copy**: Works with cloud sync, but duplicates files
- **Direct**: Write transcripts directly into the vault

**Obsidian-specific enhancements**:
- `[[wikilinks]]` for people names in transcripts (links to person note)
- Auto-generated person notes with meeting history, commitments, topics
- Daily notes integration (append meeting summaries to today's daily note)
- Tags in frontmatter that Obsidian indexes natively

---

## Phased Roadmap

### Phase 1: Structured Output (Foundation) — COMPLETE
**Goal**: Make transcripts machine-readable without changing the user workflow.

1. **YAML frontmatter on all new transcripts** — DONE
   - `shared/transcript_writer.py` — builds/parses YAML frontmatter
   - `transcribe.py`, `transcribe_cpp.py`, and Windows `core/transcription.py` all integrated
   - Fields: title, type, date, duration, speakers, source_device, source_file, action_items, decisions, key_points, tags
   - 22 tests

2. **Migrate existing transcripts** — DONE
   - `shared/migrate.py` — adds frontmatter to existing `.md` files using state.json metadata
   - CLI: `python -m shared.migrate --apply --rebuild-index`
   - 9 tests

3. **CLI detection module** — DONE
   - `shared/llm_cli.py` — detects `claude` > `codex` > `gemini` > `ollama`
   - Unified `query(prompt) → str` and `query_json(prompt) → dict` interface
   - Reads `ollama_model` from TOML config
   - 12 tests

### Phase 2: Intelligence Layer — COMPLETE (backend)
**Goal**: Extract meaning from transcripts, not just words.

4. **LLM summarization post-transcription** — DONE
   - `shared/summarize.py` — sends transcript to LLM, extracts structured JSON
   - `--summarize` flag on both `transcribe.py` and `transcribe_cpp.py`
   - Graceful skip if no LLM available
   - 8 tests

5. **Auto-titling** — DONE
   - `shared/transcript_writer.py:auto_title()` — first sentence with speaker labels stripped
   - Falls back to "Untitled recording"

6. **SQLite knowledge graph** — DONE
   - `shared/knowledge.py` — FTS5, people, action items, decisions, tags
   - Indexes both speakers (from diarization) and assignees (from action items)
   - CLI: `python -m shared.knowledge [rebuild|search|person|actions|stats|people]`
   - 16 tests

7. **Search** (backend only) — DONE
   - Full-text search via FTS5 with safe query escaping
   - Search by person, tag, text
   - **UI NOT YET BUILT** — needs search bar in recordings table (both platforms)

### Phase 3: Obsidian & Connectivity — COMPLETE (backend)
**Goal**: Make meeting knowledge part of the user's broader knowledge system.

8. **Obsidian vault sync** — DONE
   - `shared/obsidian.py` — 3 strategies (symlink, copy, direct)
   - Auto-sync via hooks pipeline after transcription
   - Wikilinks for speaker names
   - Person notes auto-generated in vault
   - 12 tests

9. **Person profiles** — DONE (backend)
   - `shared/knowledge.py:get_person_profile()` — meeting history, open action items, topics
   - Accessible via CLI and MCP server
   - **UI NOT YET BUILT** — needs person profile view in both apps

10. **Post-processing hooks** — DONE
    - `shared/hooks.py` — runs shell command with transcript metadata as env vars
    - Orchestrates: hook command + Obsidian sync
    - Config: `hooks.post_transcription` in TOML
    - 9 tests

11. **Action item dashboard** — NOT YET BUILT
    - Backend ready: `knowledge.py:list_action_items()` and `update_action_item_status()`
    - **Needs UI** — dedicated view in both macOS (Swift) and Windows (PyQt6)

### Phase 4: Agent Access & Beyond — PARTIALLY COMPLETE
**Goal**: Make the knowledge system accessible to AI agents.

12. **MCP server** — DONE
    - `mcp-server/server.py` — Python, JSON-RPC over stdio
    - 10 tools: search_meetings, get_meeting, get_recent_meetings, get_person_profile, list_people, list_action_items, search_by_person, search_by_tag, get_stats, rebuild_index
    - 1 resource: meetings://stats
    - 18 tests

13. **Cross-meeting intelligence** — NOT YET BUILT
    - Consistency detection (conflicting decisions across meetings)
    - Commitment tracking (stale action items, missed due dates)
    - Topic trends (what are you spending meeting time on?)
    - Relationship scoring (frequency x recency x topic depth)

14. **Calendar integration** — NOT YET BUILT
    - Match recording timestamps to calendar events
    - Pull attendee lists and meeting titles
    - Auto-populate speaker identification hints

---

## Rebrand Considerations

The name needs to convey "conversation intelligence" not "USB hardware tool."

**Requirements**:
- Works for both platforms (macOS + Windows)
- Doesn't reference HiDock (hardware-agnostic future)
- Suggests understanding/memory/knowledge, not just recording
- Short, memorable, available as a domain

**Naming directions to explore**:
- *Recall* — conversation memory (but common word, SEO hard)
- *Threads* — conversations connected over time (but GitHub Threads exists)
- *Convo* — short for conversations (but informal)
- *Parlor* — where conversations happen (distinctive)
- *Chronicle* — recording + history (but overused)
- *Vault* — where knowledge is stored (but Obsidian uses this)
- *Cartographer* — mapping conversations (too long)
- The user should decide — these are just starting points

**Rebrand scope**:
- App name, bundle ID, window title
- Menu bar icon / tray icon
- Folder paths (`~/HiDock/` → `~/[NewName]/`)
- GitHub repo name
- CI/CD workflow references
- Migration script for existing users (move `~/HiDock/` → `~/[NewName]/`, update state.json paths)

---

## Configuration Evolution

Move from scattered UserDefaults/Registry to a unified config file:

```toml
# ~/.config/[appname]/config.toml

[general]
recordings_folder = "~/[AppName]/Recordings"
transcripts_folder = "~/[AppName]/Transcripts"
appearance = "auto"  # auto | light | dark

[transcription]
model = "large-v3-turbo"
diarization = true
voice_library = true

[summarization]
engine = "auto"  # auto | claude | codex | gemini | ollama | none
ollama_model = "llama3.2"
custom_command = ""  # optional: path to custom CLI

[obsidian]
enabled = false
vault_path = ""
sync_strategy = "symlink"  # symlink | copy | direct
subfolder = "Meetings"
wikilinks = true
daily_notes = false

[hooks]
post_transcription = ""  # shell command, e.g. "notify-send 'Transcription done'"

[knowledge]
losing_touch_days = 21
stale_action_item_days = 14
```

**Precedence**: Compiled defaults → TOML file → UI settings panel (writes back to TOML)

Both platforms read the same TOML file. macOS UserDefaults and Windows Registry become legacy, with a one-time migration.

---

## What Stays the Same

- **HiDock USB integration** — remains a first-class feature, just not the product identity
- **Whisper.cpp transcription** — local, offline, proven
- **Voice library / speaker identification** — already competitive with minutes
- **Cross-platform architecture** — macOS Swift + Windows PyQt6
- **Shared Python modules** — extended, not replaced
- **Model management UI** — already better than minutes

## What Changes

| Before | After |
|--------|-------|
| Transcripts are the end product | Transcripts are raw material for intelligence |
| State.json holds all metadata | YAML frontmatter in markdown (self-contained) |
| No post-transcription processing | LLM summarization via CLI subscriptions |
| Files sit in a folder | Indexed in SQLite, synced to Obsidian, exposed via MCP |
| UserDefaults + Registry | Unified TOML config |
| "HiDock Mic Trigger" | [New name] — a conversation intelligence system |
| Hardware-first identity | Knowledge-first identity (hardware is a feature, not the product) |

---

## Dependencies & Risks

| Risk | Mitigation |
|------|-----------|
| LLM CLI availability varies | Graceful degradation: everything works without LLM, just less enriched |
| Ollama setup friction on Windows | Provide clear setup guide; Ollama has a Windows installer now |
| TOML config migration | One-time migration script; keep reading legacy UserDefaults as fallback |
| Obsidian vault path varies | Auto-detect common locations; user configures in settings |
| Rebrand breaks existing installs | Migration script moves folders, updates paths, preserves all data |
| Frontmatter parsing complexity | Use established library (python-frontmatter) — well-tested |
| MCP server maintenance | Keep it read-only initially; low maintenance surface area |

---

## Success Metrics

After Phase 2, the app should answer:
- "What did we decide about X?" → Search finds it
- "What am I supposed to do?" → Action items dashboard shows it
- "When did I last talk to Sarah?" → Knowledge graph knows it

After Phase 4, an AI agent should be able to answer all of the above on the user's behalf.
