"""Unit tests for extractor.py pure functions."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

# conftest.py sets up the usb mock before this import
from extractor import (
    HEADER,
    CMD_TRANSFER,
    CMD_QUERY_FILE_LIST,
    MAX_PAYLOAD_SIZE,
    HiDockProtocolError,
    human_size,
    build_transfer_request,
    build_simple_request,
    build_name_only_payload,
    build_length_name_payload,
    build_offset_length_name_payload,
    validate_filename,
    output_name_for,
    output_path_for,
    md5_hex,
    parse_frame,
    extract_frames,
    looks_like_mp3,
    bcdish_filename_to_datetime,
    load_json_file,
    save_json_file,
    build_recording_status_items,
    utc_now_iso,
)


# ---------------------------------------------------------------------------
# human_size
# ---------------------------------------------------------------------------
class TestHumanSize:
    def test_bytes(self):
        assert human_size(0) == "0 B"
        assert human_size(512) == "512 B"
        assert human_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert human_size(1024) == "1.0 KB"
        assert human_size(1536) == "1.5 KB"

    def test_megabytes(self):
        assert human_size(1024 * 1024) == "1.0 MB"
        assert human_size(5 * 1024 * 1024) == "5.0 MB"

    def test_gigabytes(self):
        assert human_size(1024 ** 3) == "1.0 GB"
        assert human_size(2 * 1024 ** 3) == "2.0 GB"


# ---------------------------------------------------------------------------
# output_name_for / output_path_for
# ---------------------------------------------------------------------------
class TestOutputNaming:
    def test_hda_to_mp3(self):
        assert output_name_for("2026Feb25-111702-Rec25.hda") == "2026Feb25-111702-Rec25.mp3"

    def test_wav_gets_mp3_suffix(self):
        assert output_name_for("recording.wav") == "recording.wav.mp3"

    def test_already_mp3(self):
        assert output_name_for("file.mp3") == "file.mp3.mp3"

    def test_path_traversal_rejected(self):
        # validate_filename rejects path traversal attempts
        with pytest.raises(HiDockProtocolError, match="unsafe filename"):
            output_name_for("../../etc/passwd.hda")

    def test_output_path_for(self):
        result = output_path_for("test.hda", Path("/tmp/out"))
        assert result == Path("/tmp/out/test.mp3")


# ---------------------------------------------------------------------------
# md5_hex
# ---------------------------------------------------------------------------
class TestMd5Hex:
    def test_known_hash(self):
        assert md5_hex("hello") == "5d41402abc4b2a76b9719d911017c592"

    def test_empty_string(self):
        assert md5_hex("") == "d41d8cd98f00b204e9800998ecf8427e"

    def test_deterministic(self):
        assert md5_hex("test") == md5_hex("test")


# ---------------------------------------------------------------------------
# build_*_request / payload builders
# ---------------------------------------------------------------------------
class TestRequestBuilders:
    def test_transfer_request_structure(self):
        req = build_transfer_request(1, "test.hda")
        assert req[:2] == HEADER
        cmd = struct.unpack(">H", req[2:4])[0]
        assert cmd == CMD_TRANSFER
        req_id = struct.unpack(">I", req[4:8])[0]
        assert req_id == 1
        payload_len = struct.unpack(">I", req[8:12])[0]
        assert payload_len == len(b"test.hda")
        assert req[12:] == b"test.hda"

    def test_simple_request_empty_payload(self):
        req = build_simple_request(CMD_QUERY_FILE_LIST, 2)
        assert req[:2] == HEADER
        payload_len = struct.unpack(">I", req[8:12])[0]
        assert payload_len == 0
        assert len(req) == 12

    def test_simple_request_with_payload(self):
        req = build_simple_request(0x0001, 3, b"\x00\x01")
        payload_len = struct.unpack(">I", req[8:12])[0]
        assert payload_len == 2
        assert req[12:] == b"\x00\x01"

    def test_name_only_payload(self):
        assert build_name_only_payload("hello.hda") == b"hello.hda"

    def test_length_name_payload(self):
        result = build_length_name_payload("f.hda", 1024)
        assert result[:4] == struct.pack(">I", 1024)
        assert result[4:] == b"f.hda"

    def test_offset_length_name_payload(self):
        result = build_offset_length_name_payload("f.hda", 100, 200)
        assert result[:4] == struct.pack(">I", 100)
        assert result[4:8] == struct.pack(">I", 200)
        assert result[8:] == b"f.hda"


# ---------------------------------------------------------------------------
# parse_frame
# ---------------------------------------------------------------------------
class TestParseFrame:
    def _make_frame(self, cmd: int, req_id: int, payload: bytes) -> bytes:
        return (
            HEADER
            + struct.pack(">H", cmd)
            + struct.pack(">I", req_id)
            + struct.pack(">I", len(payload))
            + payload
        )

    def test_valid_frame(self):
        frame = self._make_frame(0x0005, 1, b"hello")
        cmd, req_id, payload = parse_frame(frame)
        assert cmd == 0x0005
        assert req_id == 1
        assert payload == b"hello"

    def test_empty_payload(self):
        frame = self._make_frame(0x0001, 42, b"")
        cmd, req_id, payload = parse_frame(frame)
        assert cmd == 0x0001
        assert req_id == 42
        assert payload == b""

    def test_short_frame_raises(self):
        with pytest.raises(HiDockProtocolError, match="short frame"):
            parse_frame(b"\x12\x34\x00\x05")

    def test_bad_header_raises(self):
        bad = b"\xAB\xCD" + b"\x00" * 10
        with pytest.raises(HiDockProtocolError, match="unexpected frame header"):
            parse_frame(bad)


# ---------------------------------------------------------------------------
# extract_frames
# ---------------------------------------------------------------------------
class TestExtractFrames:
    def _make_frame(self, cmd: int, payload: bytes) -> bytes:
        return (
            HEADER
            + struct.pack(">H", cmd)
            + struct.pack(">I", 0)
            + struct.pack(">I", len(payload))
            + payload
        )

    def test_single_frame(self):
        frame = self._make_frame(1, b"data")
        frames, remaining = extract_frames(frame)
        assert len(frames) == 1
        assert frames[0][0] == 1
        assert frames[0][2] == b"data"
        assert remaining == b""

    def test_two_frames(self):
        buf = self._make_frame(1, b"a") + self._make_frame(2, b"b")
        frames, remaining = extract_frames(buf)
        assert len(frames) == 2
        assert frames[0][2] == b"a"
        assert frames[1][2] == b"b"
        assert remaining == b""

    def test_partial_frame_returned_as_remaining(self):
        complete = self._make_frame(1, b"ok")
        partial = HEADER + b"\x00\x02"  # incomplete header
        frames, remaining = extract_frames(complete + partial)
        assert len(frames) == 1
        assert remaining == partial

    def test_empty_buffer(self):
        frames, remaining = extract_frames(b"")
        assert frames == []
        assert remaining == b""

    def test_garbage_before_header(self):
        frame = self._make_frame(1, b"x")
        buf = b"\xff\xff\xff" + frame
        frames, remaining = extract_frames(buf)
        assert len(frames) == 1
        assert frames[0][2] == b"x"


# ---------------------------------------------------------------------------
# looks_like_mp3
# ---------------------------------------------------------------------------
class TestLooksLikeMp3:
    def test_mp3_sync_word_ff_f3(self):
        assert looks_like_mp3(b"\x00\xff\xf3\x00") is True

    def test_mp3_sync_word_ff_fb(self):
        assert looks_like_mp3(b"\x00\xff\xfb\x00") is True

    def test_not_mp3(self):
        assert looks_like_mp3(b"\x00\x00\x00\x00") is False

    def test_empty(self):
        assert looks_like_mp3(b"") is False


# ---------------------------------------------------------------------------
# bcdish_filename_to_datetime
# ---------------------------------------------------------------------------
class TestBcdishFilenameDatetime:
    def test_valid_filename(self):
        result = bcdish_filename_to_datetime("2026Feb25-111702-Rec25.hda")
        assert result is not None
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 25
        assert result.hour == 11
        assert result.minute == 17
        assert result.second == 2

    def test_valid_wav(self):
        result = bcdish_filename_to_datetime("2026Mar10-130028-Rec43.wav")
        assert result is not None
        assert result.month == 3

    def test_invalid_extension(self):
        assert bcdish_filename_to_datetime("2026Feb25-111702-Rec25.mp3") is None

    def test_invalid_format(self):
        assert bcdish_filename_to_datetime("not-a-recording.hda") is None

    def test_invalid_month(self):
        assert bcdish_filename_to_datetime("2026Xyz25-111702-Rec25.hda") is None


# ---------------------------------------------------------------------------
# load_json_file / save_json_file
# ---------------------------------------------------------------------------
class TestJsonFileIO:
    def test_load_missing_file(self, tmp_path):
        result = load_json_file(tmp_path / "nope.json", {"default": True})
        assert result == {"default": True}

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "test.json"
        data = {"key": "value", "num": 42}
        save_json_file(path, data)
        loaded = load_json_file(path, {})
        assert loaded == data

    def test_load_corrupt_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        result = load_json_file(path, {"fallback": True})
        assert result == {"fallback": True}


# ---------------------------------------------------------------------------
# build_recording_status_items
# ---------------------------------------------------------------------------
class TestBuildRecordingStatusItems:
    def test_on_device_recording(self, tmp_path):
        recordings = [
            {
                "name": "2026Feb25-111702-Rec25.hda",
                "createDate": "2026/02/25",
                "createTime": "11:17:02",
                "length": 5000000,
                "duration": 625.0,
                "version": 7,
                "mode": "room",
                "signature": "abc123",
            }
        ]
        state = {"downloads": {}}
        items = build_recording_status_items(recordings, state, tmp_path)
        assert len(items) == 1
        assert items[0]["status"] == "on_device"
        assert items[0]["downloaded"] is False
        assert items[0]["outputName"] == "2026Feb25-111702-Rec25.mp3"

    def test_downloaded_recording(self, tmp_path):
        mp3_path = tmp_path / "2026Feb25-111702-Rec25.mp3"
        mp3_path.write_bytes(b"\xff\xf3" * 100)
        recordings = [
            {
                "name": "2026Feb25-111702-Rec25.hda",
                "createDate": "2026/02/25",
                "createTime": "11:17:02",
                "length": 200,
                "duration": 0.025,
                "version": 7,
                "mode": "room",
                "signature": "abc123",
            }
        ]
        state = {
            "downloads": {
                "2026Feb25-111702-Rec25.hda": {
                    "downloaded": True,
                    "output_path": str(mp3_path),
                }
            }
        }
        items = build_recording_status_items(recordings, state, tmp_path)
        assert items[0]["status"] == "downloaded"
        assert items[0]["downloaded"] is True
        assert items[0]["localExists"] is True

    def test_marked_as_downloaded_no_local_file(self, tmp_path):
        recordings = [
            {
                "name": "2026Feb25-111702-Rec25.hda",
                "createDate": "2026/02/25",
                "createTime": "11:17:02",
                "length": 200,
                "duration": 0.025,
                "version": 7,
                "mode": "room",
                "signature": "abc123",
            }
        ]
        state = {
            "downloads": {
                "2026Feb25-111702-Rec25.hda": {
                    "downloaded": True,
                }
            }
        }
        items = build_recording_status_items(recordings, state, tmp_path)
        assert items[0]["status"] == "downloaded"
        assert items[0]["downloaded"] is True
        assert items[0]["localExists"] is False

    def test_product_id_filters_orphans(self, tmp_path):
        recordings = []
        state = {
            "downloads": {
                "h1-file.hda": {"downloaded": True, "product_id": 45068},
                "p1-file.hda": {"downloaded": True, "product_id": 45070},
                "old-file.hda": {"downloaded": True},  # no product_id
            }
        }
        items = build_recording_status_items(recordings, state, tmp_path, product_id=45068)
        names = [i["name"] for i in items]
        assert "h1-file.hda" in names
        assert "p1-file.hda" not in names
        assert "old-file.hda" not in names

    def test_failed_recording(self, tmp_path):
        recordings = [
            {
                "name": "fail.hda",
                "createDate": "2026/02/25",
                "createTime": "11:00:00",
                "length": 1000,
                "duration": 0.125,
                "version": 7,
                "mode": "room",
                "signature": "def456",
            }
        ]
        state = {
            "downloads": {
                "fail.hda": {
                    "downloaded": False,
                    "last_error": "timed out",
                }
            }
        }
        items = build_recording_status_items(recordings, state, tmp_path)
        assert items[0]["status"] == "failed"
        assert items[0]["lastError"] == "timed out"


# ---------------------------------------------------------------------------
# utc_now_iso
# ---------------------------------------------------------------------------
class TestValidateFilename:
    def test_valid_simple_name(self):
        assert validate_filename("recording.hda") == "recording.hda"

    def test_valid_with_dashes_and_dots(self):
        assert validate_filename("2026Feb25-111702-Rec25.hda") == "2026Feb25-111702-Rec25.hda"

    def test_valid_underscores(self):
        assert validate_filename("my_file_01.wav") == "my_file_01.wav"

    def test_path_traversal_rejected(self):
        with pytest.raises(HiDockProtocolError, match="unsafe filename"):
            validate_filename("../../etc/passwd")

    def test_path_traversal_backslash_rejected(self):
        with pytest.raises(HiDockProtocolError, match="unsafe filename"):
            validate_filename("..\\..\\windows\\system32\\config")

    def test_empty_string_rejected(self):
        with pytest.raises(HiDockProtocolError, match="unsafe filename"):
            validate_filename("")

    def test_spaces_rejected(self):
        with pytest.raises(HiDockProtocolError, match="unsafe filename"):
            validate_filename("my file.hda")

    def test_directory_component_rejected(self):
        with pytest.raises(HiDockProtocolError, match="unsafe filename"):
            validate_filename("subdir/safe.hda")


# ---------------------------------------------------------------------------
# parse_frame — oversized payload
# ---------------------------------------------------------------------------
class TestParseFramePayloadLimit:
    def test_oversized_payload_rejected(self):
        oversized_len = MAX_PAYLOAD_SIZE + 1
        frame = (
            HEADER
            + struct.pack(">H", 0x0005)
            + struct.pack(">I", 1)
            + struct.pack(">I", oversized_len)
        )
        with pytest.raises(HiDockProtocolError, match="payload too large"):
            parse_frame(frame)

    def test_max_payload_accepted(self):
        # Exactly MAX_PAYLOAD_SIZE should not raise (even if payload bytes are short)
        frame = (
            HEADER
            + struct.pack(">H", 0x0005)
            + struct.pack(">I", 1)
            + struct.pack(">I", MAX_PAYLOAD_SIZE)
            + b"\x00"  # truncated payload is fine — parse_frame just slices
        )
        cmd, req_id, payload = parse_frame(frame)
        assert cmd == 0x0005
        assert req_id == 1


# ---------------------------------------------------------------------------
# utc_now_iso
# ---------------------------------------------------------------------------
class TestUtcNowIso:
    def test_format(self):
        result = utc_now_iso()
        assert "T" in result
        assert result.endswith("+00:00")
        assert "." not in result  # no microseconds
