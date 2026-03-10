# Protocol Notes

## USB identity

- Product name: `HiDock_H1`
- Vendor ID: `4310`
- Product ID: `45068`

## Known packet shapes

### Heartbeat / echo

Outbound:

```text
12 34 00 12 00 00 01 6c 00 00 00 00
```

Inbound:

```text
12 34 00 12 00 00 01 6c 00 00 00 00
```

Interpretation:

- Framed command/response with identical request id.
- Used continuously during idle polling.

### Status / config

Outbound:

```text
12 34 00 0b 00 00 01 71 00 00 00 00
```

Inbound:

```text
12 34 00 0b 00 00 01 71 00 00 00 10 00 00 00 01 00 00 00 02 00 00 00 02 00 00 00 01
```

Interpretation:

- Another polling/control message.
- Returns 16 bytes of payload after the framing fields.

### File transfer request

Outbound:

```text
12 34 00 05 00 00 00 bf 00 00 00 1a 32 30 32 36 46 65 62 32 36 2d 31 36 30 31 31 37 2d 52 65 63 33 35 2e 68 64 61
```

ASCII payload:

```text
2026Feb26-160117-Rec35.hda
```

Interpretation:

- `00 05` starts transfer of a named device file.
- The requested source file uses the `.hda` extension on device.

### File transfer response

Observed inbound chunks:

- `endpoint 2`
- requested size `512000`
- returned size `512000`
- frames begin with `12 34 00 05`
- payload size field appears as `00 00 1f f4`
- payload bytes contain MP3 sync words such as `ff f3 88 c4`

Interpretation:

- Device streams MP3 data in framed chunks.
- Need to determine whether every chunk repeats the same framing layout and how end-of-file is signaled.

## Open questions

- What command lists device-side `.hda` filenames?
- Is there a transfer-finish or ack packet after the final chunk?
- Does the device expose metadata beyond filename and stream bytes?
