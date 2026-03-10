# HiDock USB Extractor

Standalone extraction work for pulling recordings directly from the HiDock over USB without using HiNotes for the transfer step.

## Current status

Confirmed from live captures:

- Device uses WebUSB via Chrome, not mounted filesystem access.
- USB target is `4310:45068` (`HiDock_H1`).
- `endpoint 1` is outbound commands.
- `endpoint 2` is inbound data.
- `00 12` is a polling/heartbeat command.
- `00 0b` is a status/config command.
- `00 05` is the transfer command family.
- Transfer payloads contain MP3 frame bytes, so the dock streams audio data directly over USB.

## Confirmed transfer request format

Example captured transfer request:

```text
12 34 00 05 00 00 00 bf 00 00 00 1a 32 30 32 36 46 65 62 32 36 2d 31 36 30 31 31 37 2d 52 65 63 33 35 2e 68 64 61
```

Decoded:

- Header: `12 34`
- Command: `00 05`
- Request ID: `00 00 00 bf`
- Payload length: `00 00 00 1a`
- Payload ASCII: `2026Feb26-160117-Rec35.hda`

This indicates transfer requests are filename-based against device-side `.hda` files.

## Remaining work

1. Reverse the device file-list command.
2. Confirm transfer completion framing and end-of-stream behavior.
3. Build a standalone downloader that:
   - lists device recordings
   - compares against local state
   - pulls only new recordings
   - writes `.mp3` outputs

## Prototype

The first prototype in this folder does not talk to HiNotes. It expects a known
device-side `.hda` filename and sends the captured transfer command directly.

Files:

- `extractor.py` - USB transfer prototype
- `extract_filenames.py` - parse `.hda` filenames from exported HiNotes state
- `requirements.txt` - Python USB dependency

Setup:

```bash
cd /Users/jameswhiting/_git/hidock-tools/usb-extractor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Example:

```bash
source .venv/bin/activate
python extractor.py pull 2026Feb26-160117-Rec35.hda --out out/
```

Current caveats:

- End-of-file detection is still partly inferred from captures.
- The file-list step is not yet pulled directly from the device.
- For now, `.hda` names can be sourced from HiNotes memory exports or future
  protocol work.
