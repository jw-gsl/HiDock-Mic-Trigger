# HiNotes Web App — Source Mining for Device Images & Feature Ideas
Research date: 2026-04-21
Sources:
- `view-source:https://hinotes.hidock.com/device/HDP1252401895/files` (P1 device view)
- `view-source:https://hinotes.hidock.com/device/HDH1243702884/files` (H1 device view)
- Both URLs serve the same SPA shell (`/` entrypoint). The device-specific content renders client-side after auth; the two URLs produced identical 5807-byte shells. All evidence below was extracted from the public JS/CSS/asset bundle that the shell loads:
  - `/assets/js/index-Q4GamRhg.js` (~8.4 MB main bundle)
  - `/assets/js/vendor-i18n-Dw3JUflX.js`
  - `/assets/js/vendor-{react,utils,state}-*.js`
  - `/assets/css/index-DjqBGdZz.css` (~920 KB)
  - `/static/manifest.json`
- Extraction method: grep for asset paths (`/assets/**`, `/static/**`) and for quoted UI strings (`"[A-Z]...[a-z]"`) inside the minified JS. See `/tmp/hinotes_bundle/` on this machine for the raw downloaded files, `/tmp/hinotes_assets.txt` for the asset list, and `/tmp/hinotes_refined.txt` for the filtered feature-string list.

> Note on scope: the JS bundle ships a TypeScript language service (used in a power-user editor somewhere in the app), which pollutes naive grep output with compiler error messages. The extraction below strips those.

## Current State
HiNotes is HiDock's web companion for the P1 and H1 recorders. The repo here (`hidock-tools`) builds native Mac + Windows desktop apps that do the same core job (device sync + transcription + summary) but with a much narrower feature set. This plan catalogues (a) the device imagery that exists in the web bundle so we can reuse it if licensing allows, and (b) features visible in the web app that aren't in our desktop apps yet — so they can be triaged against the existing backlog.

---

## Device Images — Confirmed Present

All paths below returned HTTP 200 with real image payload when fetched directly. Download sizes noted where verified (✔ = downloaded and saved to `/tmp/hinotes_bundle/device_images/`).

### P1 product imagery
| Purpose | Asset path |
|---------|-----------|
| Front render (studio) | `/assets/png/P1_front-DC4xV_ds.png` ✔ 62 KB |
| Recording hero / marketing | `/assets/png/p1_recording-BsN2DDsb.png` ✔ 3.8 KB |
| Generic body render | `/assets/png/p1-m6HM9-a8.png` |
| P1 mini recording | `/assets/png/p1mini_recording-BSWl6EMr.png` |
| Device glyph (UI icon) | `/static/svg/device/P1.svg` ✔ — 20×20 line-art of a cassette-style recorder |

### H1 product imagery
| Purpose | Asset path |
|---------|-----------|
| Front render | `/assets/png/H1_front-BQ3b_Ken.png` ✔ 194 KB |
| Back render | `/assets/png/H1_back-DkefKLBk.png` |
| Left view | `/assets/png/H1_left-5Uiki-Op.png` |
| Earphone variant (earbuds) | `/assets/png/H1_earphone-DtI9lg4u.png` |
| Black variant | `/assets/png/h1_black-Cae64RPX.png` |
| Background / hero | `/assets/png/h1_bg-CYDK52Xt.png` |
| Recording hero | `/assets/png/h1_recording-Ci0TjHTJ.png` |
| Alt front (lowercase family) | `/assets/png/h1-front-BaVQ8ntI.png` |
| Alt rear | `/assets/png/h1-rear-DUzlKM3B.png` |
| Alt side | `/assets/png/h1-side-kARJR1Yg.png` |
| Alt earphone shot | `/assets/png/h1-earphone-6ohrNv5_.png` |
| **H1e** recording hero (earbud SKU) | `/assets/png/h1e_recording-SYf-JIKs.png` ✔ 5.7 KB |
| Device glyph (UI icon) | `/static/svg/device/H1.svg` ✔ |

**Implication:** there is evidence of an **H1e** SKU (an earbud/earphone-centric variant of H1) that our device identity system doesn't currently distinguish. Our `deviceId` convention is `hidock:<productId>` so any SKU split would surface naturally as long as the productId differs — worth double-checking against the P1/H1 products we're already pairing.

### Feature / marketing artwork (could seed a marketing-style "What's new" screen or docs)
- `/assets/png/feature-transcription-DyWAa7kL.png`
- `/assets/png/feature-summary-BPAJUZKm.png`
- `/assets/png/feature-voicemark-wpxeZRJG.png`
- `/assets/png/feature-organization-D0mExzMZ.png`
- `/assets/png/feature-retrieval-CnSXt9qM.png`
- `/assets/png/scroll-speaker-BSmj_2pn.png`
- `/assets/png/scroll-summary-D9BPM1Bc.png`
- `/assets/png/scroll-translate-CKcrbBYI.jpg`
- `/assets/png/scroll-collaboration-BCmfaQxE.png`
- `/assets/png/timetick-BxXyiVxI.png`
- `/assets/png/transcription-section-CipUp-mz.png`
- `/assets/png/view-note-C-gjWleT.png`
- `/assets/png/productive-BVGl41sk.png`, `/assets/png/unlimited-CWCjr4T-.png`, `/assets/png/pro-9IP764bL.png`

**Re-use caution:** these are HiDock's copyrighted marketing assets. Safe to display in-app if we're rendering the device's actual product image in our Device Manager (same licensing relationship as if users pair a HiDock) — but **should not** be redistributed in screenshots or docs without checking. The SVG device glyphs (`/static/svg/device/{P1,H1}.svg`) are generic line-art and low-risk for reuse.

---

## Hardware Features Revealed by UI Copy

Strings found in the bundle that describe physical-device behaviour we can take as ground truth for how HiDock hardware actually works. Useful for onboarding copy, tooltips, and for knowing what extractor telemetry we might be able to surface:

- **Long-press the HiDock button to start recording; light turns cyan.** Short-press during recording adds a **VoiceMark**.
- **BlueCatch button** (named component on H1/earphone unit): single press to connect Bluetooth earphones; long-press to disconnect.
- **Red button / Noise Cancellation slider**: "enabled when button is pushed down"; user can toggle noise cancellation on/off with a physical control.
- **OTA firmware updates** ("HiDock disconnected, OTA upgrade failed"; "Your device is already up to date.").
- **USB-A passthrough** for keyboard/mouse ("USB-A for Keyboard and Mouse Connection") — the P1 appears to be a docking station with data passthrough.
- **Auto-record all phone calls** as a device-level setting.
- **Recording quality** tiers: "Recommend for calls and most recordings" vs "Recommend for music or other high quality recording applications" — implies ≥2 bitrate/format profiles.
- **Private vs Public device**: "A private device connects only to your account. A public device is accessible to others." — account-scoped pairing model, not currently a concept in our apps.
- **Factory Reset**: "Erase all recordings, reset settings, and disassociate this device."
- **Time sync with computer**: device clock is synced over USB.

---

## Features Present in HiNotes Web — Candidates for Our Desktop Apps

Cross-referenced against `PARITY.md`, `docs/PLAN-feature-overview.md`, and `docs/PLAN-unimplemented-ideas.md`. Status column legend:
- `NEW` — not tracked anywhere in our plans
- `BACKLOG` — already in `PLAN-unimplemented-ideas.md` or similar
- `BACKEND-ONLY` — built in `shared/` but no UI (per `PLAN-feature-overview.md`)
- `DONE` — already shipped in at least one platform

### Transcript & recording interaction

| Feature | Evidence (strings) | Status | Notes |
|---------|-------------------|--------|-------|
| **VoiceMark** — user-generated in-recording markers | "Short-press to add a VoiceMark during recording.", "Show/Hide VoiceMark", "VoiceMark with Highlights" | NEW | Device button press already emits an event; extractor may already have access to marker timestamps in the recording metadata. Worth confirming by inspecting a recording's on-device file format — if present, we could render marker pins on our waveform and in the transcript. |
| Full-text search across all transcripts | "Search Whispers", "Access specific notes instantly with the search field", "Find and Replace in this Note", "Find in a summary" | BACKLOG | `PLAN-unimplemented-ideas.md → Transcript Search`. Knowledge graph (FTS5) is built in `shared/knowledge.py` but has no UI — connect those two tasks. |
| Find + Replace within a single transcript | "Find and Replace in this Note" | NEW | Discrete sub-task from global search. |
| **Re-summarize** on demand | "Re-summarize", "Rate This Summary", "How do you rate this summary?" | NEW | User feedback → regenerate. We have summarization in `shared/summarize.py` but no regenerate-with-feedback loop. |
| Summary style picker | "Choose your summary style", "Customize Summary", "Tailored Summary Templates", "Summary with action items", "Psychotherapy Note" (example specialised template) | BACKLOG-adjacent | Our summarize.py has one prompt; HiNotes ships specialised templates per meeting type. Extend summarize.py with selectable templates + a UI picker. |
| Note templates with test-on-existing-note preview | "Note Templates", "Select a Note to Test the Template", "Please instruct instructions for this note. Include at least three examples." | NEW | Natural extension of the summary-style picker. |
| **Live/full translation** of transcript + summary | "Start/End Translation", "Choose a Language To Translate into", "Translate in Seconds", "Summary translation", "Keep Translating?", "Primary language for better accuracy. Other languages will also be translated." | NEW | We have i18n in the UI (en/zh/ja) but no translation of content. Could route through existing LLM call. |
| Speaker memory across meetings | "Speaker memory", "Required for speaker identification" | DONE | Our voice library + `voice_training.py` already does this. Align terminology and surface it in onboarding. |
| Merge multiple recordings into a single note | "Failed to merge whisper notes" | Partial | We have audio-file merge (`PLAN-feature-overview` Merge recordings) — extend to merge transcripts + summaries too. |
| Retry failed transcription | "Failed to retry whisper.", "Click to retry" | DONE | Right-click → Retry already works. |

### Notes, todos, and organisation

| Feature | Evidence | Status | Notes |
|---------|----------|--------|-------|
| **Automatic To-Do extraction from notes + todo manager** | "Add To-Dos manually, or they'll be created automatically from your notes.", "Failed to fetch open todos", "Search To-Do", "No archived To-Dos", "Failed to update todo smart label" | BACKEND-ONLY | `shared/intelligence.py` does commitment tracking and `shared/knowledge.py` has action items. Currently no UI. This is the single biggest product gap vs HiNotes. |
| **Folders + Tags** for transcripts | "Folders and Tags", "Move to Folder", "Smart folder", "Add Folder", "Folder deleted" | NEW | Our recordings table is flat. Could add a sidebar tree. |
| **Archive / Unarchive** workflow | "Archive", "Archived", "Unarchived", "Undo archive failed:", "Your archived items will appear here." | NEW | Distinct from Delete — soft-hide old recordings. |
| Password-protect an individual note | "Protect with Password", "You need a password to access this content." | NEW | Niche; consider only if legal/compliance asks. |
| Rich note editor (BlockNote) with blocks, code, tables, toggles, emoji, audio embeds | "BlockNoteEditor", "Table with editable cells", "Code block with syntax highlighting", "Embedded audio with caption", "Toggle Heading/List", "Search for and insert an emoji" | NEW (large) | Big lift. Probably skip — our transcripts live as plain markdown and can be edited by the user's editor of choice. |
| Outline view | "Outline" | NEW | Cheap to add — regex pass over headings. |
| Unread-note indicator | "Unread note" | NEW | Track first-open state per transcript. |

### Integrations

| Feature | Evidence | Status | Notes |
|---------|----------|--------|-------|
| **Calendar integration — read events to enrich summaries** | "Connect Outlook and Teams for smarter summaries.", "Connect Your Calendar", "Read all scheduled calendar events to optimize the accuracy of note summaries.", "This calendar account is already connected to another HiNotes account." | NEW | We can hit the same idea locally on macOS via `EventKit` (no auth flow needed) — pipe calendar events from around the recording time into the summarize prompt. Big quality win for low effort. |
| **Calendar integration — write events from notes** | "Allow HiNotes to automatically extract pending meeting schedules from your notes and send them to your calendar.", "Event Generated by HiNotes:", "Add to Calendar", "Added to Calendar." | NEW | LLM extracts scheduled follow-ups → `.ics` or EventKit write. |
| **Google Calendar / Outlook / Microsoft Calendar / Work Calendar / Google Meeting** connectors | "Google Calendar", "Outlook Calendar", "Microsoft Calendar", "Work Calendar", "Google Meeting" | NEW | On Mac: use EventKit (unified). On Windows: MAPI / Graph API. Lower priority than local calendar read. |
| **Send to Notion / Google Docs / OneNote** | "Send to Notion", "Sent to Notion successfully.", "Note sent to Google Docs.", "Note sent to OneNote.", "Integration with Google, Microsoft OneNote and Notion" | NEW | Our current export is `.md` file only. Add share targets. |
| **Multi-format export** | "Export notes in TXT, CSV, SRT, Markdown, Word, and PDF" | Partial | We produce `.md`. Add SRT + PDF + Word for practical wins. SRT falls straight out of diarized segments. |
| Include timestamps / speaker names toggle on export | "Include speaker names", "Include timestamps" | NEW | Trivial once export options dialog exists. |

### Device management

| Feature | Evidence | Status | Notes |
|---------|----------|--------|-------|
| **OTA firmware update** from the app | "OTA Update and Device Management", "Over-the-air firmware updates for your devices.", "Your device is already up to date." | NEW | Big initiative — likely blocked by HiDock's firmware distribution mechanism. **Estimate — not confirmed:** we don't ship firmware; this might require cooperation from HiDock. Flag as research-first. |
| **Bluetooth device pair from app** (for H1e earbuds and similar) | "Bluetooth Pairing Button", "Scan and connect to your Bluetooth earphones", "Nearby Devices", "Pair Now", "Pair your headphones" | NEW | macOS: CoreBluetooth; Windows: WinRT Bluetooth APIs. Only worth doing if our apps are the primary way users set up earbuds. |
| **Factory reset / disassociate device** | "Factory Reset This Device?", "Yes, Disassociate This Device", "This will erase all recordings and whispers, reset all settings, and disassociate this device from your account." | NEW | Safety-critical — needs multi-confirm. |
| Public vs Private device | "A private device connects only to your account. A public device is accessible to others.", "This device is publicly accessible." | NEW | Only meaningful if we also have account/cloud concept — currently we don't. Park. |
| Storage almost full / low warnings | "Low storage space", "Storage almost full", "Storage is completely full." | NEW | We already read device storage stats; adding a low-storage banner is cheap. |
| Auto-transfer on recording complete | "Automatically transfer new recordings when recording is complete" | DONE | We have auto-download on refresh + auto-download-new. Alignment check only. |
| Notify when recording starts on device | "Notify About Recording", "Recording Started" | NEW | Extractor status poll already knows — add a tray notification. |

### Account, billing, quota

| Feature | Evidence | Status | Notes |
|---------|----------|--------|-------|
| Membership / Pro / Unlimited / Lifetime Pro tiers + pricing UI | "Choose Your Membership Plan", "Buy Pro", "Buy Unlimited", "Lifetime Unlimited Pro", "Select Pro Quota Pack", "Renew Subscription", "Pay-as-you-use, quota-based" | N/A | Our apps are local-only. Parking — different business model. |
| Free-quota redemption codes | "Use the code provided to you to get free quota.", "You have already claimed the free quota." | N/A | Same. |

### Misc polish

| Feature | Evidence | Notes |
|---------|----------|-------|
| Onboarding: "Connect Device", "Pair Now", 5-step wizard | "Connect HiDock to transfer recordings, or upload audio to get started.", "Connect your HiDock device to unlock membership benefits" | Our onboarding wizard exists (PARITY.md) — useful copy reference. |
| Rich empty states with illustrations | `/assets/svg/empty_archived`, `/assets/svg/empty_completed`, etc. | Cheap polish — we use basic empty text. |
| Apple / Google sign-in | "Sign In with Google", "Apple sign in", `apple_logo.svg`, `google_logo.svg` | N/A for local apps. |
| GDPR / data deletion flow | "GDPR Compliance", "Restore HiNotes Account and Data", "If you choose to delete a meeting note from your HiNotes account, it will be permanently and irreversibly removed…" | N/A — local files. |
| i18n: en / zh / ja | Bundle shipping `lang-en/zh/ja` images + bootstrap lang detector | Our Swift app is en-only AFAIK — could extend if demand. |

---

## Completed
- [x] Downloaded HTML shell + main JS bundle + vendor chunks + CSS + PWA manifest from hinotes.hidock.com
- [x] Extracted and catalogued 145 asset paths (mostly `/assets/png/**` and `/assets/svg/**`)
- [x] Verified P1 and H1 product imagery exists and is fetchable (sample downloads saved to `/tmp/hinotes_bundle/device_images/`)
- [x] Extracted ~1400 feature-related UI strings from the minified JS and filtered out TypeScript compiler noise
- [x] Cross-referenced findings against `PARITY.md`, `PLAN-feature-overview.md`, `PLAN-unimplemented-ideas.md`

## In Progress
- [ ] Awaiting user input on which candidate features to promote to their own plan files

## Planned
Triage priorities for the user to pick from (no ordering implied — priority depends on user's roadmap call):

- [ ] **Calendar read for summary enrichment (local)** — cheap, big quality win, macOS EventKit avoids auth dance
- [ ] **Hardware VoiceMark capture** — investigate whether HiDock's on-device recording metadata includes marker timestamps; if so, render them in the transcript viewer
- [ ] **Multi-format export (SRT + PDF + Word)** — merges with existing export plan; SRT is free from diarization output
- [ ] **Template-based summaries** — extend `shared/summarize.py` with selectable prompt templates + a UI picker; test-against-existing-note preview
- [ ] **Full-text transcript search UI** — connect `shared/knowledge.py` FTS5 to a search box in the main window
- [ ] **Todo manager UI** — surface `shared/intelligence.py` commitment tracking + `shared/knowledge.py` action items in a dedicated view
- [ ] **Folders + Tags for transcripts** — sidebar tree for the recordings table
- [ ] **Device images in Device Manager** — use P1/H1 glyph SVGs instead of emoji/SF Symbols (licensing check needed before shipping marketing PNGs)
- [ ] **H1e SKU recognition** — verify our device identity handles an earbud-variant H1 with a different productId; add to device-image lookup
- [ ] **Low-storage banner** — read value we already poll, surface as warning
- [ ] **Recording-start tray notification** — fire when poll detects a device has begun a new recording
- [ ] **Send to Notion / Google Docs / OneNote** — export targets beyond local file
- [ ] **Re-summarize with feedback** — rate + regenerate loop tied to the summary view

## Rejected / Not Applicable
- **Rich BlockNote editor** — our transcripts are `.md` and users bring their own editor; re-building a rich editor is a huge sink for marginal value.
- **Account / billing / quota** — we're local-only by design.
- **Public-vs-Private device** — requires cloud account concept we don't have.
- **Apple/Google sign-in, GDPR deletion flow** — same reason.

## Open questions for the user
1. Which of the "Planned" items (if any) should be promoted to their own `PLAN-*.md` files?
2. Should device imagery (the actual PNGs) be vendored into the repo, or should we stick to the neutral SVG glyphs only?
3. Is the H1e SKU already on the radar, or is that news?

## Sources (raw files on this machine)
- `/tmp/hinotes_bundle/index.js` — main SPA bundle
- `/tmp/hinotes_bundle/vendor-*.js`, `css.css`, `manifest.json` — supporting chunks
- `/tmp/hinotes_assets.txt` — 145 unique asset paths
- `/tmp/hinotes_refined.txt` — 890 filtered UI strings
- `/tmp/hinotes_bundle/device_images/` — sample P1/H1 product images + SVG glyphs
