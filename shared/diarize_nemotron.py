"""Nemotron 3 Diarization — remote backend (HTTP sidecar on an NVIDIA host).

Nemotron 3 Diarization (nvidia/Nemotron-3-Diarization, OpenMDW 1.1) runs only
on NVIDIA GPUs (Ampere+; the DGX Spark is officially supported). The Mac has
no NVIDIA GPU, so this backend is a thin client: it POSTs the 16 kHz waveform
to the sidecar on ember over the tailnet and consumes the turns it gets back.

Everything after "who spoke when" is deliberately shared with the Sortformer
and pyannote backends — segment assignment, voice-library naming, collision
resolution, empty-speaker pruning — so a backend comparison in
`shared.diarisation_eval` measures the diarizer, not three post-processing
stacks.

Configuration (config.toml, via Models page or by hand):

    [nemotron]
    endpoint = "http://ember.tail17bf47.ts.net:8890/diarize"
    auth_token_file = "~/.hidock/nemotron-token"   # optional; sent as X-Auth-Token
    profile = "offline"   # offline | low | very_low | ultra_low

The sidecar itself lives outside this repo (`~/nemo-diar/sidecar.py` on
ember); its README documents the NeMo-from-source install.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


_DEFAULT_ENDPOINT = "http://ember.tail17bf47.ts.net:8890/diarize"
_TIMEOUT_S = 1800.0  # a two-hour meeting is still minutes on GPU, but bound it


def _nemotron_config() -> dict:
    try:
        from shared.config_store import get_config
        section = get_config().get_section("nemotron") or {}
    except Exception:  # noqa: BLE001 - config is optional; defaults are sane
        section = {}
    return section


def endpoint() -> str:
    """The /diarize URL. The app passes the host it was given on the Models
    page as HIDOCK_NEMOTRON_ENDPOINT (base URL); config.toml is the fallback
    for terminal runs."""
    base = os.environ.get("HIDOCK_NEMOTRON_ENDPOINT", "").strip().rstrip("/")
    if base:
        return base if base.endswith("/diarize") else base + "/diarize"
    return _nemotron_config().get("endpoint", _DEFAULT_ENDPOINT)


def _auth_token() -> str | None:
    """The service's X-Auth-Token. The app supplies it from the Keychain as
    HIDOCK_NEMOTRON_TOKEN; a token file named in config.toml is the fallback
    for terminal runs."""
    env = os.environ.get("HIDOCK_NEMOTRON_TOKEN", "").strip()
    if env:
        return env
    path = _nemotron_config().get("auth_token_file", "")
    if not path:
        return None
    p = Path(path).expanduser()
    try:
        value = p.read_text().strip()
        return value or None
    except OSError:
        return None


# A profile the sidecar is guaranteed to reject. It checks the token before
# the profile, so this answers "is the token right?" without running the model:
# 401 = token missing/wrong, 400 = token accepted.
_AUTH_PROBE_PROFILE = "__hidock_auth_check__"


def check_auth() -> tuple[bool, str]:
    """Whether the sidecar accepts our token. Never raises."""
    token = _auth_token()
    body = (f"profile={_AUTH_PROBE_PROFILE}").encode()
    req = urllib.request.Request(endpoint(), data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if token:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True, "token accepted"
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, ("no token saved yet" if not token
                           else "the Spark didn't accept this token — check it and save it again")
        if exc.code in (400, 422):
            return True, "token accepted"
        return False, f"unexpected HTTP {exc.code} from the auth check"
    except Exception as exc:  # noqa: BLE001
        return False, f"auth check failed: {exc}"


def available() -> tuple[bool, str]:
    """Check the sidecar is reachable, healthy and accepts our token.

    The token matters as much as reachability: /health needs none, so a
    reachable-only check would let Nemotron be selected and then fail with
    401 on the first real meeting. Never raises.
    """
    url = endpoint().replace("/diarize", "/health")
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        return False, f"can't reach the Spark at {url.rsplit('/health', 1)[0]} — is it on and on your network? ({exc})"
    if not body.get("ok"):
        return False, "the Spark answered but its diarization service isn't ready"
    authorised, why = check_auth()
    if not authorised:
        return False, why
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or url).split(".")[0]
    return True, f"Connected to {host} — {why}"


def _post_diarize(wav_bytes: bytes, profile: str) -> dict:
    """Multipart POST of a wav blob; returns the parsed sidecar response."""
    import uuid

    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; "
        "filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
    ).encode() + wav_bytes + b"\r\n"
    body += (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"profile\"\r\n\r\n"
        f"{profile}\r\n--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(endpoint(), data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    token = _auth_token()
    if token:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"nemotron sidecar HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"nemotron sidecar unreachable at {endpoint()} ({exc.reason}). "
            "Check the Mac is on the tailnet and the service is up on ember "
            "(systemctl --user status nemo-diar-sidecar)."
        ) from exc


def _turns_from_response(response: dict) -> list[tuple[float, float, str]]:
    turns: list[tuple[float, float, str]] = []
    for item in response.get("turns") or []:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            turns.append((start, end, str(item.get("speaker", "speaker_0"))))
    turns.sort(key=lambda t: t[0])
    return turns


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
    calendar_context=None,
    **_ignored,
) -> dict:
    """Same signature and return shape as the other backends.

    `n_speakers` is not forwarded: the sidecar's channel count is fixed at 8
    and labels are arrival-time ordered. A user count is applied *after* the
    response, mirroring Sortformer's post-hoc merge — a cluster never created
    remotely cannot be split locally, but over-merged labels can still be
    separated by voice embedding (reuse of Sortformer's helpers below).
    """
    from shared.audio_utils import load_audio
    from shared.diarize_sortformer import (
        _assign_speakers_segment_level,
        _assign_speakers_word_level,
        _prune_empty_speakers,
        _resolve_speaker_names,
        _split_labels_to_count,
    )
    from shared.diarize_lite import (
        _MAX_MERGED_SEGMENT_SECONDS,
        _anonymize_non_speech,
        _split_long_segments,
    )

    audio_path = Path(audio_path)
    audio = load_audio(audio_path, sr=16000)

    # Ship a WAV blob — 16 kHz mono f32 is the sidecar's native input, so
    # load_audio here also normalises sample rate once, client-side.
    import io
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, audio, 16000, format="WAV", subtype="FLOAT")

    profile = _nemotron_config().get("profile", "offline")
    t0 = time.time()
    response = _post_diarize(buf.getvalue(), profile)
    remote_s = response.get("inference_s", round(time.time() - t0, 1))

    raw_turns = _turns_from_response(response)
    if not raw_turns:
        print(
            "nemotron: no turns "
            f"(global max prob {response.get('global_max_prob')}); single-speaker result",
            file=sys.stderr,
        )
        segments_out = []
        for ws in whisper_segments:
            text = (ws.get("text") or "").strip()
            if not text:
                continue
            segments_out.append({
                "start": float(ws["start"]),
                "end": float(ws["end"]),
                "text": text,
                "speaker": "Speaker 1",
                "speaker_id": 0,
            })
        return {
            "version": 1,
            "audio_file": str(audio_path),
            "segments": segments_out,
            "speaker_names": {"0": "Speaker 1"},
            "speaker_meta": {"0": {"source": "generic", "confidence": None, "verified": False}},
            "speaker_embeddings": {},
            "backend": "nemotron",
        }

    # Merge consecutive same-speaker turns (sidecar post-processing can split
    # a turn at chunk boundaries).
    raw_turns.sort(key=lambda t: t[0])
    merged: list[list] = []
    for s, e, spk in raw_turns:
        if merged and merged[-1][2] == spk and s - merged[-1][1] < 1.0:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, spk])
    turns = [(m[0], m[1], m[2]) for m in merged]

    if n_speakers is not None:
        turns, _ = _split_labels_to_count(audio, turns, n_speakers)

    # Normalise raw labels ("speaker_2") to "Speaker N" by first appearance,
    # matching the other backends.
    label_map: dict[str, str] = {}
    for _, _, spk in turns:
        if spk not in label_map:
            label_map[spk] = f"Speaker {len(label_map) + 1}"
    renamed = [(s, e, label_map[spk]) for s, e, spk in turns]
    internal_labels = list(label_map.values())

    speaker_info, _naming_model = _resolve_speaker_names(
        audio, renamed, internal_labels, sr=16000,
        allowed_names=getattr(calendar_context, "candidate_names", None),
    )

    has_words = any((segment.get("words") or []) for segment in whisper_segments)
    raw_segments = (
        _assign_speakers_word_level(whisper_segments, renamed)
        if has_words
        else _assign_speakers_segment_level(whisper_segments, renamed)
    )

    label_to_id = {label: index for index, label in enumerate(internal_labels)}
    speaker_names = {
        str(spk_id): (speaker_info.get(label) or {}).get("name", label)
        for label, spk_id in label_to_id.items()
    }
    speaker_meta: dict[str, dict] = {}
    speaker_embeddings: dict[str, list] = {}
    for label, spk_id in label_to_id.items():
        info = speaker_info.get(label) or {}
        speaker_meta[str(spk_id)] = {
            "source": info.get("source", "generic"),
            "confidence": info.get("confidence"),
            "verified": False,
        }
        if info.get("embedding") is not None:
            speaker_embeddings[str(spk_id)] = info["embedding"]

    try:
        from shared.speaker_meta import resolve_name_collisions

        resolve_name_collisions(speaker_names, speaker_meta)
    except Exception as exc:  # noqa: BLE001 - naming must not break diarization
        print(f"nemotron: name-collision resolve skipped ({exc})", file=sys.stderr)

    for segment in raw_segments:
        label = segment.get("speaker") or internal_labels[0]
        spk_id = label_to_id.get(label, 0)
        segment["speaker_id"] = spk_id
        segment["speaker"] = speaker_names.get(str(spk_id), label)

    raw_segments = _anonymize_non_speech(raw_segments)

    segments_out: list[dict] = []
    for segment in raw_segments:
        if not segment.get("text"):
            continue
        if (
            segments_out
            and segments_out[-1]["speaker_id"] == segment["speaker_id"]
            and segment["start"] - segments_out[-1]["end"] < 1.0
        ):
            previous = segments_out[-1]
            previous["end"] = max(previous["end"], segment["end"])
            previous["text"] = f"{previous['text']} {segment['text']}".strip()
            if segment.get("words"):
                previous.setdefault("words", []).extend(segment["words"])
        else:
            segments_out.append(dict(segment))
    segments_out = _split_long_segments(
        segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS
    )

    speaker_names, speaker_meta, speaker_embeddings = _prune_empty_speakers(
        speaker_names, speaker_meta, speaker_embeddings, segments_out
    )

    print(
        f"nemotron: {len(turns)} turns, {len(speaker_names)} speakers, "
        f"{len(audio)/16000:.0f}s audio, remote {remote_s}s, "
        f"{len(segments_out)} output segments",
        file=sys.stderr,
    )
    return {
        "version": 1,
        "audio_file": str(audio_path),
        "segments": segments_out,
        "speaker_names": speaker_names,
        "speaker_meta": speaker_meta,
        "speaker_embeddings": speaker_embeddings,
        "speaker_embedding_model": _naming_model,
        "backend": "nemotron",
        "speaker_count_strategy": "manual" if n_speakers else "arrival-order",
        "remote_inference_s": remote_s,
        "remote_profile": profile,
    }
