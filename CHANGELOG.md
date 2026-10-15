# Changelog

All notable changes to HiDock Mic Trigger (the macOS app). Releases before v4.1.0 were reconstructed on 2026-10-15 from the merged pull requests; each one is tagged at the commit it shipped from.

The first number marks a big shift in what the app does (2: AI summaries, 3: speaker identity and the voice library, 4: Refine, learning from review, Nemotron and GitHub publishing). The second marks a batch of features, and the third a batch of fixes only.

## v4.1.0 — Settings window and setup check (2026-10-15)

- A proper Settings window (⌘,) with ten panes. The footer drops from 11 controls to 5.
- A setup check flags missing tokens, models, packages or a GitHub sign-in, with a fix for each. Nemotron falls back to on-Mac speaker detection.
- Migration guide and fixes for moving to a new Mac. TitaNet installs again.
- Refine runs automatically after transcription; the LED ticker works properly; older recordings can be left out of "to tag".

<details><summary>All changes (9 PRs)</summary>

- Fix TitaNet install on a fresh Mac (#121)
- Catch a half-migrated or new Mac before it costs a transcript (#122)
- Trim the transcript along with the audio (#123)
- Migration guide: code-signing access on a new Mac (#124)
- Move voice-library samples to whoever the transcript says is speaking (#125)
- Let old untagged recordings stop counting towards 'to tag' (#126)
- One Settings window for every setting; footer down to five controls (#127)
- Settings polish: one scroll per pane, tidy Models and Devices; make the LED ticker work (#129)
- Models pane: expand rows with a chevron; models in use open by default (#130)

</details>

## v4.0.0 — Smarter matching, Refine, and GitHub publishing (2026-10-13)

- Publish transcripts to a private GitHub repository.
- Nemotron 3 speaker detection on an NVIDIA DGX Spark.
- Smarter calendar matching: picks the meeting with the most overlap, uses the transcript as a tie-break, runs the lookup during transcription, and confirms instantly.
- Refine checks every line against your saved voices; review suggestions one by one, with playback.
- Learns from your review decisions and tunes matching automatically. Voice-library clean-up and checked voice samples.

<details><summary>All changes (29 PRs)</summary>

- Publish transcript .md files to the private Transcripts GitHub repo (#91)
- Nemotron 3 Diarization: remote backend + hardware-ownership gate (#92)
- Warn when calendar lookups can't reach the calendar, with a terminal fix (#93)
- Keep surname-first calendar invitees as one person (#94)
- Don't force a speaker count the reviewer didn't choose (#95)
- Stop re-diarisation collapsing speakers and overwriting tags (#96)
- Pick the dominant meeting when a recording overlaps several (#97)
- Diarise against the suggested meeting while transcribing (#98)
- Sidecar calendar check picks the best fit; confirm waits for the saved meeting (#99)
- Refine checks each line against the meeting's saved voices (#100)
- Only learn a voice clip that sounds like the confirmed person (#101)
- Clean up speaker-work progress and messages (#102)
- Unsigned and test builds never prompt for or take over Keychain items (#103)
- Calendar confirm advice uses the neutral info style (#104)
- Bring the transcripts-sync plan up to date (#105)
- Ignore local Obsidian settings and screenshots (#106)
- Show Refine's suggestions in the sidecar, not a popover (#107)
- Tidier sidecar: reviewable Refine suggestions, Ask behind a button, one transcript at a time (#108)
- Unsigned and test builds don't touch Keychain items at all (#109)
- Wait up to 10 minutes for deploy approval (#110)
- Refine suggestions: stop button, and playing a line shows it in the transcript (#111)
- Refine applies survive edits; playing a suggestion no longer needs two clicks (#112)
- Learn from review decisions and measure how well matching does (#113)
- Tune matching from review decisions automatically; show it in Settings (#114)
- Move Matching quality to the Voice Library (#115)
- Clean the voice library automatically; set-aside samples stay set aside (#116)
- Confirm a calendar suggestion instantly from the recordings table (#117)
- Play a segment to its end, measured on the audio's own clock (#118)
- Run Refine automatically after transcription; show its suggestions on open (#119)

</details>

## v3.3.0 — Transcript assistant (2026-09-24)

- Ask a transcript questions in plain English from the sidecar.
- Multilingual audio no longer comes out forced into English.
- Hide devices you don't use (reversible); right-click a speaker label to reassign just that line.
- Confirming a meeting no longer re-runs all speaker detection or un-confirms speakers. Fixes for hangs in the sidecar.

<details><summary>All changes (8 PRs)</summary>

- Fix multilingual transcription forcing English on non-English audio (#80)
- Surface real calendar connector errors instead of a fixed message (#84)
- Add a natural-language transcript assistant to the sidecar (#85)
- Fix transcript sidecar hang on rapid scroll (#86)
- Don't full-rediarize (and un-verify confirmed speakers) on meeting confirm (#87)
- Add a reversible way to hide devices you don't use (#88)
- Fix speaker-pill rename hanging the transcript sidecar (#89)
- Right-click a transcript row pill to reassign just that row (#90)

</details>

## v3.2.1 — Fixes (2026-08-21)

- The transcript viewer no longer hangs the app in a layout loop.
- Implausible transcription timeouts are ignored, and error alerts no longer dump raw progress logs.
- When speaker edits rewrite the transcript Markdown, the result is checked.

<details><summary>All changes (3 PRs)</summary>

- Stop the transcript viewer hanging the app in a layout loop (#79)
- Distrust implausible transcription timeouts; stop dumping raw progress logs into error alerts (#82)
- Make speaker-edited transcript Markdown rewrites verifiable (#83)

</details>

## v3.2.0 — Calendar assistant (2026-08-06)

- The calendar assistant streams its answer and can link the meeting it finds.
- Merged recordings find and inherit their meeting.
- The Keychain stops asking again after each new build. History is repaired at launch.
- The recordings table no longer redraws constantly during sync, downloads or transcription, and the Models page is less cluttered.

<details><summary>All changes (9 PRs)</summary>

- Repair history at launch, protect confirmed speakers, fit the table, and stop Keychain prompt storms (#70)
- Say what each snapshot changed, keep meeting rows one line, and allow merging into a merge (#71)
- Declutter the Models page, and stop it contradicting what is actually running (#72)
- Let the app own its Keychain grant, so a new build stops re-asking (#73)
- Stream the calendar assistant, and let it actually link the meeting it finds (#74)
- Stop the recording table thrashing during ordinary sync/download/transcription (#75)
- Fix the merge arrow disappearing on longer merge labels (#76)
- Let a merged recording find — and inherit — its meeting (#77)
- Stop the mic-trigger dot pulsing forever, and with it the whole window (#78)

</details>

## v3.1.0 — Better speaker detection (2026-07-31)

- Split one recording into two meetings that transcribe separately.
- Local transcript history, and confirmed speakers are protected when a calendar match re-runs speaker detection.
- Diarisation quality is measured, and the problems it showed are fixed: two speakers no longer merge into one, and an explicit speaker count can split as well as merge.
- Hugging Face token stored in the Keychain; calendar invitees mapped to speakers.

<details><summary>All changes (9 PRs)</summary>

- Voice library All People tab, candidate merge, and Sortformer cross-window voice linking (#61)
- Make an explicit speaker count split labels up, not just merge down (#62)
- Split one recording into two independently transcribable meetings (#63)
- Protect confirmed speakers from calendar re-diarisation; add local transcript history and a Meeting column (#64)
- Measure diarisation quality, then fix what the numbers exposed (#65)
- Hugging Face token storage, calendar no-match fix, and an honest deploy dialog (#66)
- Stop two speakers being merged into one, and teach the library that names them (#67)
- Speaker cap, invitee capture, and invitee-to-speaker mapping (#68)
- Fix the test-pass findings: discarded voice matches, split duplication, and dead transcript history (#69)

</details>

## v3.0.0 — Speaker verification and the voice library (2026-07-17)

- Speaker verification loop and more accurate voice-library matching.
- People filter, speaker-name autocomplete, and voice-library merge and de-duplication.
- Calendar invitees narrow down who a speaker can be.
- Transcript Markdown only names speakers you've confirmed. Local installs require stable signing.

<details><summary>All changes (10 PRs)</summary>

- Tidy: portable bench paths + stop publishing planning/security docs (#51)
- Speaker verify loop, voice-library accuracy, people filter & tabbed in-app panels (#52)
- Require stable Developer ID signing for local installs and updates (#53)
- Heatmap: full future-week columns and raise window min width (#54)
- Device cards polish + safe transcript copy for unconfirmed speakers (#55)
- Fix detail-pane tab switching + Close All (#56)
- People filter: Clear control at top of dropdown (#57)
- Speaker pill autocomplete + voice library merge/dedupe (#58)
- Keep transcript Markdown limited to confirmed speaker names (#59)
- Use calendar context to narrow speaker matching (#60)

</details>

## v2.1.0 — Meeting heatmap (2026-06-25)

- A GitHub-style heatmap of meeting activity over the past year.
- Agents and the terminal can read typed summaries (MCP tools and a `notes` CLI).
- The mic trigger is ready as soon as the app opens, and only holds the H1.
- Plaud sign-in: a fresh session each time, APAC region support, and Sign out kept separate from Forget.

<details><summary>All changes (6 PRs)</summary>

- Summaries access: MCP tools + notes CLI (agent/terminal access to typed summaries) (#43)
- Arm mic trigger immediately on launch (decouple from catalog probe) (#44)
- Meeting-activity heatmap (GitHub-style contribution grid) — Tier 1 (#45)
- Plaud login: ephemeral session, APAC region, Sign out vs Forget (#46)
- Filters only change on explicit user action (remove Plaud auto-filter) (#47)
- mic-trigger: only hold the HiDock H1, skip the P1 (#48)

</details>

## v2.0.0 — AI summaries (2026-06-18)

- Type-aware summaries written by Claude Code from editable templates, with a Summary Templates manager.
- Summarise one recording or a selection, or automatically, with a Summary column and a filter by meeting type.
- Built-in CLI pane: Ask Claude Code about a recording and watch summaries stream in.
- Choose the summarisation engine; Plaud recordings show their storage and download state.
- A badge on the app icon counts what was transcribed this session.

<details><summary>All changes (22 PRs)</summary>

- Enable Swift unit tests via a test-action scheme (#13)
- hidock: make Plaud "Sign in required" launch sign-in (#21)
- hidock plaud: show downloaded recordings when signed out / offline (#22)
- hidock: show storage bar + file count on the Plaud card (#23)
- hidock plaud: paint cached/downloaded recordings on launch (#24)
- hidock: transcribed-this-session badge on the app icon (#25)
- summarize: honor [summarization].engine config (was hard-coded "auto") (#26)
- summarise: Summarisation Provider menu (selectable LLM engine) (#27)
- docs: summarisation design + transcription A/B findings + WER harness (#28)
- summarise engine: type-aware, template-driven summaries via Claude Code (#29)
- fix(lint): split ;-joined statements in wer.py (ruff E702) (#30)
- summarise UI: per-recording Claude Code actions + Summarised/Summarising status (#31)
- summarise UI slice 3: Summarise Selected button, Auto-summarise toggle, Summary column (#32)
- embedded CLI pane: Ask Claude Code + summarise activity in-app, CLI toggle, glyph alignment (#33)
- slice 5: streaming summarise output + Summary Templates manager (#34)
- summarise fixes + in-app summary viewer + AI rename + engine picker + stable signing (#35)
- AI auto-detect consistency + deterministic template-edit approval hook + remove Cowork (#36)
- summarise refinements: guidance leak, cleaner CLI stream, local Plaud time, date injection (#37)
- summary classification header + reclassify dropdown + dedup on re-summarise (#38)
- add summary type filter (filter recordings by classification) (#39)
- load HiDock + Plaud cache together on launch (concurrent cache-paint) (#40)
- add "Show CLI while summarising" toggle (Models window) (#41)

</details>

## v1.1.0 — Plaud sign-in states and notarised builds (2026-06-16)

- macOS releases are now signed with a Developer ID and notarised by Apple.
- Device cards: the second phase, plus a fix to the H1 recording catalogue.
- Plaud shows "signed out" instead of "connected, 0 recordings".
- Fixes for crashes on long recordings: Whisper on Apple GPUs, and Parakeet (long audio is now chunked).

<details><summary>All changes (5 PRs)</summary>

- Device cards Phase 2 + H1 catalog fix + diarization fix + launch UX (#4)
- Fix Whisper MPS crash when word_timestamps=True (#5)
- Chunk long audio in Parakeet ASR to stop MLX/Metal SIGABRT (#11)
- hidock plaud: surface signed-out instead of "connected, 0 recordings" (#19)
- hidock release: Developer ID sign + notarize the macOS app (#20)

</details>

## v1.0.0 — First release (2026-03-24)

- SwiftUI polish, automatic updates, in-app feedback and GitHub Releases (#1, #3).
