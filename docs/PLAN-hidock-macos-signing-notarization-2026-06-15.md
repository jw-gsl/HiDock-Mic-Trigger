# HiDock macOS app — Developer ID signing + notarization (plan, not yet built)

Date: 2026-06-15
Status: **Planned** — documented for a later decision. No workflow changes made.
Scope chosen: explain only (see options at bottom).

## Current state
- `build-macos.yml` (per-push CI artifact) and `release.yml` (published GitHub Release)
  both **ad-hoc sign** the bundle (`codesign --force --sign -`) and **do not notarize**.
- The app has a **GitHub-releases auto-updater** (`UpdateChecker.swift`): it polls
  `releases/latest`, downloads the `*macOS*.zip` asset, unzips, swaps the app, and
  relaunches. Downloaded zips carry a quarantine flag, so an ad-hoc/unnotarized
  update can be blocked by Gatekeeper on relaunch → **notarizing fixes the updater too.**
- Signing secrets already exist (added for plaud-sync) and are reused as-is:
  `APPLE_CERTIFICATE` (base64 .p12), `APPLE_CERTIFICATE_PASSWORD`,
  `APPLE_SIGNING_IDENTITY` = `Developer ID Application: James Whiting (ZFFL33SU92)`,
  `APPLE_API_ISSUER`, `APPLE_API_KEY` (key id), `APPLE_API_KEY_BASE64` (.p8).

## Why this is harder than plaud-sync
plaud-sync is a single Tauri binary; `tauri-action` deep-signs + notarizes it. The
HiDock bundle contains **two relocatable Python venvs** (pyusb, numpy, pywhispercpp →
native `.so`/`.dylib`), a copied Python interpreter, the `mic-trigger` CLI binary, and
SwiftTerm. Apple's notary requires **every** nested Mach-O to be Developer-ID-signed
with **hardened runtime** + secure timestamp, and the Python interpreter needs
entitlements or the notarized app crashes on launch. This is the classic pain point and
usually takes **a few CI iterations** (read `notarytool log` to see which binary failed).

## Approach (for `release.yml`, and mirror into `build-macos.yml` if desired)

### 1. Import the Developer ID cert into a temp keychain (CI step)
```bash
KEYCHAIN=build.keychain; KP="$(uuidgen)"
security create-keychain -p "$KP" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security unlock-keychain -p "$KP" "$KEYCHAIN"
echo "$APPLE_CERTIFICATE" | base64 --decode > /tmp/devid.p12
security import /tmp/devid.p12 -k "$KEYCHAIN" -P "$APPLE_CERTIFICATE_PASSWORD" -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s -k "$KP" "$KEYCHAIN"
security list-keychains -d user -s "$KEYCHAIN" login.keychain
rm -f /tmp/devid.p12
```

### 2. Entitlements file (`hidock-mic-trigger/signing/entitlements.plist`)
Start broad, then trim based on notary/crash feedback:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.allow-dyld-environment-variables</key><true/>
</dict></plist>
```
`disable-library-validation` is the critical one (lets the signed Python load
pip-installed native libs not signed by us).

### 3. Deep-sign inner → outer (replaces the ad-hoc step)
```bash
ID="$APPLE_SIGNING_IDENTITY"; ENT=hidock-mic-trigger/signing/entitlements.plist
APP="$BUILD_DIR/$APP_NAME"
OPTS=(--force --options runtime --timestamp --sign "$ID")
# nested dylibs / .so (libraries: runtime+timestamp, no entitlements needed)
find "$APP/Contents/Resources" -type f \( -name "*.dylib" -o -name "*.so" \) -exec codesign "${OPTS[@]}" {} \;
# python interpreters + any bin executables (need entitlements)
find "$APP/Contents/Resources" -type f -path "*/bin/*" -perm +111 -exec codesign "${OPTS[@]}" --entitlements "$ENT" {} \;
# the mic-trigger CLI + anything in MacOS/
find "$APP/Contents/MacOS" -type f -exec codesign "${OPTS[@]}" --entitlements "$ENT" {} \;
# frameworks (SwiftTerm)
find "$APP/Contents/Frameworks" -name "*.framework" -maxdepth 1 -exec codesign "${OPTS[@]}" {} \; 2>/dev/null || true
# outer app last
codesign "${OPTS[@]}" --entitlements "$ENT" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
```

### 4. Notarize + staple
```bash
echo "$APPLE_API_KEY_BASE64" | base64 --decode > /tmp/AuthKey.p8
ditto -c -k --sequesterRsrc --keepParent "$APP" /tmp/notarize.zip
xcrun notarytool submit /tmp/notarize.zip \
  --key /tmp/AuthKey.p8 --key-id "$APPLE_API_KEY" --issuer "$APPLE_API_ISSUER" --wait
xcrun stapler staple "$APP"            # staple the .app (zips aren't stapled)
# Re-zip the *stapled* app for distribution / the auto-updater:
ditto -c -k --sequesterRsrc --keepParent "$APP" "$BUILD_DIR/HiDock-Mic-Trigger-macOS.zip"
spctl -a -t exec -vvv "$APP"           # expect: accepted, source=Notarized Developer ID
rm -f /tmp/AuthKey.p8
```
On failure: `xcrun notarytool log <submission-id> --key … --key-id … --issuer …` lists
the exact unsigned/bad binary to fix.

## Gotchas specific to this bundle
- The "Make venvs relocatable" step copies the system Python into the venv **before**
  signing — so sign **after** that step (the copied binary must be signed, not the
  original). Order in the workflow: build → bundle resources → make relocatable →
  **sign** → notarize → staple → zip.
- Re-zip **after** stapling, or the downloaded update won't carry the ticket.
- `release.yml` and `build-macos.yml` each have their own venv-bundling; both need the
  sign/notarize block if signing both.
- Iterate via `workflow_dispatch` — no local `xcodebuild` needed (won't touch the dev machine).

## Auto-updater interaction
Once releases are notarized + stapled, the updater's download→unzip→relaunch works
cleanly (no Gatekeeper prompt on the swapped app). No `UpdateChecker.swift` change needed.

## Options considered (2026-06-15)
- **release.yml only** — highest value (users + updater consume it).
- **release.yml + build-macos.yml** — complete, ~2× surgery + CI iteration.
- **Sign now, notarize later** — quicker/deterministic, but Gatekeeper still warns on
  first download until notarized.
- **Explain only (chosen)** — this document.
