# Provenance of the 18 "orphaned" summaries

Investigation date: 2026-06-20
Trigger: 18 summaries in `~/HiDock/Summaries/` had no matching recording in the
app. Established they are *real* (transcripts confirm candidate names / specific
interviewer detail), then asked: which tool created them, and when?

## The hard fact
All 18 files: **birth time == modified time == 2026-03-12, 17:14–17:23**.
So they were genuinely *created* (not copied/moved) on 12 March 2026, late
afternoon. They are typed interview summaries:
`2024-12-19 - Job Interview - Recruitment - <Name> Product Specialist Round 1.md`
(Anika, Asha, Reyes, …).

## Ruled out — every local tool we can check
| Candidate | Verdict | Evidence |
|---|---|---|
| **`_git/hidock-tools` repo app/scripts** (the app *I* built) | ❌ | Summarise feature first committed **5 Apr 2026** (`3bf53fd`, `73ca07d`). Did not exist on 12 Mar — three weeks too late. |
| **Installed device HiDock app** (manufacturer's) | ❌ | Does no AI summarisation; its log covers the period with no creation entries. |
| **Claude Code CLI** | ❌ | Earliest session on this machine: 20 May 2026. |
| **Codex CLI** | ❌ | Only 12-Mar session ran **16:27–16:31** (ended 43 min before creation) and was a `SKILL.md`/weekly-update authoring session (26× `SKILL.md`). Codex's *first contact* with these summary files is **26 Mar** — it **read** already-existing files; later sessions (26 Mar, 7 Apr) refine them. |
| **Cursor** | ❌ | `~/.cursor` has only extensions + argv.json; no chat/composer/workspace history at all. |
| **Anything running at 17:14–17:23 on 12 Mar** | ❌ | No Codex/agent session has internal activity in that window. |

## Important clarification (from James)
Two different things are called "HiDock":
1. the **installed device app** (manufacturer's) — does no summaries; and
2. the **`_git/hidock-tools` app/scripts James built**.
`~/HiDock/Summaries/` is just a *data folder* in the shared file structure —
neither app "owns" it. The repo app could not have written these because the
feature postdates them by three weeks (above).

## Conclusion (calibrated)
No AI-agent log on this machine accounts for the 12 Mar 17:14–17:23 creation.
What it is **not** is now well established (none of the above). The remaining
realistic explanation — for which there is **no recoverable local forensic
trail** — is a tool that leaves no CLI log: most likely a **browser LLM
(ChatGPT / Claude.ai web)** where the interview transcripts were pasted and the
outputs saved into `~/HiDock/Summaries/` by hand, or a one-off throwaway script
never committed to the repo. Stated as the honest limit of the evidence, not a
guess dressed as fact.

## Disposition
The 18 files were archived (reversibly) to
`~/HiDock/Summaries Archive (orphaned 2026-03-12)/`; `~/HiDock/Summaries/` now
holds only the 2 summaries linked to current recordings. Nothing was deleted —
they remain recoverable if we later want to relink or import them.
