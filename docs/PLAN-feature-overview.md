# Complete Feature Overview & Test Status

Last updated: 2026-04-12

---

## Core Application (on main, deployed)

### Recording Management
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| App launch with cached recordings | #1 | ❌ | |
| HiDock auto-connect | #5 | ❌ | |
| Full recording list (200+ with pagination) | #5 | ❌ | |
| Column sorting (7 columns) | #6 | ❌ | |
| Device filter | #7 | ❌ | |
| Hide Downloaded toggle | #8 | ❌ | |
| Shift+click range select | #9 | ❌ | |
| Skip/Unskip recordings | #10-11 | ❌ | |
| Download selected/new/stop | #12-13 | ❌ | |
| Auto-download toggle | #14 | ❌ | |
| Auto-transcribe toggle | #15 | ❌ | Had bugs, fixed |
| Choose recordings/transcript folders | #16 | ❌ | |
| Offline mode (HiDock disconnected) | #56 | ❌ | |

### Transcription
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| Stage-based progress (1/5 → 5/5) | #17 | ❌ | |
| Download → transcribe chain | #18 | ❌ | |
| Transcribe All | #19 | ❌ | |
| Re-transcribe alert | #20 | ❌ | |
| Queue (multiple recordings) | #21 | ❌ | |
| Queue pause/resume | #22 | ❌ | |
| Queue cancel | #23 | ❌ | Fixed timer bug |
| Queue remove/reorder | #24 | ❌ | |

### Transcript Viewer
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| In-app viewer opens | #25 | ❌ | |
| Stats header (talk %, wpm, bar) | #26 | ❌ | NEW |
| Non-diarized view | #27 | ❌ | |
| Audio playback per segment | #28 | ❌ | |
| Rename speakers | #29 | ❌ | |
| Merge speakers (right-click) | #30 | ❌ | |
| Undo merge (Cmd+Z) | #31 | ❌ | |
| Re-diarize with speaker count | #32 | ❌ | |
| Copy All (Cmd+Shift+C) | #33 | ❌ | NEW |
| Show File (reveal .md in Finder) | #34 | ❌ | NEW |

### Audio Editing
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| Trim (save as copy) | #35 | ❌ | Never tested |
| Trim (replace original) | #36 | ❌ | Never tested |
| Merge recordings | #37 | Partial | Works but UI refinements |
| Merge expandable tree view | #38 | ❌ | NEW |
| Merge row transcription | #39 | ❌ | |
| Re-merge (no duplicates) | #40 | ❌ | |
| Context menu (all actions) | #42 | ❌ | |

### Diarization Quality
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| Audio normalization | #43 | ✅ | Batch verified |
| 30s segment cap | #44 | ✅ | Batch verified |
| Speaker detection | #45 | ✅ | Batch verified |
| Whisper micro-segments saved | #46 | ✅ | Batch verified |

### Mic Trigger
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| Start/stop | #2 | ❌ | Used daily but not formally tested |
| Auto-start toggle | #3 | ❌ | |
| Mic selection + fallback | #4 | ❌ | |

### Device Manager
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| View/forget/re-pair | #52 | ❌ | |
| Volume device (USB recorder/SD) | #53 | ❌ | |

### Preferences & Settings
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| Appearance (dark/light/auto) | #54 | ❌ | |
| Notification preferences | #55 | ❌ | |
| Corrections dictionary | #47 | ❌ | No UI — manual JSON edit |

### Feedback & Updates
| Feature | Test Plan | Tested? | Notes |
|---------|-----------|---------|-------|
| Submit feedback (GitHub) | #57 | ❌ | |
| Feedback history (search/filter) | #58 | ❌ | |
| Check for updates | #59 | ❌ | |
| Onboarding wizard | #61 | ❌ | |
| Cowork prompt | #60 | ❌ | |

---

## Intelligence Layer (on main, backend only)

These features were built as backend Python modules but have **NO UI integration**.
They work via CLI or MCP server but are invisible to the app user.

### LLM Summarization
**Module:** `shared/summarize.py` + `shared/llm_cli.py`
**What it does:** Sends transcript to an LLM (claude/codex/gemini/ollama), extracts:
- Title, action items, decisions, key points, tags
- Map-reduce for long transcripts (splits into chunks)
- Confidence levels on extracted items
**CLI:** `transcribe.py transcribe --summarize`
**Status:** Backend complete, 20 tests. **No UI to trigger or view summaries.**
**Test plan:** #64 (optional)

### Knowledge Graph
**Module:** `shared/knowledge.py`
**What it does:** SQLite index over all transcript frontmatter:
- Full-text search (FTS5) across all meetings
- Person profiles (meeting history, action items, topics)
- Action item tracking (open/closed/overdue)
- Tag-based search
- Meeting statistics
**CLI:** `python -m shared.knowledge [rebuild|search|person|actions|stats|people]`
**Status:** Backend complete, 16 tests. **No UI to search or browse.**
**Test plan:** #63

### Obsidian Vault Sync
**Module:** `shared/obsidian.py`
**What it does:** Syncs transcripts into an Obsidian vault:
- `[[wikilinks]]` for speaker names
- Auto-generated person notes
- Daily notes integration
- 3 sync strategies (symlink, copy, direct)
**Config:** `hooks.post_transcription` in TOML
**Status:** Backend complete, 12 tests. **No UI to configure or trigger.**
**Test plan:** #65 (if configured)

### Cross-Meeting Intelligence
**Module:** `shared/intelligence.py`
**What it does:** Analyses relationships across meetings:
- Relationship scoring (frequency × recency × topic depth)
- Consistency reports (conflicting decisions)
- Commitment tracking (stale action items)
**Status:** Backend scaffolded. **Not wired into pipeline, no UI.**
**Test plan:** Not in plan

### MCP Server
**Module:** `mcp-server/server.py`
**What it does:** Exposes knowledge graph to AI agents (Claude Desktop, Cursor):
- 10 tools: search, get meeting, person profile, action items, etc.
- JSON-RPC over stdio
**Status:** Backend complete, 18 tests. **Not documented for users.**
**Test plan:** #66 (if configured)

### Whisper-Guard
**Module:** `shared/whisper_guard.py`
**What it does:** Anti-hallucination filtering:
- Consecutive/interleaved dedup
- Foreign-script filter
- Noise marker collapse
- Trailing noise trim
**Status:** Integrated into transcription pipeline. **Working silently.**
**Test plan:** #62

### Post-Transcription Hooks
**Module:** `shared/hooks.py`
**What it does:** Runs user-configured shell commands after transcription.
**Status:** Backend complete, 9 tests. **No UI to configure.**

### Event Logging
**Module:** `shared/event_log.py`
**What it does:** Structured event log for diagnostics.
**Status:** Integrated. Working silently.

### Health Check
**Module:** `shared/health_check.py`
**What it does:** Diagnoses config, storage, runtime issues.
**Status:** Backend complete. **No UI.**

### Config Store
**Module:** `shared/config_store.py`
**What it does:** TOML-based cross-platform settings.
**Status:** Backend complete. **App uses UserDefaults/QSettings instead.**

---

## Feature Branch (not merged)

### Voice Training (`feature/voice-training`)
- Voice Training window with cross-meeting speaker clustering
- Smart sample selection (best 5-15s clips)
- Per-sample reassignment, review state persistence
- Voice library auto-matching wired into diarization
- Non-speech event anonymization
- Silence stripping before Whisper
- Configurable embedding models (TitaNet + CAM++)
- Post-meeting nudges
**Status:** Built, untested, not merged

---

## Biggest Features Outside Core Application

### 1. Knowledge System (search, browse, connect meetings)
**What exists:** SQLite knowledge graph with FTS5, person profiles, action items, tags.
**What's missing:** ALL UI. No way for the user to search across meetings, see person profiles, track action items, or browse topic trends from within the app.
**Impact:** This is the "openbrain" — turning 72 transcripts into a searchable, connected knowledge base.

### 2. LLM Summarization (AI-generated meeting summaries)
**What exists:** Backend that extracts titles, action items, decisions, key points.
**What's missing:** UI to trigger summarization, view/edit summaries, and a settings panel for LLM engine selection.
**Impact:** Transforms raw transcripts into structured intelligence.

### 3. Cross-Meeting Intelligence (relationship tracking)
**What exists:** Scaffolded module for relationship scoring, consistency detection.
**What's missing:** Everything — not wired into pipeline, no UI, no data flowing.
**Impact:** "What did I promise Sarah last week?" "Are there conflicting decisions?"

### 4. Obsidian Integration (knowledge vault sync)
**What exists:** Full backend — sync, wikilinks, person notes.
**What's missing:** UI to configure vault path, sync strategy, trigger manual sync.
**Impact:** Bridges meeting knowledge into the user's broader note-taking system.

### 5. MCP Server (AI agent access)
**What exists:** Full server with 10 tools.
**What's missing:** User documentation, setup wizard, integration testing with Claude Desktop.
**Impact:** "Ask Claude about my meetings" — the ultimate interface.

### 6. Calendar Integration (not built)
**What exists:** Nothing.
**What's missing:** Match recordings to calendar events, auto-populate meeting titles and attendees.
**Impact:** Eliminates manual speaker tagging for known meetings.

---

## Windows Parity Gaps

| Feature | macOS | Windows |
|---------|-------|---------|
| Voice Training window | ✅ (feature branch) | ❌ |
| Transcription queue dialog | ✅ Full | ⚠️ Basic |
| Mic preferred/fallback | ✅ | ❌ (CoreAudio-only) |
| Audio playback in viewer | ✅ | ✅ |
| Speaker merge + undo | ✅ | ✅ |
| Re-diarize | ✅ | ✅ |

---

## CI/Automated Tests

| Suite | Tests | Status |
|-------|-------|--------|
| usb-extractor | 103 | ❓ Not verified in CI |
| shared | 326 | ❓ Not verified in CI |
| transcription-pipeline | 56 | ❓ Not verified in CI |
| Swift (XCTest) | 2 files | ❓ Not verified in CI |
| **Total** | **485+** | **Need CI verification** |
