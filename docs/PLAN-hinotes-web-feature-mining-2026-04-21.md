# HiNotes Web App — Source Mining, Image Integration, Feature Triage
Research date: 2026-04-21
Last updated: 2026-04-21 (critical assessment added after user review)

Sources:
- `view-source:https://hinotes.hidock.com/device/HDP1252401895/files` (P1 device view)
- `view-source:https://hinotes.hidock.com/device/HDH1243702884/files` (H1 device view)
- Both URLs serve the same SPA shell (5807-byte HTML); device-specific content renders client-side after auth. All evidence extracted from the public JS/CSS/asset bundle:
  - `/assets/js/index-Q4GamRhg.js` (~8.4 MB main bundle)
  - `/assets/js/vendor-i18n-Dw3JUflX.js`
  - `/assets/js/vendor-{react,utils,state}-*.js`
  - `/assets/css/index-DjqBGdZz.css` (~920 KB)
  - `/static/manifest.json`
- Extraction method: grep for asset paths (`/assets/**`, `/static/**`) and quoted UI strings in the minified JS. Raw files preserved at `/tmp/hinotes_bundle/` (non-repo).
- Product imagery now vendored into the repo at `assets/device-images/` — see table below.

## Current State
HiNotes is HiDock's web companion for the P1 and H1 recorders. The repo here (`hidock-tools`) builds native Mac + Windows desktop apps that cover the same core job (device sync + transcription + summary) with a much narrower feature set. This plan:
1. Catalogues the device imagery we've vendored into the repo.
2. Plans how to wire the small glyphs / recording PNGs into the existing `hidockDeviceIcon()` helper on macOS and the emoji-based equivalent on Windows so the user sees which device is connected.
3. Triages the full feature set visible in the web app against what we actually want in our desktop apps, with a yes/no/later verdict per item.

---

## Device Images — Vendored into `assets/device-images/`

All files downloaded from hinotes.hidock.com to `/Users/jameswhiting/_git/hidock-tools/assets/device-images/`. Renamed to clean filenames (dropped the Vite content hashes).

| File | Size | Content | Use in our apps |
|------|------|---------|-----------------|
| `P1_glyph.svg` | 1.7 KB | 20×20 line-art of a handheld recorder with speaker grille | **Ship** — replace SF Symbol / emoji in Device Manager rows and menu bar |
| `H1_glyph.svg` | 2.9 KB | 20×20 line-art of a dock + round earphone | **Ship** — same role as P1 glyph |
| `connected_glyph.svg` | 0.7 KB | 14×14 green tick-in-circle | **Ship** — replace the "Connected" text badge with an icon+text chip |
| `P1_recording.png` | 3.8 KB | Tiny PNG of P1 with red recording LED | **Ship** — "device is live / recording" badge |
| `H1_recording.png` | 5.4 KB | Tiny PNG of H1 dock with red LED | **Ship** — same role |
| `H1e_recording.png` | 5.7 KB | Tiny PNG of H1e earbud variant with red LED | **Ship if/when we support H1e** |
| `P1mini_recording.png` | 3.5 KB | Tiny PNG of P1 mini variant | Speculative — we haven't seen a P1 mini in the wild |
| `P1_front.png` | 62 KB | Full studio render of P1 | Optional — product detail view only |
| `P1_alt.png` | 24 KB | Alt P1 render | Optional |
| `H1_front.png` | 194 KB | Full studio render of H1 | Optional |
| `H1_front_alt.png` | 204 KB | Alt front render | Redundant with `H1_front.png` |
| `H1_back.png` / `H1_left.png` / `H1_rear.png` / `H1_side.png` | 57–123 KB each | Rotational renders | Only needed if we build a product tour |
| `H1_earphone.png` / `H1_earphone_alt.png` | 36 KB each | H1 earbud renders | Only needed if we ship H1e support |
| `H1_black.png` | 80 KB | Black colourway | Only needed if we expose colourway |
| `H1_bg.png` | 1.5 MB | Marketing wallpaper | **Don't ship** — kept for reference only. Consider deleting if we want a lighter repo. |

**Licensing caveat.** These are HiDock's copyrighted product images. Our apps are companion software for HiDock hardware, which is the same relationship their own web app has, so rendering the device glyph alongside a paired device is a defensible use. That said:
- The small glyphs (`*_glyph.svg`) are generic line-art and the lowest-risk assets. **Default to shipping these first.**
- The recording PNGs (`*_recording.png`) are photographic-style renders; lower-risk than the big studio shots but still HiDock's artwork. Worth a note in the README crediting HiDock.
- The full-resolution studio renders (`*_front.png`, `*_back.png`, etc.) should not be shipped in screenshots, marketing pages, or social cards without HiDock's OK.

---

## Integrating the Glyphs + Recording Badges into Our UI

The hooks already exist on both platforms, so this is a swap, not new plumbing.

### macOS (Swift / SwiftUI)

Current state, `hidock-mic-trigger/Sources/Helpers.swift:50`:
```swift
func hidockDeviceIcon(_ shortName: String, deviceType: DeviceType = .hidock) -> String {
    // returns an SF Symbol name: "hifispeaker" for H1, "waveform.and.mic" for P1
}
```
Used at `DeviceManagerView.swift:167` via `Image(systemName: deviceIcon)`.

**Integration steps (blocks, not hours):**
1. Add `P1`, `H1`, `H1e`, `Connected`, `P1Recording`, `H1Recording`, `H1eRecording` image sets to `hidock-mic-trigger/Assets.xcassets/`. Each imageset gets a `Contents.json` with the SVG/PNG as universal asset.
2. Add a sibling helper `hidockDeviceImage(shortName:deviceType:recording:) -> Image?` that returns the bespoke asset when the short name matches a known SKU, otherwise `nil`.
3. Update `DeviceCardView` so: if `hidockDeviceImage(...)` returns an `Image`, render that at ~24pt; otherwise fall back to the existing SF Symbol. **Fallback is important** — we shouldn't break UI for USB volume devices or any future SKU.
4. Update the menu bar attribution (`hidockDeviceEmoji`): keep returning emoji for text-only menu bar contexts — the menu bar can't render images inside a text menu item reliably. No regression there.
5. Add a "recording now" overlay: when `ViewModel.deviceIsRecording(productId)` is true, swap the glyph for `*_recording.png` (or overlay a red dot). Our extractor already polls device status — this state exists, we just don't visualise it.

### Windows (PyQt6)

Current state, `Windows-App/ui/device_manager_dialog.py:102`:
```python
def _update_icon(self):
    # Unicode emoji: 🎙 (mic), 🔊 (speaker), 💾 (drive), 🔌 (plug)
```

**Integration steps:**
1. Create `Windows-App/resources/device-images/` and copy (or symlink from build script) the same seven small assets. Leaving the source of truth at the repo-root `assets/device-images/` path and copying at build time avoids drift.
2. Replace `self.icon_label.setText(emoji)` with `self.icon_label.setPixmap(QPixmap(path).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation))` when a known SKU image is available; fall back to emoji otherwise.
3. Add the same "recording" swap as macOS when the device reports an active recording.
4. Keep the emoji path as the fallback so dark-mode / high-DPI / missing-file cases degrade gracefully.

### Surface areas that benefit
1. **Device Manager rows** — main win. Today a 🎙 emoji vs a tiny P1 line-drawing is the difference between "generic device" and "this is my recorder".
2. **Main window header / status chip** — when a device is paired and connected, show `connected_glyph.svg` + short name + device glyph instead of plain text.
3. **Menu bar / tray tooltip (hover text only)** — keep emoji.
4. **Onboarding step 2 ("Connect")** — show the glyph of each supported SKU so the user knows what to plug in.
5. **Recording toolbar** — when the device is actively recording, swap to `*_recording.png` so there's an unmistakable live indicator.

### H1e SKU — open question
The web bundle contains `h1e_recording.png`, which means HiDock ships (or ships soon) an earbud-form-factor H1 variant. Our device identity is `hidock:<productId>` and the SKU fork is probably a different productId. Before implementing we should:
- Inspect whether our paired devices have ever reported anything other than productId `45068`.
- If yes, add the new PID to `hidockDeviceIcon()`'s name-matcher.
- If no, leave `H1e_recording.png` in the repo but don't wire it into the matcher until we actually see one on a user's USB bus.

---

## Feature Triage — Critically Assessed

User directions recorded during review:
- **Live translation — NO** (confirmed by user).
- **Dedicated to-do view — NO** (user will handle todos in a separate cowork app).
- **Storage warnings — YES**.
- **Multi-format export — sceptical** (user asked me to critically assess).

Legend:
- 🟢 **Ship** — worth building, reasoning below.
- 🟡 **Later** — plausible, not blocking — park in the backlog with a trigger condition.
- 🔴 **Skip** — actively rejected or unjustifiable for our scope.

### Device management & hardware

| Item | Verdict | Reasoning |
|------|---------|-----------|
| **Device glyphs + recording badge** (this plan's integration section) | 🟢 | Low-risk polish that directly uses assets we now vendor. Replaces emoji with the real thing. |
| **Storage almost full / low storage banner** | 🟢 | We already poll device storage in the extractor; surfacing a banner when free-space < threshold is a small patch to the main window status area. User explicitly said yes. Needs a tunable threshold (suggest 10% or 200 MB, whichever comes first — **this threshold is a guess; validate against a real HiDock device before shipping**). |
| **Recording-start tray notification** | 🟢 | Extractor status poll already knows when a new recording appears. Firing a tray / UNUserNotification on first-seen is one-screen of code on each platform. Satisfies the "passive awareness" slot without the user having to look. |
| **Auto-transfer when recording complete** | 🟢 (already have) | We have auto-download on refresh + auto-download-new. Pure alignment check — no new work. |
| **Factory reset / disassociate device** | 🟡 | Matches a real user need (giving a device away, resetting before return), but implementing "erase all recordings on the device" safely needs confirmation of the extractor command surface. Put behind a multi-confirm dialog when we do build it. |
| **Public vs Private device** | 🔴 | Only meaningful with a cloud account concept we don't have and don't plan to add. |
| **OTA firmware update from our app** | 🔴 | We don't own the firmware distribution. Would need HiDock cooperation. Not our mission. |
| **Bluetooth earbud pair from our app** | 🔴 | macOS / Windows both let users pair BT devices at the OS level. Duplicating that in-app is UX noise. |
| **Auto-record all phone calls** | 🔴 | Device-level setting on the hardware — not something the desktop app should expose. |

### Transcript & recording interaction

| Item | Verdict | Reasoning |
|------|---------|-----------|
| **VoiceMark capture + render** | 🟢 | If the device writes marker timestamps into the recording file, we can show them as pins on the waveform and clickable anchors in the transcript. **Requires a spike first**: download a recording made with VoiceMark presses and inspect the binary for marker chunks. Worth a small investigation plan before committing to UI. |
| **Full-text transcript search UI** | 🟢 | `shared/knowledge.py` already has FTS5. This is a UI-only task against existing backend — high leverage. Already in `PLAN-unimplemented-ideas.md` under "Transcript Search". |
| **Find + Replace within a single transcript** | 🟡 | Useful niche. Small surface. Park until someone asks. |
| **Re-summarize on demand + rate summary** | 🟢 | Users who care about summary quality want a "that was bad, try again" button. Backend already supports re-running summarize; UI is a button + optional rating capture. |
| **Template-based summaries (Note Templates)** | 🟢 | HiNotes's "Psychotherapy Note" / "Summary with action items" templates imply a selectable-prompt system. Extending `shared/summarize.py` with a prompt registry + UI picker is modest work and a real quality unlock for users with niche meeting types. |
| **Live / full translation of transcript + summary** | 🔴 | **User rejected.** Noted: not a priority for this user base. |
| **Speaker memory across meetings** | 🟢 (already have) | Our voice library already does this. Surface it more clearly in onboarding copy — terminology alignment with HiNotes helps. |
| **Merge multiple recordings into one note** | 🟡 | We already merge audio files. Extending to merged-transcript output is plausible. Not urgent. |
| **Retry failed transcription** | 🟢 (already have) | Right-click → Retry already works. |

### Notes, todos, and organisation

| Item | Verdict | Reasoning |
|------|---------|-----------|
| **Automatic To-Do extraction + dedicated todo view** | 🔴 | **User rejected for this app.** Todos will live in the separate cowork app. However: `shared/knowledge.py` already extracts action items into frontmatter, and `shared/intelligence.py` does commitment tracking — those stay. We just don't surface a todo UI here. **Action:** make sure the action-item metadata in the transcript markdown frontmatter is stable enough that cowork can consume it. Worth a small coordination check before cowork starts on this. |
| **Folders + Tags for transcripts** | 🟡 | The flat recordings table is going to hurt once users hit 200+ recordings. Tags are especially cheap — use whatever tags `summarize.py` already emits into frontmatter as a filter chip row. Park until recordings-table scale becomes a real complaint. |
| **Archive / Unarchive** | 🟡 | Pairs naturally with Folders+Tags. Same trigger condition. |
| **Password-protect a single note** | 🔴 | Niche compliance feature. Our files are on the user's disk; OS-level FileVault / BitLocker is the right level. |
| **Rich editor (BlockNote)** | 🔴 | Huge lift for minor value. Transcripts are `.md`; users bring their own editor. Not our job to be Notion. |
| **Outline view** | 🟡 | Regex-over-headings is trivial. Only worth doing if someone actually opens big transcripts in-app. |
| **Unread-note indicator** | 🔴 | Low signal. Our current "you haven't opened this" state is the modification time. |

### Export & integrations

| Item | Verdict | Reasoning |
|------|---------|-----------|
| **Multi-format export — SRT, PDF, Word, CSV** | 🟡 with a caveat | **User pushback noted.** My honest take: **SRT is the only one of these with a clear use case** — it drops out of diarized segments for free and lets users caption recorded video. PDF and Word are solved by "Export as PDF" in any markdown viewer, and CSV for transcripts is a weird fit. Verdict: **ship SRT, skip the rest** unless users actually ask. That downgrades this from "whole multi-format export feature" to "add SRT as an export option next to the existing .md" — a small change. |
| **Include timestamps / speaker names export toggles** | 🟡 | Only matters once SRT (or any second format) lands. |
| **Send to Notion / Google Docs / OneNote** | 🔴 | Each target is an OAuth integration with its own maintenance tax. For the small number of users who want this, copy/paste from the `.md` file works. Defer unless explicitly requested. |
| **Calendar read — pull events around recording time for richer summary context** | 🟢 | Biggest quality win in the whole list. On macOS: EventKit is local and requires only a one-time permission, no OAuth. On Windows: Outlook MAPI or Graph (if Office 365). **Macro impact:** summaries that know "this was a 1:1 with Dave" are qualitatively better than summaries that don't. Worth its own plan. |
| **Calendar write — create events from notes** | 🟡 | Natural pair with calendar read. Lower priority because extracting durable events from casual notes is LLM-quality-dependent and easy to get wrong. Ship read first, see if users want write. |
| **Apple / Google sign-in / GDPR deletion / Quota / Billing** | 🔴 | All cloud-account concerns; we're local-first. |
| **i18n (zh / ja / en)** | 🟡 | Our Swift app is en-only. Only worth doing if demand surfaces. |

### Polish

| Item | Verdict | Reasoning |
|------|---------|-----------|
| **Illustrated empty states** (`empty_archived.svg`, `empty_completed.svg` etc.) | 🟡 | Cheap polish. Do it when we tidy the empty states anyway. |
| **Onboarding copy alignment with HiNotes** | 🟢 | Our onboarding is already 5 steps (per PARITY.md). Re-using HiNotes's proven copy for "Long-press the HiDock button until the light turns cyan" etc. is better than the fiction we'd write ourselves. No code change — just copy. |

---

## Completed
- [x] Downloaded HTML shell + main JS + vendor chunks + CSS + PWA manifest from hinotes.hidock.com
- [x] Catalogued 145 asset paths and extracted ~1400 feature-related UI strings (TypeScript compiler noise filtered)
- [x] Vendored 17 P1 / H1 / H1e images + 3 glyph SVGs into `assets/device-images/`
- [x] Traced existing device-icon plumbing on both platforms (`hidockDeviceIcon()` in Swift, `_update_icon()` in `Windows-App/ui/device_manager_dialog.py`)
- [x] Critical assessment of every candidate feature with a yes/no/later verdict, incorporating user directions (no live translation, no todo view, yes storage warnings, sceptical on multi-format export)

## In Progress
- [ ] None — awaiting user sign-off on which 🟢 items to promote to their own implementation plans

## Planned — Promoted from 🟢 verdicts

Ranked by estimated leverage (quality-per-effort), not calendar order:

- [ ] **Device glyphs + recording badge wiring** — `assets/device-images/` → `Assets.xcassets` + `Windows-App/resources/device-images/`; update `hidockDeviceIcon()` on Mac and `_update_icon()` on Windows with SKU→image matcher + fallback
- [ ] **Low-storage banner** — read extractor's already-polled storage %, banner at threshold (threshold TBD — validate with a real device)
- [ ] **Recording-start tray notification** — fire when extractor poll first sees a new recording on the device
- [ ] **Full-text transcript search UI** — connect `shared/knowledge.py` FTS5 to a search box in the main window
- [ ] **Calendar read for summary enrichment** — own plan file needed; EventKit on Mac, Graph/MAPI on Windows
- [ ] **Template-based summaries** — extend `shared/summarize.py` with a prompt registry + UI template picker; test-against-existing-note preview
- [ ] **Re-summarize on demand** — button in summary view; optional thumbs-up/down capture
- [ ] **Onboarding copy alignment with HiNotes** — copy change only
- [ ] **SRT export** — one new export option (not a multi-format export feature)
- [ ] **VoiceMark spike** — investigation first: does the recording file format carry marker timestamps? If yes → UI plan; if no → drop to 🔴.
- [ ] **Coordination check with cowork on action-item frontmatter schema** — so cowork's todo view can reliably consume our transcripts

## Rejected
- Live translation (user-rejected)
- Dedicated to-do view in this app (user will handle in cowork)
- BlockNote rich editor
- Password-protected notes
- Public-vs-private device concept
- OTA firmware update from our app
- Bluetooth earbud pairing from our app
- Auto-record all phone calls setting
- Send-to Notion / Google Docs / OneNote integrations
- PDF / Word / CSV export (SRT only — see above)
- Apple/Google sign-in, GDPR flow, quota/billing
- Unread-note indicator

## Open questions
1. Should I go ahead and wire the device glyphs (🟢 top of the Planned list) now, or do you want to review the `assets/device-images/` set first?
2. Is the ~1.5 MB `H1_bg.png` marketing wallpaper worth keeping in the repo, or should I delete it? (It's flagged Don't-Ship; it's only useful as reference art.)
3. Before we wire images, do we want credit-to-HiDock copy anywhere (README, About dialog)?
4. The VoiceMark spike needs a real recording made with button-presses during it — can you make one, and I'll inspect the binary?

## Sources (local, non-repo)
- `/tmp/hinotes_bundle/index.js` — main SPA bundle
- `/tmp/hinotes_bundle/vendor-*.js`, `css.css`, `manifest.json` — supporting chunks
- `/tmp/hinotes_assets.txt` — 145 unique asset paths
- `/tmp/hinotes_refined.txt` — 890 filtered UI strings
