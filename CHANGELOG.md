# Changelog

HiDock Mic Trigger for macOS. Version numbers: the **first** number marks a big change in what the app does, the **second** a group of features, and the **third** each change merged (one pull request each).

Releases before 4.1 were written up on 15 Oct 2026 from the merged pull requests. Every version is tagged at the commit it came from, with that change's original date.

## 4.1 — Settings window

Released 15 Oct 2026 as v4.1.8.

- A Settings window (⌘,) holds every setting; the footer is down to five buttons.
- A setup check flags anything missing, with how to fix it.
- Moving to a new Mac is smoother: there's a guide, and the problems it hit are fixed.
- The LED ticker works, and older recordings can be left out of "to tag".

<details><summary>Every change (9)</summary>

| Version | Date | Change |
|---|---|---|
| v4.1.0 | 15 Oct 2026 | Fix TitaNet install on a fresh Mac (#121) |
| v4.1.1 | 15 Oct 2026 | Catch a half-migrated or new Mac before it costs a transcript (#122) |
| v4.1.2 | 15 Oct 2026 | Migration guide: code-signing access on a new Mac (#124) |
| v4.1.3 | 15 Oct 2026 | Let old untagged recordings stop counting towards 'to tag' (#126) |
| v4.1.4 | 15 Oct 2026 | Trim the transcript along with the audio (#123) |
| v4.1.5 | 15 Oct 2026 | One Settings window for every setting; footer down to five controls (#127) |
| v4.1.6 | 15 Oct 2026 | Move voice-library samples to whoever the transcript says is speaking (#125) |
| v4.1.7 | 15 Oct 2026 | Settings polish: one scroll per pane, tidy Models and Devices; make the LED ticker work (#129) |
| v4.1.8 | 15 Oct 2026 | Models pane: expand rows with a chevron; models in use open by default (#130) |

</details>

## 4.0 — Smarter matching and Refine

Released 13 Oct 2026 as v4.0.28.

- Publish transcripts to a private GitHub repository.
- Nemotron speaker detection on an NVIDIA DGX Spark.
- Calendar matching picks the right meeting, and confirming it is instant.
- Refine checks every line against your saved voices and learns from your decisions.

<details><summary>Every change (29)</summary>

| Version | Date | Change |
|---|---|---|
| v4.0.0 | 12 Oct 2026 | Publish transcript .md files to the private Transcripts GitHub repo (#91) |
| v4.0.1 | 12 Oct 2026 | Nemotron 3 Diarization: remote backend + hardware-ownership gate (#92) |
| v4.0.2 | 12 Oct 2026 | Keep surname-first calendar invitees as one person (#94) |
| v4.0.3 | 12 Oct 2026 | Don't force a speaker count the reviewer didn't choose (#95) |
| v4.0.4 | 13 Oct 2026 | Warn when calendar lookups can't reach the calendar, with a terminal fix (#93) |
| v4.0.5 | 13 Oct 2026 | Stop re-diarisation collapsing speakers and overwriting tags (#96) |
| v4.0.6 | 13 Oct 2026 | Pick the dominant meeting when a recording overlaps several (#97) |
| v4.0.7 | 13 Oct 2026 | Diarise against the suggested meeting while transcribing (#98) |
| v4.0.8 | 13 Oct 2026 | Sidecar calendar check picks the best fit; confirm waits for the saved meeting (#99) |
| v4.0.9 | 13 Oct 2026 | Only learn a voice clip that sounds like the confirmed person (#101) |
| v4.0.10 | 13 Oct 2026 | Refine checks each line against the meeting's saved voices (#100) |
| v4.0.11 | 13 Oct 2026 | Clean up speaker-work progress and messages (#102) |
| v4.0.12 | 13 Oct 2026 | Unsigned and test builds never prompt for or take over Keychain items (#103) |
| v4.0.13 | 13 Oct 2026 | Calendar confirm advice uses the neutral info style (#104) |
| v4.0.14 | 13 Oct 2026 | Bring the transcripts-sync plan up to date (#105) |
| v4.0.15 | 13 Oct 2026 | Ignore local Obsidian settings and screenshots (#106) |
| v4.0.16 | 13 Oct 2026 | Show Refine's suggestions in the sidecar, not a popover (#107) |
| v4.0.17 | 13 Oct 2026 | Tidier sidecar: reviewable Refine suggestions, Ask behind a button, one transcript at a time (#108) |
| v4.0.18 | 13 Oct 2026 | Unsigned and test builds don't touch Keychain items at all (#109) |
| v4.0.19 | 13 Oct 2026 | Wait up to 10 minutes for deploy approval (#110) |
| v4.0.20 | 13 Oct 2026 | Refine suggestions: stop button, and playing a line shows it in the transcript (#111) |
| v4.0.21 | 13 Oct 2026 | Refine applies survive edits; playing a suggestion no longer needs two clicks (#112) |
| v4.0.22 | 13 Oct 2026 | Learn from review decisions and measure how well matching does (#113) |
| v4.0.23 | 13 Oct 2026 | Tune matching from review decisions automatically; show it in Settings (#114) |
| v4.0.24 | 13 Oct 2026 | Move Matching quality to the Voice Library (#115) |
| v4.0.25 | 13 Oct 2026 | Clean the voice library automatically; set-aside samples stay set aside (#116) |
| v4.0.26 | 13 Oct 2026 | Confirm a calendar suggestion instantly from the recordings table (#117) |
| v4.0.27 | 13 Oct 2026 | Play a segment to its end, measured on the audio's own clock (#118) |
| v4.0.28 | 13 Oct 2026 | Run Refine automatically after transcription; show its suggestions on open (#119) |

</details>

## 3.3 — Transcript assistant

Released 24 Sep 2026 as v3.3.7.

- Ask a transcript questions in plain English.
- Non-English audio is no longer transcribed as English.
- Hide devices you don't use; reassign a single line to another speaker.

<details><summary>Every change (8)</summary>

| Version | Date | Change |
|---|---|---|
| v3.3.0 | 21 Aug 2026 | Fix multilingual transcription forcing English on non-English audio (#80) |
| v3.3.1 | 9 Sep 2026 | Surface real calendar connector errors instead of a fixed message (#84) |
| v3.3.2 | 9 Sep 2026 | Add a natural-language transcript assistant to the sidecar (#85) |
| v3.3.3 | 9 Sep 2026 | Fix transcript sidecar hang on rapid scroll (#86) |
| v3.3.4 | 10 Sep 2026 | Don't full-rediarize (and un-verify confirmed speakers) on meeting confirm (#87) |
| v3.3.5 | 10 Sep 2026 | Add a reversible way to hide devices you don't use (#88) |
| v3.3.6 | 17 Sep 2026 | Fix speaker-pill rename hanging the transcript sidecar (#89) |
| v3.3.7 | 24 Sep 2026 | Right-click a transcript row pill to reassign just that row (#90) |

</details>

## 3.2 — Calendar assistant

Released 21 Aug 2026 as v3.2.11.

- The calendar assistant finds the meeting and links it.
- Merged recordings pick up their meeting.
- Fewer Keychain prompts, a smoother recordings table, and a fix for a viewer hang.

<details><summary>Every change (12)</summary>

| Version | Date | Change |
|---|---|---|
| v3.2.0 | 3 Aug 2026 | Repair history at launch, protect confirmed speakers, fit the table, and stop Keychain prompt storms (#70) |
| v3.2.1 | 3 Aug 2026 | Say what each snapshot changed, keep meeting rows one line, and allow merging into a merge (#71) |
| v3.2.2 | 3 Aug 2026 | Declutter the Models page, and stop it contradicting what is actually running (#72) |
| v3.2.3 | 3 Aug 2026 | Let the app own its Keychain grant, so a new build stops re-asking (#73) |
| v3.2.4 | 6 Aug 2026 | Stream the calendar assistant, and let it actually link the meeting it finds (#74) |
| v3.2.5 | 6 Aug 2026 | Stop the recording table thrashing during ordinary sync/download/transcription (#75) |
| v3.2.6 | 6 Aug 2026 | Fix the merge arrow disappearing on longer merge labels (#76) |
| v3.2.7 | 6 Aug 2026 | Let a merged recording find — and inherit — its meeting (#77) |
| v3.2.8 | 6 Aug 2026 | Stop the mic-trigger dot pulsing forever, and with it the whole window (#78) |
| v3.2.9 | 21 Aug 2026 | Make speaker-edited transcript Markdown rewrites verifiable (#83) |
| v3.2.10 | 21 Aug 2026 | Stop the transcript viewer hanging the app in a layout loop (#79) |
| v3.2.11 | 21 Aug 2026 | Distrust implausible transcription timeouts; stop dumping raw progress logs into error alerts (#82) |

</details>

## 3.1 — Better speaker detection

Released 31 Jul 2026 as v3.1.8.

- Split one recording into two meetings.
- Transcript history, and speakers you've confirmed are protected.
- Two people are no longer merged into one speaker.

<details><summary>Every change (9)</summary>

| Version | Date | Change |
|---|---|---|
| v3.1.0 | 26 Jul 2026 | Voice library All People tab, candidate merge, and Sortformer cross-window voice linking (#61) |
| v3.1.1 | 31 Jul 2026 | Make an explicit speaker count split labels up, not just merge down (#62) |
| v3.1.2 | 31 Jul 2026 | Split one recording into two independently transcribable meetings (#63) |
| v3.1.3 | 31 Jul 2026 | Protect confirmed speakers from calendar re-diarisation; add local transcript history and a Meeting column (#64) |
| v3.1.4 | 31 Jul 2026 | Measure diarisation quality, then fix what the numbers exposed (#65) |
| v3.1.5 | 31 Jul 2026 | Hugging Face token storage, calendar no-match fix, and an honest deploy dialog (#66) |
| v3.1.6 | 31 Jul 2026 | Stop two speakers being merged into one, and teach the library that names them (#67) |
| v3.1.7 | 31 Jul 2026 | Speaker cap, invitee capture, and invitee-to-speaker mapping (#68) |
| v3.1.8 | 31 Jul 2026 | Fix the test-pass findings: discarded voice matches, split duplication, and dead transcript history (#69) |

</details>

## 3.0 — Voice library

Released 17 Jul 2026 as v3.0.9.

- Speakers are checked against your voice library.
- People filter, name autocomplete, and merging of duplicate voices.
- Calendar invitees narrow down who is speaking.

<details><summary>Every change (10)</summary>

| Version | Date | Change |
|---|---|---|
| v3.0.0 | 3 Jul 2026 | Tidy: portable bench paths + stop publishing planning/security docs (#51) |
| v3.0.1 | 13 Jul 2026 | Speaker verify loop, voice-library accuracy, people filter & tabbed in-app panels (#52) |
| v3.0.2 | 13 Jul 2026 | Require stable Developer ID signing for local installs and updates (#53) |
| v3.0.3 | 13 Jul 2026 | Heatmap: full future-week columns and raise window min width (#54) |
| v3.0.4 | 13 Jul 2026 | Device cards polish + safe transcript copy for unconfirmed speakers (#55) |
| v3.0.5 | 13 Jul 2026 | Fix detail-pane tab switching + Close All (#56) |
| v3.0.6 | 13 Jul 2026 | People filter: Clear control at top of dropdown (#57) |
| v3.0.7 | 13 Jul 2026 | Speaker pill autocomplete + voice library merge/dedupe (#58) |
| v3.0.8 | 17 Jul 2026 | Keep transcript Markdown limited to confirmed speaker names (#59) |
| v3.0.9 | 17 Jul 2026 | Use calendar context to narrow speaker matching (#60) |

</details>

## 2.1 — Meeting heatmap

Released 25 Jun 2026 as v2.1.5.

- A year-long heatmap of your meetings.
- AI agents and the terminal can read your summaries.
- The mic trigger is ready as soon as the app opens; Plaud sign-in is more reliable.

<details><summary>Every change (6)</summary>

| Version | Date | Change |
|---|---|---|
| v2.1.0 | 21 Jun 2026 | Summaries access: MCP tools + notes CLI (agent/terminal access to typed summaries) (#43) |
| v2.1.1 | 21 Jun 2026 | Arm mic trigger immediately on launch (decouple from catalog probe) (#44) |
| v2.1.2 | 22 Jun 2026 | Meeting-activity heatmap (GitHub-style contribution grid) — Tier 1 (#45) |
| v2.1.3 | 23 Jun 2026 | Plaud login: ephemeral session, APAC region, Sign out vs Forget (#46) |
| v2.1.4 | 25 Jun 2026 | Filters only change on explicit user action (remove Plaud auto-filter) (#47) |
| v2.1.5 | 25 Jun 2026 | mic-trigger: only hold the HiDock H1, skip the P1 (#48) |

</details>

## 2.0 — AI summaries

Released 18 Jun 2026 as v2.0.21.

- Summaries by meeting type, written from templates you can edit.
- Summarise one recording, a selection, or every new one automatically.
- A built-in CLI pane to ask questions about a recording.

<details><summary>Every change (22)</summary>

| Version | Date | Change |
|---|---|---|
| v2.0.0 | 16 Jun 2026 | hidock: make Plaud "Sign in required" launch sign-in (#21) |
| v2.0.1 | 16 Jun 2026 | hidock plaud: show downloaded recordings when signed out / offline (#22) |
| v2.0.2 | 16 Jun 2026 | hidock: show storage bar + file count on the Plaud card (#23) |
| v2.0.3 | 16 Jun 2026 | hidock plaud: paint cached/downloaded recordings on launch (#24) |
| v2.0.4 | 16 Jun 2026 | hidock: transcribed-this-session badge on the app icon (#25) |
| v2.0.5 | 16 Jun 2026 | summarize: honor [summarization].engine config (was hard-coded "auto") (#26) |
| v2.0.6 | 16 Jun 2026 | Enable Swift unit tests via a test-action scheme (#13) |
| v2.0.7 | 16 Jun 2026 | summarise: Summarisation Provider menu (selectable LLM engine) (#27) |
| v2.0.8 | 16 Jun 2026 | docs: summarisation design + transcription A/B findings + WER harness (#28) |
| v2.0.9 | 16 Jun 2026 | summarise engine: type-aware, template-driven summaries via Claude Code (#29) |
| v2.0.10 | 16 Jun 2026 | fix(lint): split ;-joined statements in wer.py (ruff E702) (#30) |
| v2.0.11 | 16 Jun 2026 | summarise UI: per-recording Claude Code actions + Summarised/Summarising status (#31) |
| v2.0.12 | 17 Jun 2026 | summarise UI slice 3: Summarise Selected button, Auto-summarise toggle, Summary column (#32) |
| v2.0.13 | 17 Jun 2026 | embedded CLI pane: Ask Claude Code + summarise activity in-app, CLI toggle, glyph alignment (#33) |
| v2.0.14 | 17 Jun 2026 | slice 5: streaming summarise output + Summary Templates manager (#34) |
| v2.0.15 | 17 Jun 2026 | summarise fixes + in-app summary viewer + AI rename + engine picker + stable signing (#35) |
| v2.0.16 | 17 Jun 2026 | AI auto-detect consistency + deterministic template-edit approval hook + remove Cowork (#36) |
| v2.0.17 | 17 Jun 2026 | summarise refinements: guidance leak, cleaner CLI stream, local Plaud time, date injection (#37) |
| v2.0.18 | 17 Jun 2026 | summary classification header + reclassify dropdown + dedup on re-summarise (#38) |
| v2.0.19 | 17 Jun 2026 | add summary type filter (filter recordings by classification) (#39) |
| v2.0.20 | 17 Jun 2026 | load HiDock + Plaud cache together on launch (concurrent cache-paint) (#40) |
| v2.0.21 | 18 Jun 2026 | add "Show CLI while summarising" toggle (Models window) (#41) |

</details>

## 1.1 — Notarised builds and Plaud sign-in

Released 16 Jun 2026 as v1.1.4.

- Builds are signed and notarised by Apple.
- Plaud shows when you're signed out instead of an empty list.
- Fixes for crashes on long recordings.

<details><summary>Every change (5)</summary>

| Version | Date | Change |
|---|---|---|
| v1.1.0 | 19 May 2026 | Device cards Phase 2 + H1 catalog fix + diarization fix + launch UX (#4) |
| v1.1.1 | 19 May 2026 | Fix Whisper MPS crash when word_timestamps=True (#5) |
| v1.1.2 | 11 Jun 2026 | Chunk long audio in Parakeet ASR to stop MLX/Metal SIGABRT (#11) |
| v1.1.3 | 16 Jun 2026 | hidock plaud: surface signed-out instead of "connected, 0 recordings" (#19) |
| v1.1.4 | 16 Jun 2026 | hidock release: Developer ID sign + notarize the macOS app (#20) |

</details>

## 1.0 — First release

Released 24 Mar 2026 as v1.0.0.
