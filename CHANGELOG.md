# Changelog

HiDock Mic Trigger for macOS. Version numbers: the **first** number marks a big change in what the app does, the **second** a group of features, and the **third** each change merged (one pull request each). Release notes and downloads: https://github.com/jw-gsl/HiDock-Mic-Trigger/releases

Releases before 4.1 were written up on 15 Oct 2026 from the merged pull requests. Every version is tagged at the commit it came from, with that change's original date.

## 4.1 — Settings window

**Released 16 Oct 2026** · 15 changes (v4.1.0 to v4.1.14, 15 Oct 2026 – 16 Oct 2026)

This release brings every setting into one Settings window and makes setting up on a new Mac safer, warning you about missing pieces before they cost you a transcript.

### New
- **Settings window.** Press ⌘, to open one Settings window with a sidebar of panes, instead of settings spread across menus and the footer. The footer is down to five buttons.
- **Setup check.** If something the app needs is missing, such as a token, a model or your GitHub sign-in, a "Setup needs attention" banner says so and links to the right Settings pane.
- **Moving to a new Mac.** A migration guide (docs/MIGRATING.md) covers moving the app to a new Mac, including fixing code-signing access.
- **"Only count from" date for speaker tagging.** In Settings → Speakers, tick "Only count recordings from a date" and pick one; older recordings stop counting towards the "to tag" count and their tag icon turns grey (still clickable).

### Improved
- **Check for Updates in a build from the code.** It now shows your version, the latest release, and the changes waiting on GitHub, and Update now pulls them and rebuilds (you approve the restart). You're also told at launch when there's something new.
- **Models pane.** Each model expands with an arrow, the models you're using are open by default, and there are Expand all, Collapse all and Show models in use buttons.
- **Speaker detection falls back.** If Nemotron on a separate NVIDIA computer isn't available, speakers are detected on the Mac instead.
- **Voice library corrected.** Voice samples filed under the wrong person are moved to whoever the transcript says was speaking, or set aside if that's not certain.
- **LED ticker.** Events now scroll across the heatmap while it's at rest (before, they never showed). The display style, colour, brightness and speed apply straight away, and a "Send a test message" button lets you try it.

### Fixed
- **Trimmed recordings.** Trimming a recording now trims its transcript too, instead of leaving the full-length text.
- **Voice model install on a fresh Mac.** The TitaNet voice model downloads correctly again on a new Mac.
- **Downloaded app couldn't sync or transcribe.** The app on this page now includes its own Python and every file its device sync and transcription need, and each release is checked before publishing. Earlier downloads were missing parts: device sync stopped at start-up and transcription couldn't run. This didn't affect copies built from the code.

<details><summary>Every change (9)</summary>

| Version | Date | Change |
|---|---|---|
| v4.1.0 | 15 Oct 2026 | TitaNet voice model installs correctly on a fresh Mac (#121) |
| v4.1.1 | 15 Oct 2026 | Setup check and banner; Nemotron falls back to the Mac (#122) |
| v4.1.2 | 15 Oct 2026 | Migration guide covers code-signing access on a new Mac (#124) |
| v4.1.3 | 15 Oct 2026 | Old untagged recordings can stop counting towards "to tag" (#126) |
| v4.1.4 | 15 Oct 2026 | Trimming a recording trims its transcript too (#123) |
| v4.1.5 | 15 Oct 2026 | One Settings window for every setting; footer down to five buttons (#127) |
| v4.1.6 | 15 Oct 2026 | Voice samples moved to whoever the transcript says was speaking (#125) |
| v4.1.7 | 15 Oct 2026 | Tidier Settings panes; LED ticker fixes and a test button (#129) |
| v4.1.8 | 15 Oct 2026 | Models pane rows expand with an arrow; models in use open by default (#130) |
| v4.1.9 | 16 Oct 2026 | Downloaded app includes its own Python; builds run from the repo no longer offer to replace themselves with the download (#135) |
| v4.1.10 | 16 Oct 2026 | Downloaded app includes everything device sync and transcription need, checked before each release (#136) |
| v4.1.11 | 16 Oct 2026 | Release build copies the pipeline's shared files reliably (#137) |
| v4.1.12 | 16 Oct 2026 | Check for Updates in a build from the code shows what's new on GitHub and can install it (#138) |
| v4.1.13 | 16 Oct 2026 | Installing a build waits for its version label, and won't install a mismatched one; guide covers updating (#139) |
| v4.1.14 | 16 Oct 2026 | Check for Updates compares the installed app with GitHub, not just the code folder (#PR) |

</details>

## 4.0 — Smarter matching and Refine

**Released 13 Oct 2026** · 29 changes (v4.0.0 to v4.0.28, 12 Oct 2026 – 13 Oct 2026)

This release is about getting speaker names right with less effort: Refine now finds lines given to the wrong person and lets you fix them one at a time, meetings are matched from your calendar more reliably, and the voice library keeps itself tidy.

### New
- **Refine suggestions.** Refine, in the transcript panel, checks every line against the saved voices of the people in that meeting and suggests lines to move to a different speaker. Each suggestion shows the line, who it would move to and a play button, with Apply and Skip (or Apply all / Skip all). Refine now runs by itself after transcription, so the suggestions are waiting when you open a transcript.
- **Publish transcripts to GitHub.** A Transcripts on GitHub menu can copy your transcript files to a private GitHub repository automatically, or on demand with Sync to GitHub Now. It is off by default and won't switch on if the repository is public.
- **Remote speaker detection on an NVIDIA computer.** Nemotron can be chosen as the speaker detector if you have a separate machine with an NVIDIA graphics card; in Models, say you have one, then use Check connection.
- **Matching quality in the Voice Library.** A panel at the top of the Voice Library shows how often Refine's suggestions are accepted and what the app has adjusted as a result. Samples that sound more like someone else are set aside automatically, and Undo clean-ups puts them back.

### Improved
- **Better meeting matching.** When a recording overlaps several meetings, the app suggests the one it mostly covers, and uses the transcript to break ties. The meeting is looked up while transcribing, so speakers are detected against the right invite list first time and confirming usually has nothing to redo.
- **Faster confirming.** Ticking a suggested meeting in the recordings table now links it straight away instead of taking around 15 seconds.
- **Learns from your corrections.** Applying or skipping Refine suggestions, moving lines by hand and confirming meetings all teach the app, and a skipped line isn't suggested again unless the match gets clearly stronger. Voice samples are only saved when they actually sound like the confirmed person.
- **Tidier transcript panel.** Opening a transcript replaces the one already open instead of adding a tab per meeting, the "tell it what's wrong" box sits behind an Ask button, and progress and messages for speaker work are clearer.
- **Speaker count left on Auto.** Redetect and confirming a meeting no longer force a number of speakers you didn't choose.

### Fixed
- **Calendar outages going unnoticed.** If the app can't reach your calendar, an orange banner now says so, with Fix in Terminal and Check again buttons, instead of quietly leaving recordings without a meeting.
- **Names split in two.** Invitees written as "Surname, First" now stay as one person.
- **Speakers collapsing into one.** Re-detecting speakers no longer gives everyone the name of the one confirmed speaker, and names you tag while it runs are kept.
- **Lost Refine changes and playback glitches.** Applied suggestions are no longer silently dropped after you edit a line, playing a suggestion takes one click, and short lines play to the end.
- **Repeated Keychain prompts.** Test builds no longer trigger "wants to use your confidential information" prompts for saved tokens.

<details><summary>Every change (29)</summary>

| Version | Date | Change |
|---|---|---|
| v4.0.0 | 12 Oct 2026 | Option to publish transcripts to a private GitHub repository (#91) |
| v4.0.1 | 12 Oct 2026 | Nemotron speaker detection on a separate NVIDIA computer (#92) |
| v4.0.2 | 12 Oct 2026 | "Surname, First" invitees stay as one person (#94) |
| v4.0.3 | 12 Oct 2026 | Speaker count stays on Auto unless you choose one (#95) |
| v4.0.4 | 13 Oct 2026 | Banner and fix steps when the calendar can't be reached (#93) |
| v4.0.5 | 13 Oct 2026 | Re-detecting speakers no longer merges them under one name or loses new tags (#96) |
| v4.0.6 | 13 Oct 2026 | Suggests the main meeting when a recording overlaps several (#97) |
| v4.0.7 | 13 Oct 2026 | Meeting looked up during transcription so speakers are detected against it first time (#98) |
| v4.0.8 | 13 Oct 2026 | Check calendar in the transcript panel suggests the best fit; confirming waits for the meeting to save (#99) |
| v4.0.9 | 13 Oct 2026 | Only saves a voice sample that sounds like the confirmed person (#101) |
| v4.0.10 | 13 Oct 2026 | Refine checks each line against the meeting's saved voices (#100) |
| v4.0.11 | 13 Oct 2026 | Clearer progress and messages while detecting speakers (#102) |
| v4.0.12 | 13 Oct 2026 | Test builds no longer trigger Keychain prompts (#103) |
| v4.0.13 | 13 Oct 2026 | Calendar confirm advice shown as a neutral note (#104) |
| v4.0.14 | 13 Oct 2026 | Internal: planning notes (#105) |
| v4.0.15 | 13 Oct 2026 | Internal: repository tidy-up (#106) |
| v4.0.16 | 13 Oct 2026 | Refine suggestions shown in the transcript panel (#107) |
| v4.0.17 | 13 Oct 2026 | Reviewable Refine suggestions, Ask button, one transcript open at a time (#108) |
| v4.0.18 | 13 Oct 2026 | Test builds don't touch saved Keychain items at all (#109) |
| v4.0.19 | 13 Oct 2026 | Internal: longer wait for install approval (#110) |
| v4.0.20 | 13 Oct 2026 | Stop button on Refine suggestions; playing one scrolls to its line (#111) |
| v4.0.21 | 13 Oct 2026 | Applied suggestions survive edits; playing a suggestion takes one click (#112) |
| v4.0.22 | 13 Oct 2026 | Learns from your review decisions (#113) |
| v4.0.23 | 13 Oct 2026 | Adjusts matching from your decisions automatically (#114) |
| v4.0.24 | 13 Oct 2026 | Matching quality moved to the Voice Library (#115) |
| v4.0.25 | 13 Oct 2026 | Voice library cleaned automatically, with undo (#116) |
| v4.0.26 | 13 Oct 2026 | Confirm a suggested meeting instantly from the recordings table (#117) |
| v4.0.27 | 13 Oct 2026 | Short lines play to the end (#118) |
| v4.0.28 | 13 Oct 2026 | Refine runs after transcription; suggestions waiting when you open it (#119) |

</details>

## 3.3 — Transcript assistant

**Released 24 Sep 2026** · 8 changes (v3.3.0 to v3.3.7, 21 Aug 2026 – 24 Sep 2026)

This release makes the transcript panel easier to correct, adds a way to hide devices you don't use, and fixes transcripts of non-English meetings coming out as garbled English.

### New
- **Ask the transcript to fix itself.** A text box in the transcript panel lets you describe what's wrong in plain words (for example, "there were definitely 5 speakers"), and the app runs its own speaker tools to fix it. Every change it makes can be undone from the transcript's version history.
- **Reassign a single line.** Right-click the speaker label on a transcript line to move just that line to someone else, or every line for that speaker.
- **Hide devices you don't use.** In Device Manager, each paired device now has a Hide button next to Forget. Hidden devices leave the device strip and stop being polled for, but keep their recording history; tick "Show hidden" to bring them back.

### Improved
- **Clearer calendar errors.** When the calendar assistant can't reach your calendar, the message now says why instead of showing the same fixed text every time. The assistant also only uses the calendar connector you picked in Settings.
- **Confirming a meeting keeps confirmed speakers.** If some speakers are already confirmed, confirming the meeting no longer re-detects the whole recording and risks losing those names.

### Fixed
- **Non-English meetings.** Audio in other languages, or calls that switch language part-way through, are no longer forced into English and transcribed as nonsense.
- **Transcript panel freezing.** Scrolling a long transcript quickly, or clicking a speaker label to rename it, no longer locks up the app.

<details><summary>Every change (8)</summary>

| Version | Date | Change |
|---|---|---|
| v3.3.0 | 21 Aug 2026 | Non-English and mixed-language audio is no longer forced into English (#80) |
| v3.3.1 | 9 Sep 2026 | Calendar errors now say what actually went wrong (#84) |
| v3.3.2 | 9 Sep 2026 | New plain-language assistant in the transcript panel that fixes speakers for you (#85) |
| v3.3.3 | 9 Sep 2026 | Transcript panel no longer freezes when you scroll quickly (#86) |
| v3.3.4 | 10 Sep 2026 | Confirming a meeting no longer un-confirms speakers you'd already named (#87) |
| v3.3.5 | 10 Sep 2026 | Hide devices you don't use without losing their history (#88) |
| v3.3.6 | 17 Sep 2026 | Renaming a speaker from its label no longer freezes the transcript panel (#89) |
| v3.3.7 | 24 Sep 2026 | Right-click a line's speaker label to reassign just that line (#90) |

</details>

## 3.2 — Calendar assistant

**Released 21 Aug 2026** · 12 changes (v3.2.0 to v3.2.11, 3 Aug 2026 – 21 Aug 2026)

This release smooths out everyday use: the calendar assistant can now link the meeting it finds, merged recordings work with meetings, and several causes of high CPU use and a transcript-viewer hang are fixed.

### New
- **Link a meeting straight from the calendar assistant.** When the assistant finds the right meeting, a "Use this meeting" button links it to the recording, even when the recorder's clock was wrong. Replies now appear as they are written, with a note of what the assistant is currently doing.
- **Clearer transcript history.** Each snapshot now says what it was saved before, such as "Before renaming Speaker 2 → Alex", and expands to show the speakers in that version.
- **Settings tab on the Models page.** AI summariser, Hugging Face and calendar settings now sit on their own tab, so the Models tab only shows what each step uses.

### Improved
- **Merged recordings and meetings.** You can add a recording to an existing merged file. A merged recording can now be matched to a calendar meeting, and picks up the meeting automatically when every piece is already linked to the same one.
- **Easier-to-read Models page.** Each step shows the selected model clearly (or "None selected"), descriptions are tucked away, models that aren't available yet can't be picked, licence restrictions show on the row, and sizes say whether they are on disk or a download.
- **Recordings table fits the window.** The table no longer leaves a growing gap at the side as the window narrows, meeting rows stay one line tall, and merged labels keep their arrow (for example "Rec00→Rec01").
- **Clearer error messages.** A failed download now shows the last few useful lines rather than thousands of progress lines.

### Fixed
- **Confirmed speakers merged together.** Asking for fewer speakers no longer combines two people you have already confirmed; it stops at the number you've confirmed.
- **Repeated Keychain prompts.** After you allow access once, a new version of the app no longer asks again.
- **High CPU use.** The app no longer uses a lot of CPU while sitting idle (caused by the pulsing green mic-trigger dot, which is now steady) or during sync, download and transcription.
- **Transcript viewer freezing.** Tagging speakers on a long transcript no longer hangs the app.
- **Long transcriptions stopped early.** Some Plaud recordings were timed out a few minutes into transcription because their length was misread; that no longer happens.
- **Other fixes.** Transcript history now works from launch; the Voice Library duplicates count shows straight away; gated models such as pyannote can be downloaded; the app now reports when a saved transcript fails to update after speaker edits.

<details><summary>Every change (12)</summary>

| Version | Date | Change |
|---|---|---|
| v3.2.0 | 3 Aug 2026 | Transcript history repaired at launch; confirmed speakers can't be merged; table fits the window; fewer Keychain prompts (#70) |
| v3.2.1 | 3 Aug 2026 | History snapshots say what changed; one-line meeting rows; add a recording to an existing merge (#71) |
| v3.2.2 | 3 Aug 2026 | Tidier Models page with a separate Settings tab, and it now shows the model actually in use (#72) |
| v3.2.3 | 3 Aug 2026 | Keychain access remembered across app updates; gated models can be downloaded; duplicates count shows straight away (#73) |
| v3.2.4 | 6 Aug 2026 | Calendar assistant replies as it goes, and can link the meeting it finds (#74) |
| v3.2.5 | 6 Aug 2026 | Lower CPU use during sync, download and transcription (#75) |
| v3.2.6 | 6 Aug 2026 | Merged recording labels keep their arrow (#76) |
| v3.2.7 | 6 Aug 2026 | Merged recordings can be matched to a meeting and inherit it from their pieces (#77) |
| v3.2.8 | 6 Aug 2026 | Mic-trigger dot no longer pulses, fixing high CPU while idle (#78) |
| v3.2.9 | 21 Aug 2026 | The app reports when a transcript fails to update after speaker edits (#83) |
| v3.2.10 | 21 Aug 2026 | Transcript viewer no longer hangs the app on long recordings (#79) |
| v3.2.11 | 21 Aug 2026 | Long Plaud recordings no longer time out early; shorter download error messages (#82) |

</details>

## 3.1 — Better speaker detection

**Released 31 Jul 2026** · 9 changes (v3.1.0 to v3.1.8, 26 Jul 2026 – 31 Jul 2026)

This release makes speaker detection noticeably more reliable, especially for long meetings and calls where two people were being merged into one, and adds tools to split a recording in two and undo speaker changes.

### New
- **Split a recording into two meetings.** A new Split button in the recordings toolbar cuts one recording into two at a time you choose, so back-to-back meetings on one file can be transcribed and linked separately. Existing transcripts are carried over to the two parts, and the original no longer shows alongside them.
- **Transcript history.** Before any change that could alter speaker names, the app saves a snapshot of the transcript. A History button lets you see earlier versions and restore one, and a restore can itself be undone.
- **Meeting column.** The recordings table shows the linked calendar meeting's title and time, with confirm and reject controls. A calendar icon looks up a meeting on demand, and recordings you mark as having no meeting read "Ad-hoc call".
- **Invitees shown under the meeting.** The meeting's invitees appear as pills under the title. Click one to choose which detected speaker that person is.
- **All People tab in the Voice Library.** See everyone the app knows about in one list and merge two entries that are the same person.
- **Hugging Face access on the Models page.** Store a Hugging Face token securely in the macOS Keychain (shown only partly hidden), with step-by-step setup for models that need it.

### Improved
- **Choosing a higher speaker count now works.** Asking Redetect for more speakers now splits voices apart where the audio supports it, rather than doing nothing. The limit goes up from 8 to 20 speakers for large meetings.
- **Fewer people merged into one.** Speaker detection no longer collapses two clearly different people into a single speaker, keeps the same person under one label throughout a long recording, and more often names speakers automatically.
- **All invitees are picked up.** Calendar invitees whose names weren't in your Contacts were being dropped; they are now included, using their email address to work out a name.
- **Quicker renaming.** The name editor now selects the existing name when it opens, so you can just start typing.

### Fixed
- **Confirmed speakers being reshuffled.** Linking a calendar meeting to a transcript whose speakers you've all confirmed no longer re-runs speaker detection and loses your names.
- **Linked meetings disappearing.** Confirmed meetings no longer vanish from the table when device status refreshes.
- **Bogus meeting suggestions.** The app no longer offers a "meeting" titled with the assistant's own "no event found" message.
- **Known voices left unnamed.** Speakers the voice library clearly recognised were sometimes left as "Speaker 1"; they are now named.
- **Merged recordings.** Merged recordings show a readable range such as "Rec07→Rec09", and "Ad-hoc call" works on them.

<details><summary>Every change (9)</summary>

| Version | Date | Change |
|---|---|---|
| v3.1.0 | 26 Jul 2026 | Voice Library All People tab and merge; same speaker kept under one label across a long recording (#61) |
| v3.1.1 | 31 Jul 2026 | Asking for more speakers now splits voices apart instead of doing nothing (#62) |
| v3.1.2 | 31 Jul 2026 | Split one recording into two separately transcribable meetings (#63) |
| v3.1.3 | 31 Jul 2026 | Confirmed speakers protected when linking a meeting; transcript history; Meeting column (#64) |
| v3.1.4 | 31 Jul 2026 | More accurate speaker detection, after fixing a fault that had quietly disabled part of it (#65) |
| v3.1.5 | 31 Jul 2026 | Hugging Face token stored in the Keychain; on-demand meeting lookup and "Ad-hoc call"; no more bogus meeting suggestions (#66) |
| v3.1.6 | 31 Jul 2026 | Two different people no longer merged into one speaker; better automatic naming (#67) |
| v3.1.7 | 31 Jul 2026 | Up to 20 speakers; all calendar invitees captured; click an invitee to pick their speaker (#68) |
| v3.1.8 | 31 Jul 2026 | Fixes: unnamed known voices, split leaving the original in place, transcript history not saving (#69) |

</details>

## 3.0 — Voice library

**Released 17 Jul 2026** · 10 changes (v3.0.0 to v3.0.9, 3 Jul 2026 – 17 Jul 2026)

This release is about getting speaker names right: you can now check and confirm who is speaking in each transcript, the voice library learns from what you confirm, and your calendar helps narrow down who was in the room. Side panels also now open as tabs inside the main window.

### New
- **Confirm who is speaking.** Each speaker in a transcript now shows as confirmed (green tick), suggested by the app (blue question mark) or still to tag (orange). In the transcript viewer you can confirm, rename or mark a speaker as unknown, play just one speaker's voice to check it, and follow the words highlighting as the audio plays.
- **People filter.** Filter the recordings list by who was in the meeting, choosing any or all of the selected people. A Clear option sits at the top of the menu.
- **Tabbed side panel.** Transcripts, summaries, the voice library, Device Manager, Models and other windows now open as tabs in a resizable panel on the right, so you can have several meetings open at once. A Close All button appears when two or more tabs are open.
- **Calendar-assisted speaker matching.** When a recording overlaps a calendar meeting, the app only considers the people invited (leaving out anyone who declined) when suggesting speaker names.
- **Build the voice library from past meetings.** A menu option goes through meetings you have already tagged and adds those voices to the library, with a progress percentage.

### Improved
- **More accurate voice matching.** The voice library now keeps several samples per person, so a voice is recognised across different microphones and meetings, and the bar for an automatic match is higher.
- **Name suggestions as you type.** Every speaker name field suggests people already in your voice library. In the Voice Library you can merge a misspelt name into the right one, and the same name typed with different capitals no longer creates a second person.
- **Tidier main window.** Device cards are more compact, with icon-only status chips and a one-line storage summary (for example "6.3/32 GB · 20% · 375 files"); disconnected devices keep the same card size. The meeting heatmap always shows a full current week plus the next week, and the window can no longer be shrunk so far that content is cut off.
- **Plaud checks.** Plaud is checked for new recordings on a schedule you can set, and pending cloud recordings download automatically.

### Fixed
- **Unconfirmed names leaking into copies.** Copy All and the saved Markdown transcript now use "Speaker 1", "Speaker 2" and so on until you confirm a name, instead of an unconfirmed guess.
- **Side-panel tabs not switching.** Clicking a tab now shows that tab's content.
- **List order on launch.** Plaud recordings no longer load after HiDock ones; the list now appears in one go.
- **Plaud audio playback.** Playing a single speaker's segment now works for Plaud recordings.

<details><summary>Every change (10)</summary>

| Version | Date | Change |
|---|---|---|
| v3.0.0 | 3 Jul 2026 | Internal: repo tidy-up (#51) |
| v3.0.1 | 13 Jul 2026 | Speaker confirmation, more accurate voice library, People filter, and side panels as tabs (#52) |
| v3.0.2 | 13 Jul 2026 | Installs and updates now always use the app's proper Developer ID signature (#53) |
| v3.0.3 | 13 Jul 2026 | Heatmap shows full weeks; window can't shrink so far that content is cut off (#54) |
| v3.0.4 | 13 Jul 2026 | Tidier device cards; Copy All uses "Speaker N" for unconfirmed speakers (#55) |
| v3.0.5 | 13 Jul 2026 | Side-panel tabs switch correctly again; new Close All button (#56) |
| v3.0.6 | 13 Jul 2026 | People filter: Clear option moved to the top of the menu (#57) |
| v3.0.7 | 13 Jul 2026 | Name suggestions on every speaker field; merge duplicate people in the Voice Library (#58) |
| v3.0.8 | 17 Jul 2026 | Saved transcripts only use names you've confirmed; bulk clear / mark-all-unknown actions (#59) |
| v3.0.9 | 17 Jul 2026 | Calendar invitees used to narrow speaker name suggestions (#60) |

</details>

## 2.1 — Meeting heatmap

**Released 25 Jun 2026** · 6 changes (v2.1.0 to v2.1.5, 21 Jun 2026 – 25 Jun 2026)

This release adds a year-at-a-glance meeting heatmap, makes the mic trigger ready the moment the app opens, and improves Plaud sign-in.

### New
- **Meeting activity heatmap.** A GitHub-style grid below the device cards shows the last year of meetings, one square per day, darker for busier days. Hover a square to see that day's meetings, total length, which devices recorded them and how many are transcribed or summarised.
- **Search your summaries from other tools.** Your summaries can now be listed, searched and read from the Terminal (`notes list`, `search`, `get`, `stats`) and by AI assistants connected to HiDock's meeting tools, filtered by type, area or date.
- **Sign out or Forget a Plaud account.** In Device Manager, Sign out ends the session but keeps the account linked so you can sign back in, while Forget removes it entirely.

### Improved
- **Mic trigger ready at launch.** The trigger now starts straight away instead of waiting about 25 seconds for devices to finish loading, so recordings made just after opening the app are not missed.
- **Plaud sign-in.** Each sign-in now starts fresh, so you can switch Plaud accounts, and the APAC region is now offered alongside US and EU.
- **Only the H1 is held.** The mic trigger now holds only the HiDock H1's microphone and leaves a connected HiDock P1 alone.

### Fixed
- **Recordings list jumping to Plaud.** The list no longer switches its filter to Plaud on its own during background downloads; filters change only when you change them.

<details><summary>Every change (6)</summary>

| Version | Date | Change |
|---|---|---|
| v2.1.0 | 21 Jun 2026 | Summaries can be listed and searched from the Terminal and by connected AI assistants (#43) |
| v2.1.1 | 21 Jun 2026 | Mic trigger starts immediately when the app launches (#44) |
| v2.1.2 | 22 Jun 2026 | Meeting activity heatmap below the device cards (#45) |
| v2.1.3 | 23 Jun 2026 | Fresh Plaud sign-in each time, APAC region, and Sign out vs Forget (#46) |
| v2.1.4 | 25 Jun 2026 | Filters no longer change on their own (#47) |
| v2.1.5 | 25 Jun 2026 | Mic trigger holds only the HiDock H1, not the P1 (#48) |

</details>

## 2.0 — AI summaries

**Released 18 Jun 2026** · 22 changes (v2.0.0 to v2.0.21, 16 Jun 2026 – 18 Jun 2026)

This release brings AI summaries into the app: each transcript can be classified by meeting type and summarised with your own templates, then read, filtered and reclassified without leaving HiDock. It also makes Plaud recordings easier to reach when you are signed out or offline.

### New
- **AI summaries.** Right-click a transcribed recording and choose Summarise with AI, or tick several and use Summarise Selected. The app works out the meeting type, picks the matching template from your Summary Templates folder and saves the summary; turn on Auto-summarise to do this for each recording transcribed during the session. It uses an AI tool you are already signed in to on your Mac (Claude, Codex, Gemini or Ollama), so no API keys are needed.
- **Summary viewer and Summary column.** A new Summary column shows which recordings have a summary; click it to open the summary in the app, with a header showing the meeting type, why it was chosen and the date. Use Reclassify to re-run the summary with a different template.
- **Filter by meeting type.** A Type menu next to Filter shows only recordings summarised as a chosen type, such as Brainstorming.
- **Built-in AI terminal.** A CLI pane (toggle it from the bottom bar) shows summaries being written live and lets you Ask AI about a recording. You can stop it opening automatically with "Show CLI while summarising" in the Models window.
- **Summary Templates manager.** Open it with the Templates button to import, create, edit or delete your summary templates, including drafting or refining one with AI.
- **Choose your AI provider.** Pick Auto, Claude, Codex, Gemini or Ollama from the Summarisation Provider menu in the menu bar or the AI Summariser picker in the Models window.
- **Finished-transcription badge.** When recordings finish transcribing while the main window is in the background, a count appears on the Dock icon (or in the menu bar), and clears when you return to the window.

### Improved
- **Plaud card.** The "Sign in required" chip now opens sign-in directly, and the card shows a storage bar and file count (it assumes a 64 GB Plaud Note Pro).
- **Plaud recordings always visible.** Recordings you have already downloaded now show when you are signed out or offline, and appear at launch together with your HiDock recordings rather than after Plaud connects.
- **Cleaner summaries.** Template instructions no longer leak into saved summaries, the date and time are filled in from the recording, and re-summarising replaces the old summary instead of adding a duplicate.
- **Fewer keychain prompts.** Locally built copies of the app are now signed consistently, so macOS stops asking again for keychain access after each update.
- **Cowork button removed.** It has been replaced by the in-app summaries.

### Fixed
- **Plaud times an hour out.** Plaud recordings' "Created" time now matches the local time in the file name.
- **Summarise failing in some builds.** Summarising no longer fails with an "invalid choice" error when the app runs from a development copy.

<details><summary>Every change (22)</summary>

| Version | Date | Change |
|---|---|---|
| v2.0.0 | 16 Jun 2026 | Plaud "Sign in required" chip now opens sign-in (#21) |
| v2.0.1 | 16 Jun 2026 | Downloaded Plaud recordings show even when signed out or offline (#22) |
| v2.0.2 | 16 Jun 2026 | Storage bar and file count on the Plaud card (#23) |
| v2.0.3 | 16 Jun 2026 | Plaud recordings appear at launch from the saved list (#24) |
| v2.0.4 | 16 Jun 2026 | Dock badge counts recordings transcribed while you were away (#25) |
| v2.0.5 | 16 Jun 2026 | The summary provider setting is now respected (#26) |
| v2.0.6 | 16 Jun 2026 | Internal: app tests now run (#13) |
| v2.0.7 | 16 Jun 2026 | Summarisation Provider menu in the menu bar (#27) |
| v2.0.8 | 16 Jun 2026 | Internal: design notes and transcription accuracy tooling (#28) |
| v2.0.9 | 16 Jun 2026 | Summaries classified by meeting type and written from your templates (#29) |
| v2.0.10 | 16 Jun 2026 | Internal: code tidy (#30) |
| v2.0.11 | 16 Jun 2026 | Summarise and Ask actions on recordings, plus Summarised/Summarising status (#31) |
| v2.0.12 | 17 Jun 2026 | Summarise Selected button, Auto-summarise toggle and Summary column (#32) |
| v2.0.13 | 17 Jun 2026 | Built-in CLI pane for Ask and summary progress, with a toggle in the bottom bar (#33) |
| v2.0.14 | 17 Jun 2026 | Live summary progress and a Summary Templates manager (#34) |
| v2.0.15 | 17 Jun 2026 | In-app summary viewer, AI picker in Models, summarise fix and fewer keychain prompts (#35) |
| v2.0.16 | 17 Jun 2026 | Auto picks the same AI tool everywhere, template edits ask before saving, Cowork removed (#36) |
| v2.0.17 | 17 Jun 2026 | Cleaner summaries and progress, Plaud times in local time, dates filled in (#37) |
| v2.0.18 | 17 Jun 2026 | Summary header with meeting type, Reclassify menu, no duplicate summaries (#38) |
| v2.0.19 | 17 Jun 2026 | Filter recordings by meeting type (#39) |
| v2.0.20 | 17 Jun 2026 | HiDock and Plaud recordings appear together at launch (#40) |
| v2.0.21 | 18 Jun 2026 | Option to keep the CLI pane closed while summarising (#41) |

</details>

## 1.1 — Notarised builds and Plaud sign-in

**Released 16 Jun 2026** · 5 changes (v1.1.0 to v1.1.4, 19 May 2026 – 16 Jun 2026)

This release makes syncing from the HiDock H1 complete and dependable, refreshes the device cards and recordings table, and fixes transcription crashes on long recordings.

### New
- **Redesigned device cards.** Each device now has its own card with a product photo, a Recording chip on the HiDock the mic trigger is holding, and an animated Connecting… chip while the app checks a device.
- **Transcribed status.** Recordings now show On device → Downloaded → Transcribed (in purple), and the progress bar shows file X of Y, percentage and stage on one line.
- **Signed and notarised app.** Release builds are now signed with an Apple Developer ID and notarised, so macOS opens downloaded copies and in-app updates without security warnings.

### Improved
- **Faster, gentler start-up.** The recordings table appears straight away from the last known list, before the devices finish connecting, and background checks no longer interrupt the mic trigger while it is recording.
- **Speaker detection.** Fixed a bias that put almost all speech on the first speaker; a real two-person meeting went from a 95/5 split to 50/50.

### Fixed
- **Missing H1 recordings.** Large H1 libraries were cut short (282 recordings showed as 143); the full list now comes through, and downloads no longer time out after a status check.
- **Cancel transcription.** Cancelling now actually stops the transcription, and Download Selected no longer starts transcribing on its own.
- **Transcription retry loop.** New recordings no longer fail and get retried over and over on Apple Silicon with the Whisper model.
- **Crashes on very long recordings.** Recordings of three hours or more no longer crash transcription with the Parakeet model.
- **Plaud showing "Connected, 0 recordings".** When your Plaud sign-in has expired, the app now says you are signed out and prompts you to sign in again.

<details><summary>Every change (5)</summary>

| Version | Date | Change |
|---|---|---|
| v1.1.0 | 19 May 2026 | New device cards, full H1 recording list, Transcribed status, better speaker detection and faster start-up (#4) |
| v1.1.1 | 19 May 2026 | Fixed transcription repeatedly failing on Apple Silicon with Whisper (#5) |
| v1.1.2 | 11 Jun 2026 | Fixed crashes when transcribing recordings of three hours or more (#11) |
| v1.1.3 | 16 Jun 2026 | Plaud now shows "signed out" instead of "connected, 0 recordings" when the sign-in has expired (#19) |
| v1.1.4 | 16 Jun 2026 | Release builds are signed and notarised by Apple (#20) |

</details>

## 1.0 — First release

**Released 24 Mar 2026** as v1.0.0.
