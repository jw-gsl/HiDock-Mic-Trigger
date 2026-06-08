# Device Provider Architecture

HiDock Tools treats every recording source as a paired device provider. A provider can be a physical USB device, a mounted filesystem volume, or a cloud account such as Plaud. The app should not need bespoke table logic for each provider: once paired, the provider owns discovery/download/auth, then emits the same recording contract as HiDock.

Current providers:

| Provider | Device type | Transport | Extractor commands |
|---|---|---|---|
| HiDock H1/P1 | `hidock` | Vendor USB protocol | `status`, `download`, `download-new` |
| Mounted recorder/SD card | `volume` | Local filesystem | `volume-status`, `volume-import`, `volume-import-new` |
| Plaud | `plaud` | Plaud cloud API | `plaud-status`, `plaud-download`, `plaud-download-new` |

## Core Rules

Every provider must behave like a HiDock from the app's point of view:

1. A paired provider has a stable `deviceId`.
2. Status returns the same JSON shape as `HiDockSyncStatusResponse`.
3. Recordings become `HiDockSyncRecordingEntry` rows with a stable `deviceId`, `deviceName`, and recording `name`.
4. Download/import commands write or reference local audio files under the configured recordings folder.
5. Download state is stored in `usb-extractor/state.json`, keyed by provider and recording id.
6. A disconnected, unreachable, or signed-out provider must still return cached recordings from `state.json` when a cached catalog exists.
7. Provider errors belong on the device tile, not in the recordings table filter logic or as a global banner unless every provider has failed unusually.
8. Imported local files are a virtual source, not a child of any real provider. Filtering by a provider must not show imported rows.

## Status Contract

Status commands must print JSON matching this shape:

```json
{
  "connected": true,
  "outputDir": "/Users/name/HiDock/Recordings",
  "statePath": "/Users/name/HiDock/state.json",
  "configPath": "/Users/name/HiDock/config.json",
  "recordings": [],
  "error": null,
  "storage": {
    "totalFiles": 7,
    "returnedFiles": 7,
    "totalBytesReturned": 1234567,
    "truncated": false
  }
}
```

Rules:

- `connected: true` means a live query succeeded.
- `connected: false` means the provider is not currently reachable, signed in, or mounted.
- `recordings` must still contain cached rows when `connected` is false and a cached catalog exists.
- `error` should be a human-readable reason, for example `HiDock device not found`, `volume not mounted`, or `Plaud is not signed in`.
- `storage` is optional, but if present must describe the visible provider catalog, not the entire app library.
- `cached: true` may be included when rows come from local catalog cache rather than a live provider.

## Recording Row Contract

Each provider recording must match `HiDockSyncRecording`:

```json
{
  "name": "provider-stable-recording-id",
  "createDate": "2026/06/08",
  "createTime": "16:22:57",
  "length": 1234567,
  "duration": 180.5,
  "durationEstimated": false,
  "version": 0,
  "mode": "plaud",
  "signature": "provider-stable-recording-id",
  "outputPath": "/Users/name/HiDock/Recordings/Plaud/2026-06-08/file.mp3",
  "outputName": "file.mp3",
  "downloaded": true,
  "localExists": true,
  "downloadedAt": "2026-06-08T16:30:00+00:00",
  "lastError": null,
  "status": "downloaded",
  "humanLength": "1.2 MB",
  "trimmed": false,
  "removed": false
}
```

Rules:

- `name` is the stable provider id used by download, mark-downloaded, unmark, remove, and table selection.
- `createDate` must use app display format `yyyy/MM/dd`.
- `createTime` must use app display format `HH:mm:ss`.
- Filesystem folders may use a different safe format such as `yyyy-MM-dd`.
- `duration` is seconds. Convert provider milliseconds/microseconds before emitting.
- If local file duration can be measured from audio metadata, prefer that over provider metadata.
- `durationEstimated: true` is required when duration is a fallback estimate.
- `mode` should identify the source family, such as `usb`, `volume`, or `plaud`.
- `downloaded` means the app considers the recording downloaded.
- `localExists` means the referenced local file is currently present.
- `removed: true` means the user deliberately deleted the local copy; auto-download must skip it.

## State And Cache

`usb-extractor/state.json` has two separate responsibilities:

- `downloads`: per-recording local state such as downloaded, output path, removed, trimmed, and last error.
- `catalogs`: last successful provider catalog, used to rebuild rows while offline.

Use provider-scoped keys:

```json
{
  "downloads": {
    "plaud:account-id:recording-id": {
      "downloaded": true,
      "output_path": "/Users/name/HiDock/Recordings/Plaud/2026-06-08/file.mp3",
      "source": "plaud",
      "account_id": "account-id"
    }
  },
  "catalogs": {
    "plaud:account-id": {
      "recordings": [],
      "updated_at": "2026-06-08T16:30:00+00:00",
      "source": "plaud",
      "account_id": "account-id"
    }
  }
}
```

Rules:

- Save the provider catalog after every successful live status query.
- On provider error, rebuild status rows from the cached catalog before returning.
- Do not wipe cached rows on a transient failure.
- Do not use display names as state keys; display names can change.
- Include enough provider metadata in cached catalog records to rebuild dates, duration, file names, and output paths later.

## App Integration

Swift model rules:

- Add the provider to `DeviceType`.
- Add provider-specific identity fields to `HiDockPairedDevice` only when needed.
- `deviceId` must be stable and prefixed by provider, for example `hidock:45068`, `volume:ZOOM_H1`, or `plaud:<account-id>`.
- `cleanName` and `shortName` must be user-facing. Provider names should not leak account names unless that is deliberately useful.
- Device tile status comes from `syncDeviceConnected`, `syncDeviceLastError`, `syncDeviceLastOK`, and `syncDeviceStorage`.

UI rules:

- The provider must appear as a top device tile when paired.
- The tile must show provider-specific auth or reachability problems inline.
- The tile filter must filter strictly by `entry.deviceId == device.deviceId`.
- Downloading fresh provider rows should not leave a stale status filter hiding them.
- Recording row glyphs may vary by provider, but table behavior must stay provider-neutral.

Extractor invocation rules:

- App code must pass credentials or provider context through `Process.environment`, not command-line arguments, when values are secret.
- Command-line arguments may contain stable non-secret ids such as product id, volume name, or Plaud account id.
- Provider commands must be subprocess-safe: use argument arrays, not shell strings.

## Authentication Rules

Cloud providers must separate pairing from status:

- Pairing obtains credentials and stores them in the app's credential store, such as Keychain on macOS.
- Status/download subprocesses receive credentials only through environment variables.
- If credentials are missing, expired, or rejected, the provider returns `connected:false`, `error:"<Provider> is not signed in"`, and cached rows if available.
- The app surfaces that message on the provider tile.
- Re-authentication clears the provider's signed-out error and refreshes status.

Plaud example:

- Keychain stores the Plaud session.
- `plaudEnvironment(for:)` injects `PLAUD_ACCOUNT_ID`, `PLAUD_ACCESS_TOKEN`, `PLAUD_REFRESH_TOKEN`, and `PLAUD_REGION`.
- `plaud-status` writes `state["catalogs"]["plaud:<account-id>"]` after live API success.
- If Plaud is signed out, `plaud-status` returns cached rows with `connected:false` and `error:"Plaud is not signed in"`.

## Adding A New Provider

Checklist:

- Add or extend extractor commands: `<provider>-status`, `<provider>-download`, `<provider>-download-new`.
- Emit `HiDockSyncStatusResponse` and `HiDockSyncRecording` compatible JSON.
- Add provider-scoped download keys in `state["downloads"]`.
- Add provider-scoped catalog keys in `state["catalogs"]`.
- Prove offline/signed-out status returns cached rows.
- Add `DeviceType` and `HiDockPairedDevice` identity fields.
- Add pairing/forgetting behavior, including credential cleanup if needed.
- Add provider environment injection for secrets.
- Add tile icon/glyph assets or SF Symbol fallback.
- Ensure provider filtering excludes imported rows.
- Ensure auto-download and auto-transcribe use the same code paths as HiDock rows.
- Add a focused test or script covering date format, duration units, cache fallback, and download state.

## Non-Negotiable Behaviors

Future providers must not:

- Return provider-specific date formats to the app.
- Return milliseconds as `duration`.
- Use account email or display name as the stable device id.
- Clear the table just because the provider is offline.
- Put provider auth failures only in logs or global status.
- Make the recordings table understand provider-specific rules.
- Show imported local files inside a real device filter.
- Store access tokens in `state.json`.
