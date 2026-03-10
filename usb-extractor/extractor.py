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
import os
import struct
from pathlib import Path

import usb.core
import usb.util


VENDOR_ID = 4310
PRODUCT_ID = 45068
OUT_ENDPOINT = 1
IN_ENDPOINT = 0x82
USB_READ_SIZE = 512000
HEADER = b"\x12\x34"
CMD_TRANSFER = 0x0005
CMD_QUERY_FILE_LIST = 0x0004
CMD_QUERY_FILE_COUNT = 0x0006
CMD_GET_FILE_BLOCK = 0x000D
CMD_TRANSFER_FILE_PARTIAL = 0x0015
MAX_EMPTY_READS = 4


class HiDockProtocolError(RuntimeError):
    pass


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


def output_name_for(filename: str) -> str:
    base = Path(filename).name
    if base.lower().endswith(".hda"):
        return base[:-4] + ".mp3"
    return base + ".mp3"


def md5_hex(text: str) -> str:
    import hashlib

    return hashlib.md5(text.encode("utf-8")).hexdigest()


def find_device():
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        raise FileNotFoundError(f"HiDock device {VENDOR_ID}:{PRODUCT_ID} not found")
    return dev


def prepare_device(dev):
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


def release_device(dev, interface_number: int) -> None:
    try:
        usb.util.release_interface(dev, interface_number)
    except usb.core.USBError:
        pass
    usb.util.dispose_resources(dev)


def parse_frame(buf: bytes) -> tuple[int, int, bytes]:
    if len(buf) < 12:
        raise HiDockProtocolError(f"short frame: {len(buf)} bytes")
    if buf[:2] != HEADER:
        raise HiDockProtocolError(f"unexpected frame header: {buf[:8].hex(' ')}")
    cmd = struct.unpack(">H", buf[2:4])[0]
    req_id = struct.unpack(">I", buf[4:8])[0]
    payload_len = struct.unpack(">I", buf[8:12])[0]
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
    if len(data) >= 6 and (data[0] & ~0xFF) == 0 and (data[1] & ~0xFF) == 0:
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

        duration = 0.0
        if version == 1:
            duration = file_len / 32 * 2
        elif version == 2:
            duration = (file_len - 44) / 48 / 2
        elif version == 3:
            duration = (file_len - 44) / 48 / 2 / 2
        elif version == 5:
            duration = file_len / 12
        elif version == 6:
            duration = file_len / 16
        elif version == 7:
            duration = file_len / 10
        else:
            duration = file_len / 32 * 4

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
            if empty_reads >= 2:
                break
            continue
        pending += data
        parsed, pending = extract_frames(pending)
        for frame in parsed:
            cmd, req, body = frame
            if req == request_id:
                frames.append(frame)
    return frames


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
    expected_count = None
    try:
        expected_count = query_file_count(dev, request_id=request_id - 1, timeout_ms=timeout_ms)
    except Exception:
        pass

    frames = send_and_collect(dev, CMD_QUERY_FILE_LIST, request_id, timeout_ms=timeout_ms, max_reads=64)
    payloads = [payload for cmd, _, payload in frames if cmd == CMD_QUERY_FILE_LIST]
    if not payloads:
        raise HiDockProtocolError("no file list response received")
    return parse_query_file_list_payload(payloads, expected_count=expected_count)


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
    payload = build_name_only_payload(filename)
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
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_name_only_payload(filename)
    request = build_simple_request(CMD_TRANSFER, request_id, payload)
    dev.write(OUT_ENDPOINT, request, timeout=timeout_ms)

    pending = b""
    received = 0
    timeouts = 0
    started = False
    last_seq = request_id

    with out_path.open("wb") as fh:
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
                        return received
                    continue
                if received + len(body) > total_length:
                    body = body[: total_length - received]
                fh.write(body)
                received += len(body)
                if received == len(body) or received % (1024 * 256) < len(body) or received >= total_length:
                    print(f"{received}/{total_length}")
                if received >= total_length:
                    return received
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
            if chunk_index == 1 or chunk_index % progress_every == 0 or offset >= total_length:
                print(f"{offset}/{total_length}")

    return offset


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


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

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

    args = parser.parse_args()

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
        import json

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

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
