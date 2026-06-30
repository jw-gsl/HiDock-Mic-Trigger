#!/usr/bin/env python3
"""Prototype HiDock USB extractor.

Confirmed from captures:
- Vendor/product: 4310:45068
- Out endpoint: 1
- In endpoint: 2
- Command 0x0005 transfers a named `.hda` file
- Returned payload contains MP3 frames

Still inferred:
- End-of-stream framing rules
- Whether a trailing ack is required after the last chunk
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make `shared/` importable regardless of where the extractor is invoked
# from. The desktop app subprocesses this script with various CWDs,
# and the project's shared/ lives one level up from usb-extractor/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import usb.core
import usb.util

import plaud_client


def _attach_refreshed_plaud_tokens(payload: dict, account_id: str) -> None:
    """If the Plaud user token was refreshed during this command, attach the
    (possibly rotated) tokens so the app can persist them. The Python side does
    not store secrets, so without this the next run reuses the stale env token."""
    refreshed = plaud_client.pop_refreshed_tokens(account_id)
    if refreshed:
        payload["refreshedTokens"] = refreshed


VENDOR_ID = 4310
PRODUCT_ID = 45068
OUT_ENDPOINT = 1
IN_ENDPOINT = 0x82
USB_READ_SIZE = 512000
HEADER = b"\x12\x34"
CMD_TRANSFER = 0x0005
CMD_QUERY_TIME = 0x0002
CMD_QUERY_FILE_LIST = 0x0004
CMD_QUERY_FILE_COUNT = 0x0006
CMD_GET_FILE_BLOCK = 0x000D
CMD_TRANSFER_FILE_PARTIAL = 0x0015
MAX_EMPTY_READS = 4
MAX_PAYLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_STATE_PATH = BASE_DIR / "state.json"
DEFAULT_OUTPUT_DIR = BASE_DIR / "out"


class HiDockProtocolError(RuntimeError):
    pass


def _log_warn(message: str) -> None:
    """Print a warning to stderr that the desktop app's log capture picks
    up. Used wherever we previously had a silent `except Exception: pass`
    that could mask real-world breakage (e.g. a missing mutagen dep that
    silently let bad duration estimates flow through to the UI for
    months). Anything noisy enough to log but not fatal goes here."""
    print(f"[extractor] WARN: {message}", file=sys.stderr, flush=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def save_json_file(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(str(tmp), str(path))


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    config = load_json_file(config_path, {})
    output_dir = config.get("output_dir")
    if not output_dir:
        output_dir = str(DEFAULT_OUTPUT_DIR)
    config["output_dir"] = output_dir
    return config


def save_config(config: dict, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    save_json_file(config_path, config)


def load_state(state_path: Path = DEFAULT_STATE_PATH) -> dict:
    state = load_json_file(state_path, {})
    downloads = state.get("downloads")
    if not isinstance(downloads, dict):
        downloads = {}
    state["downloads"] = downloads
    return state


def save_state(state: dict, state_path: Path = DEFAULT_STATE_PATH) -> None:
    save_json_file(state_path, state)


def resolved_output_dir(config: dict) -> Path:
    return Path(config["output_dir"]).expanduser().resolve()


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(num_bytes)} B"


def build_transfer_request(request_id: int, filename: str) -> bytes:
    payload = filename.encode("ascii")
    return (
        HEADER
        + struct.pack(">H", CMD_TRANSFER)
        + struct.pack(">I", request_id)
        + struct.pack(">I", len(payload))
        + payload
    )


def build_simple_request(command: int, request_id: int, payload: bytes = b"") -> bytes:
    return (
        HEADER
        + struct.pack(">H", command)
        + struct.pack(">I", request_id)
        + struct.pack(">I", len(payload))
        + payload
    )


def build_name_only_payload(filename: str) -> bytes:
    return filename.encode("ascii")


def build_length_name_payload(filename: str, length: int) -> bytes:
    return struct.pack(">I", length) + filename.encode("ascii")


def build_offset_length_name_payload(filename: str, offset: int, length: int) -> bytes:
    return struct.pack(">I", offset) + struct.pack(">I", length) + filename.encode("ascii")


SAFE_FILENAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


def validate_filename(filename: str) -> str:
    """Validate a device-provided filename, rejecting path traversal attempts."""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HiDockProtocolError(f"unsafe filename from device: {filename!r}")
    if not SAFE_FILENAME_RE.match(filename):
        raise HiDockProtocolError(f"unsafe filename from device: {filename!r}")
    return filename


def output_name_for(filename: str) -> str:
    base = validate_filename(filename)
    if base.lower().endswith(".hda"):
        return base[:-4] + ".mp3"
    return base + ".mp3"


def output_path_for(filename: str, output_dir: Path) -> Path:
    return output_dir / output_name_for(filename)


def md5_hex(text: str) -> str:
    import hashlib

    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _resolve_owner_pid(owner: str) -> str:
    """If owner string contains a PID, resolve it to a human-friendly process name."""
    pid_in_owner = re.search(r"pid (\d+)", owner)
    if pid_in_owner:
        try:
            ps = subprocess.run(
                ["ps", "-p", pid_in_owner.group(1), "-o", "comm="],
                capture_output=True, text=True, timeout=3,
            )
            proc_name = ps.stdout.strip()
            if proc_name:
                # Show just the app name, not the full path
                short_name = proc_name.rsplit("/", 1)[-1]
                return f"{short_name} (pid {pid_in_owner.group(1)})"
        except Exception:
            pass
    return owner


def detect_usb_owner(product_id: int | None = None) -> str | None:
    """On macOS, check ioreg for which process holds the USB device or interface exclusively."""
    if platform.system() != "Darwin":
        return None
    pid = product_id if product_id is not None else PRODUCT_ID
    try:
        # Check interface level first (most specific)
        result = subprocess.run(
            ["ioreg", "-r", "-c", "IOUSBHostInterface", "-l", "-w0"],
            capture_output=True, text=True, timeout=5,
        )
        blocks = re.split(r"\+-o IOUSBHostInterface", result.stdout)
        for block in blocks:
            pid_match = re.search(r'"idProduct"\s*=\s*(\d+)', block)
            vid_match = re.search(r'"idVendor"\s*=\s*(\d+)', block)
            iclass_match = re.search(r'"bInterfaceClass"\s*=\s*(\d+)', block)
            owner_match = re.search(r'"UsbExclusiveOwner"\s*=\s*"(.+?)"', block)
            if (pid_match and int(pid_match.group(1)) == pid and
                    vid_match and int(vid_match.group(1)) == VENDOR_ID and
                    iclass_match and int(iclass_match.group(1)) == 255):
                if owner_match:
                    return _resolve_owner_pid(owner_match.group(1))

        # Check device level too — WebUSB/Chrome may claim at this level
        result = subprocess.run(
            ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w0"],
            capture_output=True, text=True, timeout=5,
        )
        blocks = re.split(r"\+-o ", result.stdout)
        for block in blocks:
            pid_match = re.search(r'"idProduct"\s*=\s*(\d+)', block)
            vid_match = re.search(r'"idVendor"\s*=\s*(\d+)', block)
            owner_match = re.search(r'"UsbExclusiveOwner"\s*=\s*"(.+?)"', block)
            if (pid_match and int(pid_match.group(1)) == pid and
                    vid_match and int(vid_match.group(1)) == VENDOR_ID):
                if owner_match:
                    return _resolve_owner_pid(owner_match.group(1))

        return None
    except Exception:
        return None


def find_device(product_id: int | None = None):
    pid = product_id if product_id is not None else PRODUCT_ID
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=pid)
    if dev is None:
        raise FileNotFoundError(f"HiDock device {VENDOR_ID}:{pid} not found")
    return dev


def prepare_device(dev):
    """Claim the HiDock's vendor interface.

    Historically this called ``dev.reset()`` unconditionally before every
    command. That sends a USB bus reset to the device, which:
      - yanks any other process (e.g. ffmpeg holding the audio-class
        interface) out of the middle of a read/write, and
      - forces the HiDock firmware to re-initialise its USB stack.
    On a HiDock H1 the latter occasionally wedges the firmware
    completely — subsequent opens hang for 30+ seconds until the user
    physically unplugs the device. The reset was there as a "kitchen
    sink" recovery for devices left in a weird state by a previous
    session, but paying that cost on every status query is too
    expensive for a device shared with ffmpeg.

    New flow: try the normal claim path first. Only fall back to a full
    reset if ``claim_interface`` raises ``USBError`` (i.e. the device
    really is in a state that needs recovery).
    """
    def _claim_once():
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass

        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        try:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                try:
                    dev.detach_kernel_driver(intf.bInterfaceNumber)
                except usb.core.USBError:
                    # On macOS this is often unsupported or blocked even when we can
                    # still continue to talk to the device directly.
                    pass
        except (NotImplementedError, usb.core.USBError):
            pass
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
        return intf.bInterfaceNumber

    try:
        return _claim_once()
    except usb.core.USBError:
        # Recovery path: the device is probably in a stuck state. Reset
        # the bus and retry once. This used to run on every invocation;
        # now it only runs when the fast path genuinely fails.
        try:
            dev.reset()
        except usb.core.USBError:
            pass
        return _claim_once()


def release_device(dev, interface_number: int) -> None:
    try:
        usb.util.release_interface(dev, interface_number)
    except usb.core.USBError:
        pass
    usb.util.dispose_resources(dev)


# Global reference for signal handler cleanup
_active_dev = None
_active_intf = None


def _sigterm_handler(signum, frame):
    if _active_dev is not None and _active_intf is not None:
        release_device(_active_dev, _active_intf)
    sys.exit(143)


signal.signal(signal.SIGTERM, _sigterm_handler)


def parse_frame(buf: bytes) -> tuple[int, int, bytes]:
    if len(buf) < 12:
        raise HiDockProtocolError(f"short frame: {len(buf)} bytes")
    if buf[:2] != HEADER:
        raise HiDockProtocolError(f"unexpected frame header: {buf[:8].hex(' ')}")
    cmd = struct.unpack(">H", buf[2:4])[0]
    req_id = struct.unpack(">I", buf[4:8])[0]
    payload_len = struct.unpack(">I", buf[8:12])[0]
    if payload_len > MAX_PAYLOAD_SIZE:
        raise HiDockProtocolError(f"payload too large: {payload_len} bytes (max {MAX_PAYLOAD_SIZE})")
    payload = buf[12 : 12 + payload_len]
    return cmd, req_id, payload


def extract_frames(buffer: bytes) -> tuple[list[tuple[int, int, bytes]], bytes]:
    frames: list[tuple[int, int, bytes]] = []
    cursor = 0

    while True:
        start = buffer.find(HEADER, cursor)
        if start == -1:
            # Keep a small tail in case the next read completes the header.
            return frames, buffer[-1:] if buffer else b""
        if len(buffer) - start < 12:
            return frames, buffer[start:]

        payload_len = struct.unpack(">I", buffer[start + 8 : start + 12])[0]
        frame_len = 12 + payload_len
        if len(buffer) - start < frame_len:
            return frames, buffer[start:]

        frame = buffer[start : start + frame_len]
        frames.append(parse_frame(frame))
        cursor = start + frame_len
        if cursor >= len(buffer):
            return frames, b""


def looks_like_mp3(payload: bytes) -> bool:
    return b"\xff\xf3" in payload or b"\xff\xfb" in payload


def bcdish_filename_to_datetime(filename: str):
    import datetime as dt
    import re

    m = re.match(r"^(?:\d{2})?(\d{2})([A-Z][a-z]{2})(\d{2})-(\d{2})(\d{2})(\d{2})-.*\.(?:hda|wav)$", filename)
    if not m:
        return None
    year, mon, day, hh, mm, ss = m.groups()
    try:
        return dt.datetime.strptime(f"20{year} {mon} {day} {hh}:{mm}:{ss}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None


def parse_query_file_list_payload(raw_payloads: list[bytes], expected_count: int | None = None) -> list[dict]:
    data: list[int] = []
    for part in raw_payloads:
        data.extend(part)

    total = -1
    cursor = 0
    if len(data) >= 6 and data[0] == 0xFF and data[1] == 0xFF:
        total = (data[2] << 24) | (data[3] << 16) | (data[4] << 8) | data[5]
        cursor = 6

    items: list[dict] = []
    while cursor < len(data):
        if cursor + 4 >= len(data):
            break
        version = data[cursor]
        cursor += 1
        length = (data[cursor] << 16) | (data[cursor + 1] << 8) | data[cursor + 2]
        cursor += 3

        if cursor + length > len(data):
            break
        name_bytes = [b for b in data[cursor : cursor + length] if b > 0]
        cursor += length
        name = bytes(name_bytes).decode("ascii", errors="ignore")

        if cursor + 4 + 6 + 16 > len(data):
            break
        file_len = (data[cursor] << 24) | (data[cursor + 1] << 16) | (data[cursor + 2] << 8) | data[cursor + 3]
        cursor += 4
        cursor += 6
        signature = "".join(f"{data[cursor + i]:02x}" for i in range(16))
        cursor += 16

        ts = bcdish_filename_to_datetime(name)
        if ts is None:
            continue

        # Downloaded recordings are 16 kHz mono MP3 at ~64 kbps, so duration is
        # well approximated by bytes / 8000. The earlier version-based formulas
        # produced wildly incorrect values for current devices.
        duration = max(file_len / 8000.0, 0.0)

        mode = "room"
        upper_name = name.upper()
        if upper_name.startswith("WHSP") or upper_name.startswith("WIP"):
            mode = "whisper"
        elif upper_name.startswith("CALL"):
            mode = "call"

        items.append(
            {
                "name": name,
                "createDate": ts.strftime("%Y/%m/%d"),
                "createTime": ts.strftime("%H:%M:%S"),
                "length": file_len,
                "duration": duration,
                "version": version,
                "mode": mode,
                "signature": signature,
            }
        )

    seen: dict[str, int] = {}
    for item in items:
        seen[item["signature"]] = seen.get(item["signature"], 0) + 1
    for item in items:
        if seen[item["signature"]] > 1:
            item["signature"] = md5_hex(f'{item["name"]}{item["length"]}')

    if expected_count is not None and len(items) >= expected_count:
        return items
    if total > -1 and len(items) >= total:
        return items
    return items


def send_and_collect(
    dev,
    command: int,
    request_id: int,
    payload: bytes = b"",
    timeout_ms: int = 5000,
    max_reads: int = 32,
) -> list[tuple[int, int, bytes]]:
    request = build_simple_request(command, request_id, payload)
    dev.write(OUT_ENDPOINT, request, timeout=timeout_ms)

    pending = b""
    frames: list[tuple[int, int, bytes]] = []
    empty_reads = 0

    for _ in range(max_reads):
        try:
            data = bytes(dev.read(IN_ENDPOINT, USB_READ_SIZE, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            empty_reads += 1
            if empty_reads >= 4:
                break
            continue
        empty_reads = 0
        pending += data
        parsed, pending = extract_frames(pending)
        for frame in parsed:
            cmd, req, body = frame
            if req == request_id:
                frames.append(frame)
    return frames


def drain_input(dev, timeout_ms: int = 100) -> None:
    while True:
        try:
            dev.read(IN_ENDPOINT, USB_READ_SIZE, timeout=timeout_ms)
        except usb.core.USBTimeoutError:
            break


def read_raw_response_payload(
    dev,
    command: int,
    request_id: int,
    timeout_ms: int = 5000,
    idle_timeout_ms: int = 250,
) -> bytes:
    request = build_simple_request(command, request_id)
    drain_input(dev)
    dev.write(OUT_ENDPOINT, request, timeout=timeout_ms)

    pending = b""
    started = False
    payload_len = None
    total_needed = None
    deadline = time.time() + (timeout_ms / 1000.0)

    while time.time() < deadline:
        try:
            chunk = bytes(dev.read(IN_ENDPOINT, USB_READ_SIZE, timeout=idle_timeout_ms))
        except usb.core.USBTimeoutError:
            if started:
                break
            continue

        pending += chunk

        if not started:
            header = HEADER + struct.pack(">H", command) + struct.pack(">I", request_id)
            start = pending.find(header)
            if start == -1 or len(pending) - start < 12:
                continue
            payload_len = struct.unpack(">I", pending[start + 8 : start + 12])[0]
            # Bounds-check the device-reported payload length the same way
            # parse_frame() does. Without this, a corrupted or malicious
            # length field can cause the loop below to wait indefinitely
            # for an impossible byte count, then fall through to the
            # best-effort return below — silently truncating the payload.
            if payload_len > MAX_PAYLOAD_SIZE:
                raise HiDockProtocolError(
                    f"payload too large: {payload_len} bytes (max {MAX_PAYLOAD_SIZE})"
                )
            total_needed = start + 12 + payload_len
            pending = pending[start:]
            started = True

        if total_needed is not None and len(pending) >= total_needed:
            return pending[12:total_needed]

    if started and payload_len is not None and len(pending) >= 12:
        available = min(len(pending) - 12, payload_len)
        return pending[12 : 12 + available]
    raise HiDockProtocolError(f"no response payload received for command 0x{command:04x}")


def send_and_stream(
    dev,
    command: int,
    request_id: int,
    payload: bytes,
    expected_length: int,
    timeout_ms: int = 5000,
    max_timeouts: int = 4,
) -> bytes:
    request = build_simple_request(command, request_id, payload)
    dev.write(OUT_ENDPOINT, request, timeout=timeout_ms)

    pending = b""
    chunks: list[bytes] = []
    received = 0
    timeouts = 0

    while True:
        try:
            data = bytes(dev.read(IN_ENDPOINT, USB_READ_SIZE, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            timeouts += 1
            if received and timeouts >= max_timeouts:
                break
            if timeouts >= max_timeouts:
                raise TimeoutError("timed out waiting for HiDock stream data")
            continue

        timeouts = 0
        pending += data
        frames, pending = extract_frames(pending)
        for cmd, req, body in frames:
            if req != request_id:
                continue
            if cmd != command:
                continue
            if not body:
                if received:
                    return b"".join(chunks)
                continue
            chunks.append(body)
            received += len(body)
            if received >= expected_length:
                return b"".join(chunks)[:expected_length]

    return b"".join(chunks)


def query_file_count(dev, request_id: int = 1, timeout_ms: int = 5000) -> int:
    frames = send_and_collect(dev, CMD_QUERY_FILE_COUNT, request_id, timeout_ms=timeout_ms)
    for cmd, _, payload in frames:
        if cmd == CMD_QUERY_FILE_COUNT and len(payload) >= 4:
            return struct.unpack(">I", payload[:4])[0]
    raise HiDockProtocolError("no file count response received")


def query_file_list(dev, request_id: int = 2, timeout_ms: int = 5000) -> list[dict]:
    """Fetch the device's full recording catalog.

    HiDock firmware behaviour discovered 2026-04-23 via raw USB capture:

    - **P1** packs the entire catalog into one USB transfer with multiple
      frames. One request, one read.

    - **H1** auto-paginates across multiple USB transfers. We send ONE
      ``CMD_QUERY_FILE_LIST`` request; the device returns the first chunk
      (~8KB, up to ~143 records) almost immediately with the original
      request_id, then a few seconds later streams a second frame with
      the remaining records — but the firmware **increments the
      request_id** on that continuation frame (from N to N+1). For 282
      records we observed frame 0 (req=N, 8180b) then frame 1
      (req=N+1, 7900b). Large catalogs may have three or more frames.

    The previous implementation missed this because ``send_and_collect``
    filtered on matching request_id, silently discarding every
    continuation frame. It also sent its own offset-based continuation
    requests which the firmware ignores (they get the same first batch
    back, or nothing), leaving us stuck at ~143 records.

    New approach: send one request, keep reading every frame whose
    command ID is ``CMD_QUERY_FILE_LIST`` until the device goes quiet
    for long enough that we're confident no more frames are coming.
    Sort by request_id to reassemble ordering, then parse.
    """
    # IMPORTANT: don't issue QUERY_FILE_COUNT or QUERY_TIME here.
    # Empirically (2026-04-23) any other command between our two
    # back-to-back QUERY_FILE_LIST writes resets the firmware's
    # "continuation pending" state and we end up with only the first
    # frame. Pull expected_count out of the header payload instead
    # (it's encoded in the first 4 bytes after the 0xFFFF marker).
    expected_count: int | None = None

    # Drain any stale data buffered from a previous aborted transaction
    # so our first read doesn't pick up orphaned continuation bytes
    # without their header. Safe here (before we've sent anything).
    drain_input(dev, timeout_ms=100)

    # H1 catalog protocol (discovered 2026-04-23 via USB capture):
    #   - A single QUERY_FILE_LIST returns one 8180-byte frame with the
    #     header (0xFFFF + total count) + the first-buffer-worth of
    #     records (~143 for modern filename length).
    #   - The firmware ALSO QUEUES the remainder internally. That
    #     queued continuation is only released when the device sees the
    #     next OUT request on the endpoint — and crucially, it is
    #     flushed as the FIRST frame of that next transaction. The
    #     second transaction's own response then follows.
    #   - So to get the full catalog in one shot: send request #1,
    #     drain what comes back (header + batch1); send request #2,
    #     which returns <queued continuation batch2> + <header + batch1
    #     again>. Merge + de-dupe.
    #   - Frame request_ids are firmware-assigned and effectively
    #     random; we collect by cmd, not by rid.
    def _read_all_frames(deadline: float, idle_stop_s: float = 3.0) -> list[bytes]:
        pending_local = b""
        out: list[bytes] = []
        last_data = time.time()
        while time.time() < deadline:
            try:
                chunk = bytes(dev.read(IN_ENDPOINT, USB_READ_SIZE, timeout=500))
            except usb.core.USBTimeoutError:
                if out and (time.time() - last_data) > idle_stop_s:
                    break
                continue
            if not chunk:
                continue
            last_data = time.time()
            pending_local += chunk
            parsed_frames, pending_local = extract_frames(pending_local)
            for cmd, _rid, body in parsed_frames:
                if cmd == CMD_QUERY_FILE_LIST and body:
                    out.append(body)
        return out

    # Request #1 — primes the queue. Read frame 1.
    total_budget_s = max(min(timeout_ms / 1000.0, 30.0), 12.0)
    half_budget_s = total_budget_s / 2.0

    req_a = build_simple_request(CMD_QUERY_FILE_LIST, request_id)
    dev.write(OUT_ENDPOINT, req_a, timeout=timeout_ms)
    first_batch = _read_all_frames(time.time() + half_budget_s, idle_stop_s=2.0)

    # Request #2 — releases the queued continuation, then sends its own
    # response. Read everything.
    req_b = build_simple_request(CMD_QUERY_FILE_LIST, request_id + 1)
    dev.write(OUT_ENDPOINT, req_b, timeout=timeout_ms)
    second_batch = _read_all_frames(time.time() + half_budget_s, idle_stop_s=3.0)

    # After request #2 the firmware still has a continuation queued —
    # if we just return, the NEXT command issued against the device
    # (CMD_TRANSFER for a download, typically) will have its response
    # preceded by the queued continuation and time out.
    #
    # Empirically, CMD_QUERY_TIME *clears* the firmware's pending-
    # continuation state (discovered when priming with it before the
    # two list writes broke pagination — it was eating the queue we
    # needed). Here we use that property intentionally: fire a
    # QUERY_TIME, drain its reply, and the firmware is back to a
    # clean state ready for CMD_TRANSFER / CMD_QUERY_FILE_COUNT /
    # whatever comes next.
    try:
        send_and_collect(dev, CMD_QUERY_TIME, request_id + 2, timeout_ms=1000, max_reads=4)
    except Exception:
        pass
    drain_input(dev, timeout_ms=150)

    collected = first_batch + second_batch

    if not collected:
        raise HiDockProtocolError("no file list response received")

    # De-dupe exact-duplicate frames (small catalog = both requests
    # return the same header frame).
    seen: set[bytes] = set()
    unique: list[bytes] = []
    for body in collected:
        if body not in seen:
            seen.add(body)
            unique.append(body)

    # Reassembly: header frame (starts with 0xFFFF + 4-byte total)
    # must go first so the parser finds the record stream. The
    # continuation frames go after, keeping their arrival order.
    header_idx = next(
        (i for i, body in enumerate(unique) if len(body) >= 2 and body[0] == 0xFF and body[1] == 0xFF),
        None,
    )
    if header_idx is None:
        raise HiDockProtocolError(
            f"no header frame in file-list response "
            f"({len(unique)} frame(s), totalling {sum(len(b) for b in unique)} bytes)"
        )
    payloads = [unique[header_idx]] + [b for i, b in enumerate(unique) if i != header_idx]

    # Read the declared total count from the header frame (0xFFFF +
    # 4-byte big-endian count). Used below for the truncation warning
    # and stashed on the function object so cmd_status can read it.
    header = payloads[0]
    if len(header) >= 6:
        expected_count = struct.unpack(">I", header[2:6])[0]
    query_file_list._last_declared_total = expected_count  # type: ignore[attr-defined]

    result = parse_query_file_list_payload(payloads, expected_count=expected_count)

    # Warn only if we truly couldn't get all records. With the new
    # multi-frame collector this should be rare — mainly for catalogs
    # that exceed the firmware's own buffer regardless of pagination.
    if expected_count is not None and len(result) < expected_count:
        missing = expected_count - len(result)
        import sys
        print(
            f"WARNING: Device has {expected_count} recordings but firmware only returned {len(result)}. "
            f"{missing} newest recordings are hidden. Delete old recordings from the device to free space.",
            file=sys.stderr,
        )

    return result


def get_file_metadata(dev, request_id: int = 3, timeout_ms: int = 5000) -> dict | None:
    frames = send_and_collect(dev, 0x0012, request_id, timeout_ms=timeout_ms)
    for _cmd, _req, payload in frames:
        if payload:
            return {"raw": payload.hex(" ")}
    return None


def get_file_block(dev, filename: str, length: int, request_id: int = 4, timeout_ms: int = 5000) -> bytes:
    payload = build_length_name_payload(filename, length)
    return send_and_stream(
        dev,
        CMD_GET_FILE_BLOCK,
        request_id,
        payload,
        expected_length=length,
        timeout_ms=timeout_ms,
    )


def transfer_file_stream(
    dev,
    filename: str,
    total_length: int,
    request_id: int = 6,
    timeout_ms: int = 5000,
) -> bytes:
    out_path = Path("/tmp") / output_name_for(filename)
    written = transfer_file_stream_to_path(
        dev,
        filename,
        total_length=total_length,
        out_path=out_path,
        request_id=request_id,
        timeout_ms=timeout_ms,
    )
    return out_path.read_bytes()[:written]


def transfer_file_stream_to_path(
    dev,
    filename: str,
    total_length: int,
    out_path: Path,
    request_id: int = 6,
    timeout_ms: int = 5000,
    progress=None,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + '.downloading')

    # Clear stuck firmware queue state before issuing CMD_TRANSFER.
    # Empirically (2026-05-07): a HiDock H1 with leftover queued state
    # from a previous transaction (a file-list continuation, an aborted
    # transfer, or an earlier transfer that ended uncleanly) will
    # silently swallow the next CMD_TRANSFER request — the read loop
    # times out with zero bytes after 40s. Reproduced on Rec70 (47MB,
    # not corrupt — HiDock's own app downloaded it fine) which had
    # failed three times with three different transfer commands
    # (CMD_TRANSFER, CMD_TRANSFER_FILE_PARTIAL, CMD_GET_FILE_BLOCK)
    # until this warm-up was added; with it, the same file pulled
    # cleanly in 23s on the first try.
    #
    # CMD_QUERY_TIME has a side-effect of resetting the firmware's
    # "continuation pending" state — a property already exploited at
    # the end of query_file_list to avoid breaking the next command.
    # Drain before AND after to mop up any orphaned bytes.
    drain_input(dev, timeout_ms=200)
    try:
        send_and_collect(dev, CMD_QUERY_TIME, request_id - 1, timeout_ms=1000, max_reads=4)
    except Exception:
        # Warm-up is best-effort. If it fails we still try the transfer;
        # most files don't need the warm-up at all.
        pass
    drain_input(dev, timeout_ms=200)

    payload = build_name_only_payload(filename)
    request = build_simple_request(CMD_TRANSFER, request_id, payload)
    dev.write(OUT_ENDPOINT, request, timeout=timeout_ms)

    pending = b""
    received = 0
    timeouts = 0
    started = False
    last_seq = request_id

    try:
        with tmp_path.open("wb") as fh:
            while received < total_length:
                try:
                    data = bytes(dev.read(IN_ENDPOINT, USB_READ_SIZE, timeout=timeout_ms))
                except usb.core.USBTimeoutError:
                    timeouts += 1
                    if received and timeouts >= 8:
                        break
                    if timeouts >= 8:
                        raise TimeoutError("timed out waiting for HiDock transfer stream")
                    continue

                timeouts = 0
                pending += data
                frames, pending = extract_frames(pending)
                for cmd, req, body in frames:
                    if cmd != CMD_TRANSFER:
                        continue
                    if not started:
                        if req != request_id:
                            continue
                        started = True
                    else:
                        if req < last_seq:
                            continue
                    last_seq = req
                    if not body:
                        if received:
                            os.replace(str(tmp_path), str(out_path))
                            return received
                        continue
                    if received + len(body) > total_length:
                        body = body[: total_length - received]
                    fh.write(body)
                    received += len(body)
                    if progress is not None:
                        progress(received, total_length)
                    if received >= total_length:
                        os.replace(str(tmp_path), str(out_path))
                        return received
        os.replace(str(tmp_path), str(out_path))
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return received


def read_file_partial(
    dev,
    filename: str,
    offset: int,
    length: int,
    request_id: int = 5,
    timeout_ms: int = 5000,
) -> bytes:
    payload = build_offset_length_name_payload(filename, offset, length)
    return send_and_stream(
        dev,
        CMD_TRANSFER_FILE_PARTIAL,
        request_id,
        payload,
        expected_length=length,
        timeout_ms=timeout_ms,
    )


def pull_file_by_partials(
    dev,
    filename: str,
    total_length: int,
    chunk_size: int = 8180,
    request_id_start: int = 5,
    timeout_ms: int = 5000,
) -> bytes:
    parts: list[bytes] = []
    offset = 0
    request_id = request_id_start

    while offset < total_length:
        want = min(chunk_size, total_length - offset)
        chunk = read_file_partial(
            dev,
            filename,
            offset=offset,
            length=want,
            request_id=request_id,
            timeout_ms=timeout_ms,
        )
        if not chunk:
            raise HiDockProtocolError(f"empty chunk at offset {offset} for {filename}")
        parts.append(chunk)
        offset += len(chunk)
        request_id += 1

    data = b"".join(parts)
    return data[:total_length]


def pull_file_by_partials_to_path(
    dev,
    filename: str,
    total_length: int,
    out_path: Path,
    chunk_size: int = 8180,
    request_id_start: int = 5,
    timeout_ms: int = 5000,
    progress_every: int = 20,
    progress=None,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    request_id = request_id_start
    chunk_index = 0

    with out_path.open("wb") as fh:
        while offset < total_length:
            want = min(chunk_size, total_length - offset)
            chunk = read_file_partial(
                dev,
                filename,
                offset=offset,
                length=want,
                request_id=request_id,
                timeout_ms=timeout_ms,
            )
            if not chunk:
                raise HiDockProtocolError(f"empty chunk at offset {offset} for {filename}")
            fh.write(chunk)
            offset += len(chunk)
            request_id += 1
            chunk_index += 1
            if progress is not None:
                progress(offset, total_length)
            elif chunk_index == 1 or chunk_index % progress_every == 0 or offset >= total_length:
                print(f"{offset}/{total_length}")

    return offset


def _enrich_usb_error(error_str: str, product_id: int | None = None) -> str:
    """If a USB access error occurs, try to identify which process holds the device."""
    owner = detect_usb_owner(product_id)
    if owner:
        return f"{error_str} — device held by {owner}"
    # On macOS, "Access denied" with no ioreg owner often means WebUSB (Chrome/Edge)
    if "Access denied" in error_str or "Errno 13" in error_str:
        return f"{error_str} — another app (possibly a browser with WebUSB) may have the device open"
    return error_str


def probe_device(timeout_ms: int = 2000) -> dict:
    try:
        dev = find_device()
    except FileNotFoundError:
        owner = detect_usb_owner()
        error = "HiDock device not found"
        if owner:
            error += f" — device held by {owner}"
        return {"connected": False, "available": False, "error": error}

    try:
        interface_number = prepare_device(dev)
    except usb.core.USBError as exc:
        return {"connected": False, "available": True, "error": _enrich_usb_error(str(exc))}

    try:
        items = query_file_list(dev, request_id=2, timeout_ms=timeout_ms)
        return {"connected": True, "available": True, "file_count": len(items)}
    except Exception as exc:
        return {"connected": False, "available": True, "error": str(exc)}
    finally:
        release_device(dev, interface_number)


def build_recording_status_items(recordings: list[dict], state: dict, output_dir: Path, product_id: int | None = None) -> list[dict]:
    downloads = state.get("downloads", {})
    items: list[dict] = []
    seen_names: set[str] = set()
    for recording in recordings:
        name = recording["name"]
        seen_names.add(name)
        stored = downloads.get(name, {})
        stored_path = Path(stored["output_path"]) if "output_path" in stored else None
        expected_path = output_path_for(name, output_dir)
        # Prefer the stored path if it exists, otherwise check the current output dir
        if stored_path and stored_path.exists():
            output_path = stored_path
        elif expected_path.exists():
            output_path = expected_path
        else:
            output_path = stored_path or expected_path
        local_exists = output_path.exists()
        downloaded = bool(stored.get("downloaded"))
        status = "downloaded" if downloaded else "on_device"
        if stored.get("last_error") and not downloaded:
            status = "failed"
        duration = recording.get("duration", 0.0)
        # When the file exists locally, prefer filesystem truth for
        # length and duration over the device's catalog numbers.
        # Rationale: the user may have trimmed the local file in-place
        # (Mac app's Trim action), producing a shorter file than the
        # device still reports. Without this override the UI flips back
        # to the device-reported (pre-trim) size and duration on every
        # refresh, confusing the user. Duration from mutagen is
        # frame-accurate; size is the filesystem byte count.
        length_for_display = recording.get("length", 0)
        # `duration_estimated` flips false the moment we successfully
        # read the duration from the local MP3 via mutagen. The flag
        # rides through to the desktop UI so the column can show "~"
        # for any value that is still a `bytes / 8000` guess — useful
        # when the file is downloaded but the metadata read failed
        # (e.g. mutagen missing from the venv, which is the bug that
        # made every P1 recording show ~50% over its real length).
        duration_estimated = True
        if local_exists:
            try:
                local_size = output_path.stat().st_size
                if local_size > 0:
                    length_for_display = local_size
            except OSError as exc:
                _log_warn(f"length stat failed for {output_path}: {exc}")
            try:
                from mutagen.mp3 import MP3
                audio = MP3(str(output_path))
                duration = audio.info.length
                duration_estimated = False
            except ImportError as exc:
                # Hard dependency missing — don't swallow it. Previously
                # this `except Exception: pass` kept the bad estimate
                # silently. Log loudly so the next time a venv is missing
                # mutagen it's obvious in the desktop log.
                _log_warn(
                    f"mutagen unavailable; duration for {output_path.name} "
                    f"is the size/8000 estimate, not the real value ({exc})"
                )
            except Exception as exc:
                _log_warn(f"mutagen read failed for {output_path}: {exc}")
        # Derive the "trimmed" flag two ways:
        #   1. Explicit: state.json has `trimmed: true` from a Mac-app
        #      Trim that ran after the mark-trimmed feature shipped.
        #   2. Inferred: the local file is meaningfully smaller than the
        #      device-reported length. Catches files trimmed BEFORE
        #      mark-trimmed existed — without this, every historical
        #      trim shows no scissors icon. 5% slack absorbs ID3 / minor
        #      tag-rewrite differences from non-trim post-processing.
        device_length = recording.get("length", 0) or 0
        size_inferred_trim = (
            local_exists
            and device_length > 0
            and length_for_display > 0
            and length_for_display < device_length * 0.95
        )
        trimmed_flag = bool(stored.get("trimmed")) or size_inferred_trim
        items.append(
            {
                **recording,
                "length": length_for_display,
                "duration": duration,
                "durationEstimated": duration_estimated,
                "outputPath": str(output_path),
                "outputName": output_name_for(name),
                "downloaded": downloaded,
                "localExists": local_exists,
                "downloadedAt": stored.get("downloaded_at"),
                "lastError": stored.get("last_error"),
                "status": status,
                "humanLength": human_size(length_for_display),
                "trimmed": trimmed_flag,
                "removed": bool(stored.get("removed")),
            }
        )

    for name, stored in downloads.items():
        if name in seen_names:
            continue
        # Skip orphan records that don't belong to this device
        stored_pid = stored.get("product_id")
        if product_id is not None and stored_pid != product_id:
            continue
        stored_path = Path(stored["output_path"]) if "output_path" in stored else None
        expected_path = output_path_for(name, output_dir)
        if stored_path and stored_path.exists():
            output_path = stored_path
        elif expected_path.exists():
            output_path = expected_path
        else:
            output_path = stored_path or expected_path
        local_exists = output_path.exists()
        length = int(stored.get("length", 0))
        duration = 0.0
        duration_estimated = True
        if local_exists:
            try:
                from mutagen.mp3 import MP3
                audio = MP3(str(output_path))
                duration = audio.info.length
                duration_estimated = False
            except ImportError as exc:
                _log_warn(
                    f"mutagen unavailable; orphan duration for {output_path.name} "
                    f"falling back to size/8000 estimate ({exc})"
                )
                duration = max(length / 8000.0, 0.0)
            except Exception as exc:
                _log_warn(f"mutagen read failed for orphan {output_path}: {exc}")
                duration = max(length / 8000.0, 0.0)
        ts = bcdish_filename_to_datetime(name)
        items.append(
            {
                "name": name,
                "createDate": ts.strftime("%Y/%m/%d") if ts else "",
                "createTime": ts.strftime("%H:%M:%S") if ts else "",
                "length": length,
                "duration": duration,
                "durationEstimated": duration_estimated,
                "version": 0,
                "mode": "unknown",
                "signature": stored.get("signature", md5_hex(name)),
                "outputPath": str(output_path),
                "outputName": output_name_for(name),
                "downloaded": bool(stored.get("downloaded")),
                "localExists": local_exists,
                "downloadedAt": stored.get("downloaded_at"),
                "lastError": stored.get("last_error"),
                "status": "downloaded" if bool(stored.get("downloaded")) else "missing_local",
                "humanLength": human_size(length),
                "trimmed": bool(stored.get("trimmed")),
                "removed": bool(stored.get("removed")),
            }
        )
    items.sort(key=lambda item: f'{item["createDate"]} {item["createTime"]}', reverse=True)
    return items


def cached_status_payload(
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    product_id: int | None = None,
) -> dict:
    """Return what `status_payload` returns, without touching USB.

    Reads the cached catalog from state.json and enriches it with
    local download/transcription metadata. `connected` is always
    False — we haven't verified the device is live; the desktop
    client can use this for instant startup paint and then do the
    real USB probe asynchronously to update.
    """
    config = load_config(config_path)
    output_dir = resolved_output_dir(config)
    state = load_state(state_path)
    cache_key = str(product_id) if product_id else "default"
    cached_recs = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
    return {
        "connected": False,
        "outputDir": str(output_dir),
        "statePath": str(state_path.resolve()),
        "configPath": str(config_path.resolve()),
        "recordings": build_recording_status_items(
            cached_recs, state, output_dir, product_id=product_id
        ),
        "cached": True,
    }


def status_payload(timeout_ms: int = 5000, config_path: Path = DEFAULT_CONFIG_PATH, state_path: Path = DEFAULT_STATE_PATH, product_id: int | None = None) -> dict:
    config = load_config(config_path)
    output_dir = resolved_output_dir(config)
    state = load_state(state_path)
    payload = {
        "connected": False,
        "outputDir": str(output_dir),
        "statePath": str(state_path.resolve()),
        "configPath": str(config_path.resolve()),
        "recordings": [],
    }

    try:
        dev = find_device(product_id=product_id)
    except FileNotFoundError:
        owner = detect_usb_owner(product_id)
        error = "HiDock device not found"
        if owner:
            error += f" — device held by {owner}"
        payload["error"] = error
        # Use cached catalog so recordings persist when device is disconnected
        cache_key = str(product_id) if product_id else "default"
        cached_recs = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
        payload["recordings"] = build_recording_status_items(cached_recs, state, output_dir, product_id=product_id)
        return payload

    try:
        interface_number = prepare_device(dev)
    except usb.core.USBError as exc:
        payload["error"] = _enrich_usb_error(str(exc), product_id)
        cache_key = str(product_id) if product_id else "default"
        cached_recs = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
        payload["recordings"] = build_recording_status_items(cached_recs, state, output_dir, product_id=product_id)
        # The device was found but couldn't be opened (busy / held by another
        # process) — connected:false here is NOT an authoritative disconnect, so
        # flag it cached so the client doesn't reset its Connected baseline.
        payload["cached"] = True
        return payload

    try:
        # Previously we called query_file_count here as a cache-fast-path
        # optimisation, but it consumed firmware state in a way that
        # prevented query_file_list from picking up the full catalog on
        # the H1. Now we always do a full query_file_list and derive
        # the declared total from its header frame.
        cache_key = str(product_id) if product_id else "default"
        catalogs = state.get("catalogs", {})

        recordings = query_file_list(dev, request_id=2, timeout_ms=timeout_ms)
        # Cache the raw catalog for next time
        catalogs[cache_key] = {"recordings": recordings}
        state["catalogs"] = catalogs
        save_state(state, state_path)

        payload["connected"] = True
        payload["recordings"] = build_recording_status_items(recordings, state, output_dir, product_id=product_id)

        # Storage stats — the HiDock USB protocol we've implemented doesn't
        # expose a 'free space' query, so we derive usage from summed file
        # sizes. If the firmware truncated the list, the sum is a minimum
        # bound — we flag that to the client.
        total_bytes = sum(int(r.get("length", 0)) for r in recordings)
        # query_file_list sets a module-level hint on the function
        # object when it detects a declared-count > returned-count
        # scenario, so we can surface truncation accurately.
        declared = getattr(query_file_list, "_last_declared_total", None)
        truncated = declared is not None and declared > len(recordings)
        payload["storage"] = {
            "totalFiles": declared if declared is not None else len(recordings),
            "returnedFiles": len(recordings),
            "totalBytesReturned": total_bytes,
            "truncated": truncated,
        }

        if truncated:
            missing = declared - len(recordings)
            payload["warning"] = (
                f"{missing} newest recordings are hidden due to device firmware limits. "
                f"Delete old recordings from the device to see them all."
            )
    except Exception as exc:
        payload["error"] = f"Failed to query device: {exc}"
        # Fall back to the cached catalog so the desktop UI can still
        # show rows the user has already downloaded/transcribed. Covers
        # the timeout kill, protocol errors after prepare_device
        # succeeded, JSON decode failures, etc. The two earlier error
        # paths (FileNotFoundError, USBError on prepare_device) already
        # did this; this closes the gap for everything in between.
        #
        # The device was present (find_device + prepare_device succeeded) — the
        # live read just didn't complete (e.g. timeout). connected:false here is
        # NOT an authoritative disconnect, so flag it cached so the client keeps
        # its Connected baseline instead of treating the next live probe as a
        # fresh connect (the flapping that re-fired auto-download).
        payload["cached"] = True
        cache_key = str(product_id) if product_id else "default"
        cached_recs = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
        if cached_recs:
            payload["recordings"] = build_recording_status_items(cached_recs, state, output_dir, product_id=product_id)
    finally:
        release_device(dev, interface_number)
    return payload


def download_one(
    filename: str,
    length: int | None = None,
    output_dir: Path | None = None,
    timeout_ms: int = 5000,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    product_id: int | None = None,
) -> dict:
    config = load_config(config_path)
    if output_dir is None:
        output_dir = resolved_output_dir(config)
    else:
        output_dir = output_dir.expanduser().resolve()
    state = load_state(state_path)
    downloads = state["downloads"]

    global _active_dev, _active_intf
    dev = find_device(product_id=product_id)
    interface_number = prepare_device(dev)
    _active_dev = dev
    _active_intf = interface_number
    try:
        if length is None:
            recordings = query_file_list(dev, request_id=2, timeout_ms=timeout_ms)
            match = next((item for item in recordings if item["name"] == filename), None)
            if match is None:
                raise HiDockProtocolError(f"recording not found on device: {filename}")
            length = int(match["length"])
            signature = match["signature"]
        else:
            signature = None

        out_path = output_path_for(filename, output_dir)

        def _progress(received, total):
            pct = int(received * 100 / total) if total else 0
            print(f"PROGRESS:{received}:{total}:{pct}", file=sys.stderr, flush=True)

        written = transfer_file_stream_to_path(
            dev,
            filename,
            total_length=length,
            out_path=out_path,
            request_id=0x100,
            timeout_ms=timeout_ms,
            progress=_progress,
        )
    except Exception as exc:
        error_record = {
            **downloads.get(filename, {}),
            "downloaded": False,
            "last_error": str(exc),
            "output_path": str(output_path_for(filename, output_dir)),
            "updated_at": utc_now_iso(),
        }
        if product_id is not None:
            error_record["product_id"] = product_id
        downloads[filename] = error_record
        save_state(state, state_path)
        raise
    finally:
        release_device(dev, interface_number)
        _active_dev = None
        _active_intf = None

    record = {
        **downloads.get(filename, {}),
        "downloaded": written == length,
        "downloaded_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "output_path": str(out_path),
        "length": length,
        "last_error": None,
    }
    if product_id is not None:
        record["product_id"] = product_id
    if signature is not None:
        record["signature"] = signature
    downloads[filename] = record
    save_state(state, state_path)
    return {
        "filename": filename,
        "written": written,
        "expectedLength": length,
        "outputPath": str(out_path),
        "downloaded": written == length,
    }


def download_new(
    timeout_ms: int = 5000,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    product_id: int | None = None,
) -> dict:
    status = status_payload(timeout_ms=timeout_ms, config_path=config_path, state_path=state_path, product_id=product_id)
    if not status["connected"]:
        return {
            "connected": False,
            "outputDir": status["outputDir"],
            "downloaded": [],
            "skipped": [],
            "error": status.get("error"),
        }

    downloaded: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    for item in status["recordings"]:
        if item["downloaded"]:
            skipped.append({"filename": item["name"], "reason": "already_downloaded"})
            continue
        if item.get("removed"):
            # User deleted the local copy via the Mac app's Remove. Don't
            # silently re-pull it on the next auto-download cycle —
            # they'll have to explicitly Unremove first.
            skipped.append({"filename": item["name"], "reason": "user_removed"})
            continue
        # Emit a per-file marker on stderr so the Mac app can paint the
        # currently-downloading row as "Downloading" (yellow) instead of
        # leaving every not-yet-downloaded row stuck on "On device" until
        # the whole batch finishes. PROGRESS lines on the same channel
        # cover transfer percent; FILE_START / FILE_DONE bracket the file.
        print(f"FILE_START:{item['name']}", file=sys.stderr, flush=True)
        try:
            result = download_one(
                item["name"],
                length=item["length"],
                output_dir=Path(status["outputDir"]),
                timeout_ms=timeout_ms,
                config_path=config_path,
                state_path=state_path,
                product_id=product_id,
            )
            downloaded.append(result)
        except Exception as exc:
            # Resilience: one bad file (transient USB error, a recording the
            # device is still finalising, etc.) must NOT abort the whole batch
            # and leave every later recording un-downloaded. Record it and move
            # on — the next auto-download cycle retries it.
            print(f"download-new: failed for {item['name']}: {exc}", file=sys.stderr, flush=True)
            errors.append({"filename": item["name"], "error": str(exc)})
        finally:
            print(f"FILE_DONE:{item['name']}", file=sys.stderr, flush=True)

    return {
        "connected": True,
        "outputDir": status["outputDir"],
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
    }


def pull_file(filename: str, out_dir: Path, request_id: int = 1, timeout_ms: int = 5000) -> Path:
    dev = find_device()
    interface_number = prepare_device(dev)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / output_name_for(filename)

    request = build_transfer_request(request_id, filename)
    dev.write(OUT_ENDPOINT, request, timeout=timeout_ms)

    written = 0
    empty_reads = 0
    pending = b""

    with out_path.open("wb") as fh:
        while True:
            try:
                data = bytes(dev.read(IN_ENDPOINT, USB_READ_SIZE, timeout=timeout_ms))
            except usb.core.USBTimeoutError:
                empty_reads += 1
                if written and empty_reads >= MAX_EMPTY_READS:
                    break
                if empty_reads >= MAX_EMPTY_READS:
                    raise TimeoutError("timed out waiting for HiDock transfer data")
                continue

            empty_reads = 0
            pending += data
            frames, pending = extract_frames(pending)

            for cmd, req_id, payload in frames:
                if cmd != CMD_TRANSFER:
                    continue
                if req_id != request_id:
                    continue
                if not payload:
                    if written:
                        break
                    continue

                fh.write(payload)
                written += len(payload)

                # Current captures show framed chunks containing MP3 data.
                # Stop after the stream goes silent; do not try to trim yet.
                if written and not looks_like_mp3(payload) and len(payload) < 1024:
                    break

    release_device(dev, interface_number)
    if written == 0:
        raise HiDockProtocolError(f"no transfer payload received for {filename}")
    return out_path


# ── Volume (mass-storage) support ──────────────────────────────────────────────

VOLUME_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".wma"}


def _find_volumes() -> list[Path]:
    """Return mount points of removable/external volumes."""
    volumes_root = Path("/Volumes")
    if not volumes_root.exists():
        return []
    mounts = []
    for entry in volumes_root.iterdir():
        if entry.is_dir() and not entry.is_symlink() and entry.name != "Macintosh HD":
            mounts.append(entry)
    return sorted(mounts, key=lambda p: p.name)


def _safe_resolve(base: Path, user_path: str | None) -> Path:
    """Resolve a user-supplied subpath within a base directory safely.

    Raises ValueError if the resolved path escapes the base directory.
    """
    if user_path is None:
        return base
    # Reject obvious traversal attempts
    if ".." in user_path:
        raise ValueError(f"Path traversal detected in: {user_path}")
    resolved = (base / user_path).resolve()
    base_resolved = base.resolve()
    if not str(resolved).startswith(str(base_resolved)):
        raise ValueError(f"Path escapes base directory: {user_path}")
    return resolved


def _scan_audio_files(mount_point: Path, subpath: str | None = None) -> list[Path]:
    """Return all audio files under a volume mount point."""
    scan_root = _safe_resolve(mount_point, subpath)
    if not scan_root.is_dir():
        return []
    files = []
    for entry in scan_root.rglob("*"):
        if entry.is_file() and entry.suffix.lower() in VOLUME_AUDIO_EXTENSIONS:
            files.append(entry)
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _audio_file_metadata(audio_path: Path) -> dict:
    """Build a recording-like metadata dict from a filesystem audio file."""
    stat = audio_path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    duration = stat.st_size / 16000.0  # rough estimate
    duration_estimated = True
    try:
        from mutagen import File as MutagenFile
        mf = MutagenFile(str(audio_path))
        if mf and mf.info:
            duration = mf.info.length
            duration_estimated = False
    except ImportError as exc:
        _log_warn(
            f"mutagen unavailable; volume duration for {audio_path.name} "
            f"falling back to size/16000 estimate ({exc})"
        )
    except Exception as exc:
        _log_warn(f"mutagen read failed for volume file {audio_path}: {exc}")

    return {
        "name": audio_path.name,
        "createDate": mtime.strftime("%Y/%m/%d"),
        "createTime": mtime.strftime("%H:%M:%S"),
        "length": stat.st_size,
        "duration": round(duration, 1),
        "durationEstimated": duration_estimated,
        "version": 0,
        "mode": "external",
        "signature": md5_hex(f"{audio_path.name}:{stat.st_size}:{int(stat.st_mtime)}"),
    }


def scan_volumes() -> dict:
    """Enumerate mounted volumes and count audio files on each."""
    results = []
    for mount in _find_volumes():
        audio_files = _scan_audio_files(mount)
        if not audio_files:
            continue
        total_size = sum(f.stat().st_size for f in audio_files)
        extensions = sorted({f.suffix.lower() for f in audio_files})
        results.append({
            "volumeName": mount.name,
            "mountPoint": str(mount),
            "audioFileCount": len(audio_files),
            "totalSizeBytes": total_size,
            "audioExtensions": extensions,
        })
    return {"volumes": results}


def volume_status(
    volume_name: str,
    subpath: str | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
) -> dict:
    """Return recording status for a mounted volume (same shape as HiDock status)."""
    config = load_config(config_path)
    output_dir = resolved_output_dir(config)
    state = load_state(state_path)
    downloads = state.get("downloads", {})

    mount = Path("/Volumes") / volume_name
    connected = mount.is_dir()

    payload = {
        "connected": connected,
        "outputDir": str(output_dir),
        "statePath": str(state_path.resolve()),
        "configPath": str(config_path.resolve()),
        "recordings": [],
    }

    if not connected:
        payload["error"] = f"Volume '{volume_name}' is not mounted"
        return payload

    audio_files = _scan_audio_files(mount, subpath)
    items: list[dict] = []
    seen_names: set[str] = set()

    for audio_path in audio_files:
        meta = _audio_file_metadata(audio_path)
        name = meta["name"]
        state_key = f"vol:{volume_name}/{name}"
        seen_names.add(state_key)
        stored = downloads.get(state_key, {})
        output_path = Path(stored["output_path"]) if "output_path" in stored else output_dir / name
        local_exists = output_path.exists()
        downloaded = bool(stored.get("downloaded"))
        status = "downloaded" if downloaded else "on_device"
        if stored.get("last_error") and not downloaded:
            status = "failed"

        items.append({
            **meta,
            "sourcePath": str(audio_path),
            "outputPath": str(output_path),
            "outputName": name,
            "downloaded": downloaded,
            "localExists": local_exists,
            "downloadedAt": stored.get("downloaded_at"),
            "lastError": stored.get("last_error"),
            "status": status,
            "humanLength": human_size(meta["length"]),
            "trimmed": bool(stored.get("trimmed")),
            "removed": bool(stored.get("removed")),
        })

    # Include state-only entries for files no longer on the volume
    for state_key, stored in downloads.items():
        if not state_key.startswith(f"vol:{volume_name}/"):
            continue
        if state_key in seen_names:
            continue
        output_path = Path(stored["output_path"]) if "output_path" in stored else None
        local_exists = output_path.exists() if output_path else False
        items.append({
            "name": state_key.split("/", 1)[1],
            "createDate": "",
            "createTime": "",
            "length": stored.get("length", 0),
            "duration": 0,
            "durationEstimated": True,
            "version": 0,
            "mode": "external",
            "signature": stored.get("signature", ""),
            "sourcePath": "",
            "outputPath": str(output_path) if output_path else "",
            "outputName": state_key.split("/", 1)[1],
            "downloaded": bool(stored.get("downloaded")),
            "localExists": local_exists,
            "downloadedAt": stored.get("downloaded_at"),
            "lastError": stored.get("last_error"),
            "status": "downloaded" if stored.get("downloaded") else "missing_local",
            "humanLength": human_size(stored.get("length", 0)),
            "trimmed": bool(stored.get("trimmed")),
            "removed": bool(stored.get("removed")),
        })

    items.sort(key=lambda item: f'{item["createDate"]} {item["createTime"]}', reverse=True)
    payload["recordings"] = items
    return payload


def volume_import_one(
    filename: str,
    volume_name: str,
    subpath: str | None = None,
    output_dir: Path | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
) -> dict:
    """Copy one audio file from a mounted volume to the recordings folder."""
    # Validate filename to prevent path traversal
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return {
            "filename": filename,
            "written": 0,
            "expectedLength": 0,
            "outputPath": "",
            "downloaded": False,
            "error": f"Invalid filename: {filename}",
        }

    if output_dir is None:
        config = load_config(config_path)
        output_dir = resolved_output_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)

    mount = Path("/Volumes") / volume_name
    scan_root = _safe_resolve(mount, subpath)
    source = scan_root / filename

    # Verify source doesn't escape scan_root after resolution
    if not str(source.resolve()).startswith(str(scan_root.resolve())):
        return {
            "filename": filename,
            "written": 0,
            "expectedLength": 0,
            "outputPath": "",
            "downloaded": False,
            "error": f"Path traversal detected: {filename}",
        }

    state_key = f"vol:{volume_name}/{filename}"
    state = load_state(state_path)
    downloads = state.get("downloads", {})

    if not source.is_file():
        downloads[state_key] = {
            **downloads.get(state_key, {}),
            "downloaded": False,
            "updated_at": utc_now_iso(),
            "last_error": f"Source file not found: {source}",
        }
        save_state(state, state_path)
        return {
            "filename": filename,
            "written": 0,
            "expectedLength": 0,
            "outputPath": "",
            "downloaded": False,
            "error": f"Source file not found: {source}",
        }

    stat = source.stat()
    out_path = output_dir / filename
    shutil.copy2(str(source), str(out_path))
    written = out_path.stat().st_size

    meta = _audio_file_metadata(source)
    downloads[state_key] = {
        "downloaded": written == stat.st_size,
        "downloaded_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "output_path": str(out_path),
        "length": stat.st_size,
        "last_error": None,
        "volume_name": volume_name,
        "signature": meta["signature"],
    }
    save_state(state, state_path)

    return {
        "filename": filename,
        "written": written,
        "expectedLength": stat.st_size,
        "outputPath": str(out_path),
        "downloaded": written == stat.st_size,
    }


def volume_import_new(
    volume_name: str,
    subpath: str | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
) -> dict:
    """Import all new audio files from a mounted volume."""
    status = volume_status(volume_name, subpath=subpath, config_path=config_path, state_path=state_path)
    if not status["connected"]:
        return {
            "connected": False,
            "outputDir": status["outputDir"],
            "downloaded": [],
            "skipped": [],
            "error": status.get("error"),
        }

    downloaded: list[dict] = []
    skipped: list[dict] = []
    for item in status["recordings"]:
        if item["downloaded"]:
            skipped.append({"filename": item["name"], "reason": "already_downloaded"})
            continue
        result = volume_import_one(
            item["name"],
            volume_name,
            subpath=subpath,
            output_dir=Path(status["outputDir"]),
            config_path=config_path,
            state_path=state_path,
        )
        downloaded.append(result)

    return {
        "connected": True,
        "outputDir": status["outputDir"],
        "downloaded": downloaded,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", type=int, default=None, help="USB product ID to target a specific HiDock model")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Report sync status and device recordings as JSON")
    status.add_argument("--timeout-ms", type=int, default=5000, help="USB read/write timeout")

    # `cached-status` returns what `status` returns when the device is
    # unreachable: connected=false, recordings from state.json's cached
    # catalog. Meant for instant startup — desktop clients can show
    # already-downloaded rows immediately without waiting 10s+ for USB
    # enumeration to complete.
    sub.add_parser("cached-status", help="Report cached catalog from state.json without touching USB")

    set_output = sub.add_parser("set-output", help="Persist the default output directory")
    set_output.add_argument("path", help="Directory for downloaded recordings")

    download = sub.add_parser("download", help="Download one recording directly from the dock")
    download.add_argument("filename", help="Device-side filename, e.g. 2026Mar09-131439-Rec39.hda")
    download.add_argument("--length", type=int, default=None, help="Known device-side length in bytes")
    download.add_argument("--timeout-ms", type=int, default=5000, help="USB read/write timeout")

    sub.add_parser("list-devices", help="List all connected HiDock devices as JSON")

    mark_dl = sub.add_parser("mark-downloaded", help="Mark recordings as already downloaded without transferring")
    mark_dl.add_argument("filenames", nargs="+", help="Device-side filenames to mark")
    mark_dl.add_argument("--volume-name", default=None, help="For volume devices: prefix state keys with vol:<name>/")
    mark_dl.add_argument("--plaud-account", default=None, help="For Plaud devices: prefix state keys with plaud:<account>/")

    unmark_dl = sub.add_parser("unmark-downloaded", help="Unmark recordings so they can be re-downloaded")
    unmark_dl.add_argument("filenames", nargs="+", help="Device-side filenames to unmark")
    unmark_dl.add_argument("--volume-name", default=None, help="For volume devices")
    unmark_dl.add_argument("--plaud-account", default=None, help="For Plaud devices")

    mark_trim = sub.add_parser("mark-trimmed", help="Flag recordings as locally trimmed (UI icon + re-download warning)")
    mark_trim.add_argument("filenames", nargs="+", help="Device-side filenames to flag as trimmed")

    unmark_trim = sub.add_parser("unmark-trimmed", help="Clear the trimmed flag on recordings")
    unmark_trim.add_argument("filenames", nargs="+", help="Device-side filenames to clear trimmed flag on")

    mark_removed_p = sub.add_parser("mark-removed", help="Flag recordings as locally removed (excluded from auto-download/transcribe)")
    mark_removed_p.add_argument("filenames", nargs="+", help="Device-side filenames to flag as removed")
    mark_removed_p.add_argument("--plaud-account", default=None, help="For Plaud devices")

    unmark_removed_p = sub.add_parser("unmark-removed", help="Clear the removed flag on recordings")
    unmark_removed_p.add_argument("filenames", nargs="+", help="Device-side filenames to clear removed flag on")
    unmark_removed_p.add_argument("--plaud-account", default=None, help="For Plaud devices")

    cand_p = sub.add_parser("merge-candidates",
                            help="List groups of recordings that look like one conversation split across files")
    cand_p.add_argument("--include-low-confidence", action="store_true",
                        help="Also surface candidates below the high-confidence cutoff (score < 8)")

    dismiss_p = sub.add_parser("dismiss-merge-pair",
                               help="Mark a chain of recordings as 'not the same conversation' (sticky)")
    dismiss_p.add_argument("filenames", nargs="+", help="Ordered device-side filenames in the chain to dismiss")

    download_new_cmd = sub.add_parser("download-new", help="Download every recording not yet present in local state")
    download_new_cmd.add_argument("--timeout-ms", type=int, default=5000, help="USB read/write timeout")

    pull = sub.add_parser("pull", help="Pull one known device-side .hda file")
    pull.add_argument("filename", help="Device-side filename, e.g. 2026Feb26-160117-Rec35.hda")
    pull.add_argument("--out", default="out", help="Output directory")
    pull.add_argument("--request-id", type=lambda s: int(s, 0), default=1, help="Request id to use")
    pull.add_argument("--timeout-ms", type=int, default=5000, help="USB read/write timeout")

    ls = sub.add_parser("list-files", help="List files reported by the dock")
    ls.add_argument("--request-id", type=lambda s: int(s, 0), default=2, help="Request id to use")
    ls.add_argument("--timeout-ms", type=int, default=5000, help="USB read/write timeout")

    block = sub.add_parser("pull-block", help="Pull one file using GET_FILE_BLOCK")
    block.add_argument("filename")
    block.add_argument("length", type=int)
    block.add_argument("--out", default="out")
    block.add_argument("--request-id", type=lambda s: int(s, 0), default=4)
    block.add_argument("--timeout-ms", type=int, default=5000)

    partial = sub.add_parser("pull-partial", help="Pull one file chunk using TRANSFER_FILE_PARTIAL")
    partial.add_argument("filename")
    partial.add_argument("length", type=int)
    partial.add_argument("--offset", type=int, default=0)
    partial.add_argument("--out", default="out")
    partial.add_argument("--request-id", type=lambda s: int(s, 0), default=5)
    partial.add_argument("--timeout-ms", type=int, default=5000)

    full = sub.add_parser("pull-full", help="Pull one full file by looping TRANSFER_FILE_PARTIAL")
    full.add_argument("filename")
    full.add_argument("length", type=int)
    full.add_argument("--out", default="out")
    full.add_argument("--chunk-size", type=int, default=8180)
    full.add_argument("--request-id-start", type=lambda s: int(s, 0), default=5)
    full.add_argument("--timeout-ms", type=int, default=5000)

    transfer = sub.add_parser("pull-transfer", help="Pull one full file using TRANSFER_FILE stream")
    transfer.add_argument("filename")
    transfer.add_argument("length", type=int)
    transfer.add_argument("--out", default="out")
    transfer.add_argument("--request-id", type=lambda s: int(s, 0), default=6)
    transfer.add_argument("--timeout-ms", type=int, default=5000)

    # Volume (mass-storage) commands
    sub.add_parser("scan-volumes", help="List mounted volumes with audio files")

    vol_status = sub.add_parser("volume-status", help="Report recordings on a mounted volume as JSON")
    vol_status.add_argument("--volume-name", required=True, help="Name of the mounted volume")
    vol_status.add_argument("--subpath", default=None, help="Subdirectory within the volume to scan")

    vol_import = sub.add_parser("volume-import", help="Import one audio file from a mounted volume")
    vol_import.add_argument("filename", help="Audio filename on the volume")
    vol_import.add_argument("--volume-name", required=True, help="Name of the mounted volume")
    vol_import.add_argument("--subpath", default=None, help="Subdirectory within the volume")

    vol_import_new = sub.add_parser("volume-import-new", help="Import all new audio files from a mounted volume")
    vol_import_new.add_argument("--volume-name", required=True, help="Name of the mounted volume")
    vol_import_new.add_argument("--subpath", default=None, help="Subdirectory within the volume")

    plaud_status = sub.add_parser("plaud-status", help="Report Plaud cloud recordings as JSON")
    plaud_status.add_argument("--account-id", required=True, help="Stable Plaud account identifier")

    plaud_cached = sub.add_parser("plaud-cached-status", help="Report cached Plaud catalog / local downloads from state.json without touching the network")
    plaud_cached.add_argument("--account-id", required=True, help="Stable Plaud account identifier")

    plaud_download = sub.add_parser("plaud-download", help="Download one Plaud cloud recording")
    plaud_download.add_argument("recording_id", help="Plaud file id")
    plaud_download.add_argument("--account-id", required=True, help="Stable Plaud account identifier")

    plaud_download_new = sub.add_parser("plaud-download-new", help="Download every new Plaud cloud recording")
    plaud_download_new.add_argument("--account-id", required=True, help="Stable Plaud account identifier")

    args = parser.parse_args()

    if args.command == "list-devices":
        devices = []
        for dev in usb.core.find(idVendor=VENDOR_ID, find_all=True):
            devices.append({
                "vendorId": dev.idVendor,
                "productId": dev.idProduct,
                "productName": usb.util.get_string(dev, dev.iProduct) if dev.iProduct else None,
                "serialNumber": usb.util.get_string(dev, dev.iSerialNumber) if dev.iSerialNumber else None,
                "bus": dev.bus,
                "address": dev.address,
            })
        print(json.dumps({"devices": devices}, indent=2))
        return 0
    if args.command == "plaud-status":
        config = load_config()
        state = load_state()
        output_dir = resolved_output_dir(config)
        payload = plaud_client.status_payload(output_dir, state, account_id=args.account_id)
        payload["statePath"] = str(DEFAULT_STATE_PATH.resolve())
        payload["configPath"] = str(DEFAULT_CONFIG_PATH.resolve())
        _attach_refreshed_plaud_tokens(payload, args.account_id)
        save_state(state)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "plaud-cached-status":
        # Network-free: paint cached catalog / local downloads instantly on
        # launch, before the live plaud-status cloud probe resolves.
        config = load_config()
        state = load_state()
        output_dir = resolved_output_dir(config)
        payload = plaud_client.cached_status_payload(output_dir, state, account_id=args.account_id)
        payload["statePath"] = str(DEFAULT_STATE_PATH.resolve())
        payload["configPath"] = str(DEFAULT_CONFIG_PATH.resolve())
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "plaud-download":
        config = load_config()
        state = load_state()
        output_dir = resolved_output_dir(config)
        try:
            payload = plaud_client.download_one(
                args.recording_id,
                output_dir,
                state,
                account_id=args.account_id,
            )
            save_state(state)
        except Exception as exc:
            state_key = f"plaud:{args.account_id}:{args.recording_id}"
            downloads = state.setdefault("downloads", {})
            downloads[state_key] = {
                **downloads.get(state_key, {}),
                "downloaded": False,
                "updated_at": utc_now_iso(),
                "last_error": str(exc),
                "source": "plaud",
                "account_id": args.account_id,
            }
            save_state(state)
            raise
        _attach_refreshed_plaud_tokens(payload, args.account_id)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "plaud-download-new":
        config = load_config()
        state = load_state()
        output_dir = resolved_output_dir(config)
        payload = plaud_client.download_new(output_dir, state, account_id=args.account_id)
        _attach_refreshed_plaud_tokens(payload, args.account_id)
        save_state(state)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "mark-downloaded":
        state = load_state()
        downloads = state["downloads"]
        config = load_config()
        output_dir = resolved_output_dir(config)
        # Look up catalog for size info when marking new entries
        cache_key = str(args.product_id) if args.product_id else "default"
        cached_recs = {r["name"]: r for r in state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])}
        marked = []
        vol_prefix = f"vol:{args.volume_name}/" if args.volume_name else ""
        plaud_prefix = f"plaud:{args.plaud_account}:" if args.plaud_account else ""
        for filename in args.filenames:
            state_key = f"{plaud_prefix}{vol_prefix}{filename}"
            existing = downloads.get(state_key, {})
            # Populate length/output_path from catalog if not already set
            if not existing.get("length") and filename in cached_recs:
                existing.setdefault("length", cached_recs[filename].get("length"))
            if not existing.get("output_path"):
                existing["output_path"] = str(output_path_for(filename, output_dir))
            record = {
                **existing,
                "downloaded": True,
                "downloaded_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "last_error": None,
            }
            if args.product_id is not None:
                record["product_id"] = args.product_id
            if args.plaud_account:
                record["source"] = "plaud"
                record["account_id"] = args.plaud_account
            downloads[state_key] = record
            marked.append(filename)
        save_state(state)
        print(json.dumps({"marked": marked}, indent=2))
        return 0
    if args.command == "unmark-downloaded":
        state = load_state()
        downloads = state["downloads"]
        unmarked = []
        vol_prefix = f"vol:{args.volume_name}/" if args.volume_name else ""
        plaud_prefix = f"plaud:{args.plaud_account}:" if args.plaud_account else ""
        for filename in args.filenames:
            state_key = f"{plaud_prefix}{vol_prefix}{filename}"
            if state_key in downloads:
                downloads[state_key]["downloaded"] = False
                downloads[state_key]["updated_at"] = utc_now_iso()
                unmarked.append(filename)
        save_state(state)
        print(json.dumps({"unmarked": unmarked}, indent=2))
        return 0
    if args.command == "mark-trimmed":
        state = load_state()
        downloads = state["downloads"]
        flagged = []
        for filename in args.filenames:
            if filename in downloads:
                downloads[filename]["trimmed"] = True
                downloads[filename]["updated_at"] = utc_now_iso()
                flagged.append(filename)
        save_state(state)
        print(json.dumps({"trimmed": flagged}, indent=2))
        return 0
    if args.command == "unmark-trimmed":
        state = load_state()
        downloads = state["downloads"]
        cleared = []
        for filename in args.filenames:
            if filename in downloads and "trimmed" in downloads[filename]:
                del downloads[filename]["trimmed"]
                downloads[filename]["updated_at"] = utc_now_iso()
                cleared.append(filename)
        save_state(state)
        print(json.dumps({"untrimmed": cleared}, indent=2))
        return 0
    if args.command == "mark-removed":
        state = load_state()
        downloads = state["downloads"]
        flagged = []
        plaud_prefix = f"plaud:{args.plaud_account}:" if args.plaud_account else ""
        for filename in args.filenames:
            state_key = f"{plaud_prefix}{filename}"
            if state_key not in downloads:
                downloads[state_key] = {"downloaded": False}
            if state_key in downloads:
                downloads[state_key]["removed"] = True
                downloads[state_key]["updated_at"] = utc_now_iso()
                flagged.append(filename)
        save_state(state)
        print(json.dumps({"removed": flagged}, indent=2))
        return 0
    if args.command == "unmark-removed":
        state = load_state()
        downloads = state["downloads"]
        cleared = []
        plaud_prefix = f"plaud:{args.plaud_account}:" if args.plaud_account else ""
        for filename in args.filenames:
            state_key = f"{plaud_prefix}{filename}"
            if state_key in downloads and "removed" in downloads[state_key]:
                del downloads[state_key]["removed"]
                downloads[state_key]["updated_at"] = utc_now_iso()
                cleared.append(filename)
        save_state(state)
        print(json.dumps({"unremoved": cleared}, indent=2))
        return 0
    if args.command == "merge-candidates":
        from shared.merge_finder import find_candidates, candidates_to_payload
        state = load_state()
        chains = find_candidates(state.get("downloads", {}))
        if not args.include_low_confidence:
            chains = [c for c in chains if c.score >= 8]
        print(json.dumps(candidates_to_payload(chains), indent=2))
        return 0
    if args.command == "dismiss-merge-pair":
        from shared.merge_finder import dismiss_chain
        dismiss_chain(args.filenames)
        print(json.dumps({"dismissed": args.filenames}, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(status_payload(timeout_ms=args.timeout_ms, product_id=args.product_id), indent=2))
        return 0
    if args.command == "cached-status":
        print(json.dumps(cached_status_payload(product_id=args.product_id), indent=2))
        return 0
    if args.command == "set-output":
        config = load_config()
        output_dir = Path(args.path).expanduser().resolve()
        config["output_dir"] = str(output_dir)
        save_config(config)

        # Scan new folder and remap state entries to match existing files
        state = load_state()
        downloads = state.get("downloads", {})
        remapped = 0
        for name, record in downloads.items():
            expected = output_dir / output_name_for(name)
            old_path = record.get("output_path", "")
            if expected.exists():
                if str(expected) != old_path:
                    record["output_path"] = str(expected)
                    remapped += 1
            elif not Path(old_path).exists() if old_path else True:
                # Old path gone and not in new folder either — clear the path
                record["output_path"] = str(expected)
                remapped += 1
        if remapped:
            save_state(state)

        print(json.dumps({"outputDir": str(output_dir), "configPath": str(DEFAULT_CONFIG_PATH.resolve()), "remapped": remapped}, indent=2))
        return 0
    if args.command == "download":
        result = download_one(
            args.filename,
            length=args.length,
            timeout_ms=args.timeout_ms,
            product_id=args.product_id,
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "download-new":
        print(json.dumps(download_new(timeout_ms=args.timeout_ms, product_id=args.product_id), indent=2))
        return 0

    if args.command == "pull":
        out_path = pull_file(
            filename=args.filename,
            out_dir=Path(args.out),
            request_id=args.request_id,
            timeout_ms=args.timeout_ms,
        )
        print(out_path)
        return 0
    if args.command == "list-files":
        dev = find_device()
        interface_number = prepare_device(dev)
        try:
            items = query_file_list(dev, request_id=args.request_id, timeout_ms=args.timeout_ms)
        finally:
            release_device(dev, interface_number)
        print(json.dumps(items, indent=2))
        return 0
    if args.command == "pull-block":
        dev = find_device()
        interface_number = prepare_device(dev)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / output_name_for(args.filename)
        try:
            data = get_file_block(dev, args.filename, args.length, request_id=args.request_id, timeout_ms=args.timeout_ms)
        finally:
            release_device(dev, interface_number)
        out_path.write_bytes(data)
        print(out_path)
        return 0
    if args.command == "pull-partial":
        dev = find_device()
        interface_number = prepare_device(dev)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / output_name_for(args.filename)
        try:
            data = read_file_partial(
                dev,
                args.filename,
                offset=args.offset,
                length=args.length,
                request_id=args.request_id,
                timeout_ms=args.timeout_ms,
            )
        finally:
            release_device(dev, interface_number)
        out_path.write_bytes(data)
        print(out_path)
        return 0
    if args.command == "pull-full":
        dev = find_device()
        interface_number = prepare_device(dev)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / output_name_for(args.filename)
        try:
            written = pull_file_by_partials_to_path(
                dev,
                args.filename,
                total_length=args.length,
                out_path=out_path,
                chunk_size=args.chunk_size,
                request_id_start=args.request_id_start,
                timeout_ms=args.timeout_ms,
            )
        finally:
            release_device(dev, interface_number)
        print(f"written={written}")
        print(out_path)
        return 0
    if args.command == "pull-transfer":
        dev = find_device()
        interface_number = prepare_device(dev)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / output_name_for(args.filename)
        try:
            written = transfer_file_stream_to_path(
                dev,
                args.filename,
                total_length=args.length,
                out_path=out_path,
                request_id=args.request_id,
                timeout_ms=args.timeout_ms,
            )
        finally:
            release_device(dev, interface_number)
        print(f"written={written}")
        print(out_path)
        return 0

    if args.command == "scan-volumes":
        print(json.dumps(scan_volumes(), indent=2))
        return 0
    if args.command == "volume-status":
        result = volume_status(args.volume_name, subpath=args.subpath)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "volume-import":
        result = volume_import_one(args.filename, args.volume_name, subpath=args.subpath)
        print(json.dumps(result, indent=2))
        return 0 if result.get("downloaded") else 1
    if args.command == "volume-import-new":
        result = volume_import_new(args.volume_name, subpath=args.subpath)
        print(json.dumps(result, indent=2))
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
