# Gap Analysis: HiDock Mic Trigger vs. silverstein/minutes

**Date**: 2026-04-05
**Comparison**: [HiDock Mic Trigger](https://github.com/jw-gsl/hidock-mic-trigger) vs. [minutes](https://github.com/silverstein/minutes)
**Status**: Actioned — see [evolution-plan.md](evolution-plan.md) for implementation status

## The Fundamental Difference

**HiDock** is a **hardware companion tool** — it manages a specific USB device (HiDock dock/recorder), downloads recordings, transcribes them, and identifies speakers. It's tightly coupled to the HiDock hardware workflow.

**minutes** is a **conversation memory system** — it captures any audio (any mic, system audio, calls), transcribes locally, summarizes with LLMs, builds a knowledge graph of people/commitments/decisions, and exposes everything to AI agents via MCP. The hardware is incidental; the *knowledge* is the product.

**HiDock stops at transcription. minutes starts there.**

---

## Critical Gaps (High Impact)

### 1. No LLM-Powered Post-Processing

| | HiDock | minutes |
|---|---|---|
| **What happens after transcription** | Markdown file. Done. | LLM summarizes → extracts action items, decisions, open questions, commitments, key points, participants |
| **LLM support** | None | Claude, OpenAI, Mistral, Ollama (fully local option) |

Raw transcripts are rarely useful. People want "what did we decide?" and "what am I supposed to do?" — not 45 minutes of verbatim text. This is the single biggest value-add HiDock is missing.

**Recommendation**: Add a summarization step after transcription. Use Claude API or Ollama for local. Extract structured data: action items (with assignees), decisions, key topics. Store as YAML frontmatter in existing Markdown files.

### 2. No Agent/MCP Integration

| | HiDock | minutes |
|---|---|---|
| **Accessibility** | Files sit in `~/HiDock/Raw Transcripts/` | 25+ MCP tools, 7 resources. Users ask Claude "what did I promise Sarah?" |
| **Queryability** | Manual file browsing only | Full agent integration (Claude Desktop, Codex, Gemini, Cursor, etc.) |

MCP is becoming the standard way AI assistants access external data. Without it, transcripts are siloed — useful only if someone manually opens them. With MCP, every past meeting becomes queryable context.

**Recommendation**: Build an MCP server that indexes `~/HiDock/Raw Transcripts/` and exposes search, person lookup, and meeting retrieval tools. The `minutes-sdk` approach (TypeScript, reads Markdown files) shows this doesn't need to be complex.

### 3. No Structured Metadata Extraction

| | HiDock | minutes |
|---|---|---|
| **Transcript format** | Markdown with optional speaker labels | YAML frontmatter: title, type, date, duration, attendees, action_items (assignee + status + due), decisions, intents, entities, tags, visibility |

Structured data enables search, filtering, dashboards, commitment tracking, and agent queries. Without it, transcripts are text blobs.

**Recommendation**: Add YAML frontmatter to Markdown transcripts. Even basic metadata (date, duration, speakers, auto-generated title) is a major improvement.

### 4. No Relationship/Knowledge Graph

| | HiDock | minutes |
|---|---|---|
| **Cross-meeting intelligence** | None | SQLite graph: people, meetings, commitments, topics. Relationship scoring with recency decay. "Losing touch" alerts. Stale commitment tracking. Consistency reports. |

This is what transforms a transcription tool into "conversation memory." Individual meetings are useful; patterns across meetings are powerful.

**Recommendation**: Start simple — SQLite index of speakers across transcripts, meeting frequency and recency. Even "you've met with Sarah 12 times, last on March 15" adds value.

### 5. No Real-Time/Live Transcription

| | HiDock | minutes |
|---|---|---|
| **Transcription timing** | Batch only (record → download → transcribe later) | Streaming JSONL during recording. Partial results in real-time. |

Real-time transcription during meetings is increasingly expected. Enables live captioning and immediate post-meeting output.

**Note**: This is architecturally harder for HiDock since recordings come from the device, not a live mic stream. But for the mic trigger workflow, stream-transcription of the mic input could work in parallel.

### 6. No Dictation Mode

| | HiDock | minutes |
|---|---|---|
| **Dictation** | None | Model preloading, streaming partial results, clipboard output, accumulation across pauses, hotkey (Caps Lock) |

Natural extension of having Whisper already loaded. High-utility, low-cost feature that increases daily engagement.

---

## Moderate Gaps (Medium Impact)

### 7. No Screen Context Capture
minutes captures periodic screenshots during recording and feeds them to vision-capable LLMs during summarization. Less applicable for HiDock's USB device recordings, but valuable for mic trigger (meeting) workflows.

### 8. No Calendar Integration
minutes reads Calendar.app/CalDAV to auto-associate recordings with calendar events, pull attendee lists, detect meeting URLs. Even basic calendar matching (timestamp → overlapping event → auto-title) would improve navigation.

### 9. No Call-Aware Recording
minutes detects active Zoom/Teams/Webex calls, captures dual sources (voice + system audio) for energy-based speaker attribution without ML diarization. Simpler and more reliable than ML diarization for 2-party calls.

### 10. No Search Across Transcripts
HiDock has no search. minutes has full-text regex, semantic vector search, intent-filtered (action items, decisions), person-filtered, and date-filtered search. A SQLite FTS5 index over Markdown files would be high-value, low-effort.

### 11. No Post-Record Hooks
minutes supports arbitrary shell commands after processing. A "run this command after transcription" setting enables power users to integrate with existing workflows (Notion, Slack, etc.).

### 12. No Vault/PKM Sync
minutes syncs to Obsidian and Logseq (symlink, copy, or direct). Many knowledge workers live in Obsidian — syncing transcripts with YAML frontmatter makes recordings part of their knowledge system.

---

## Where Both Think Similarly

| Area | HiDock | minutes | Verdict |
|------|--------|---------|---------|
| **Local-first transcription** | whisper.cpp, no cloud | whisper.cpp, no cloud | Same philosophy |
| **Speaker diarization** | Silero VAD + TitaNet clustering | pyannote-rs ONNX | Similar approach; minutes uses more mature pyannote models |
| **Voice enrollment** | Cosine similarity, JSON storage | Cosine similarity, SQLite storage | Nearly identical concept |
| **Offline capability** | All processing local | All processing local (except optional summarization) | Same |
| **VAD** | Silero ONNX | Silero v6.2.0 | Same model |

---

## Where HiDock Is Actually Better

1. **Hardware integration** — USB protocol implementation, device pairing, recording download. minutes has nothing comparable. This is HiDock's moat.
2. **Cross-platform UI parity** — macOS + Windows with explicit porting guides. minutes is macOS-primary with minimal Windows.
3. **Onboarding UX** — 5-step wizard is more polished than minutes' CLI-first setup.
4. **Model management UI** — In-app model browser with download/delete/status per model is more user-friendly.
5. **Windows support** — Real PyQt6 desktop app. minutes' Windows story is weak.
6. **Visual polish** — Menu bar app with device icons, progress bars, table views is more accessible to non-technical users.

---

## Where minutes Thought Differently

| Design Choice | HiDock | minutes | Assessment |
|---------------|--------|---------|------------|
| **Data format** | JSON state files + Markdown | Markdown with YAML frontmatter (source of truth) + SQLite (rebuildable cache) | minutes — Markdown-as-truth is more portable and grep-friendly |
| **Architecture** | Swift + Python subprocesses | Rust monolith with TypeScript SDK | minutes — single binary is simpler to deploy; HiDock's approach is more flexible for prototyping |
| **Speaker ID storage** | JSON embeddings file | SQLite with 0600 permissions | minutes — SQLite scales better; permissions show security awareness for biometric data |
| **Config** | UserDefaults/Registry | TOML file with CLI flag overrides | minutes — TOML is portable and version-controllable |
| **Recording safety** | Basic error handling | Escalating silence reminders, disk space monitoring, 8hr time cap, device reconnection | minutes — much more defensive |
| **Transcript format** | Plain or speaker-labeled Markdown | Structured Markdown with YAML frontmatter schema | minutes — frontmatter enables machine-readable metadata |

---

## Prioritized Recommendations

### Tier 1 — High impact, aligns with existing architecture
1. **Add LLM summarization** after transcription (Claude API or Ollama). Extract action items, decisions, key points. Store in YAML frontmatter.
2. **Add full-text search** across transcripts (SQLite FTS5 index).
3. **Add YAML frontmatter** to transcript Markdown files (date, duration, speakers, title, source device).

### Tier 2 — Medium effort, significant differentiation
4. **Build an MCP server** that exposes transcripts to AI assistants.
5. **Add post-transcription hooks** (shell command execution).
6. **Add basic relationship tracking** (speaker frequency across meetings).

### Tier 3 — Larger effort, strategic
7. **Calendar integration** for auto-titling recordings.
8. **Obsidian/Logseq vault sync**.
9. **Dictation mode** leveraging already-loaded Whisper model.
10. **Commitment/action item tracking** across meetings.

---

## Bottom Line

**minutes is playing a different game.** It's not a better transcription tool — it's a conversation *memory* and *intelligence* system that happens to include transcription. The key insight: **raw transcripts have almost zero recall value; structured, searchable, agent-queryable meeting knowledge has enormous value.**

HiDock's hardware integration and cross-platform polish are genuine strengths minutes doesn't have. But the gap between "I have a transcript file" and "I can ask an AI what I committed to last week" is where the real value lives. The Tier 1 recommendations (summarization, search, structured metadata) would close the biggest gaps with relatively modest effort and make the existing hardware workflow dramatically more useful.
