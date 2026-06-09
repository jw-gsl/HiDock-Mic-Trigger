"""Plaud cloud recording backend for the HiDock desktop app.

This module deliberately mirrors extractor.py's JSON contracts so Plaud can be
treated as another paired device by the Swift app. Authentication is owned by
the app and passed to this subprocess via environment variables; this file does
not persist Plaud secrets.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any


API_US = "https://api.plaud.ai"
API_EU = "https://api-euc1.plaud.ai"
AUDIO_EXTENSIONS = {".mp3", ".opus"}


class PlaudError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _base_url(region: str) -> str:
    return API_EU if region == "eu" else API_US


def _token_env_name(account_id: str, suffix: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]", "_", account_id or "default").upper()
    return f"PLAUD_{clean}_{suffix}"


def _get_auth(account_id: str | None = None) -> tuple[str, str | None, str]:
    account = account_id or os.environ.get("PLAUD_ACCOUNT_ID", "default")
    access = (
        os.environ.get(_token_env_name(account, "ACCESS_TOKEN"))
        or os.environ.get("PLAUD_ACCESS_TOKEN")
    )
    refresh = (
        os.environ.get(_token_env_name(account, "REFRESH_TOKEN"))
        or os.environ.get("PLAUD_REFRESH_TOKEN")
    )
    region = (
        os.environ.get(_token_env_name(account, "REGION"))
        or os.environ.get("PLAUD_REGION")
        or "us"
    )
    if not access:
        raise PlaudError("Plaud is not signed in")
    return access, refresh, region


def _request_json(
    path: str,
    *,
    token: str,
    region: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{_base_url(region)}{path}"
    req_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "app-platform": "web",
        "Cookie": f"pld_ut={token}",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        detail = f": {body[:500]}" if body else ""
        raise PlaudError(f"Plaud API error: HTTP {exc.code}{detail}") from exc
    except urllib.error.URLError as exc:
        raise PlaudError(f"Plaud network error: {exc.reason}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PlaudError("Invalid Plaud API response") from exc

    if data.get("status") == -302:
        domain = (((data.get("data") or {}).get("domains") or {}).get("api") or "")
        if "euc1" in domain and region != "eu":
            return _request_json(path, token=token, region="eu", method=method, body=body, headers=headers)
        if domain and "euc1" not in domain and region != "us":
            return _request_json(path, token=token, region="us", method=method, body=body, headers=headers)
    return data


def refresh_user_token(refresh_token: str, region: str) -> tuple[str, str | None]:
    req = urllib.request.Request(
        f"{_base_url(region)}/auth/refresh-user-token",
        data=b"",
        method="POST",
        headers={
            "app-platform": "web",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Cookie": f"pld_urt={refresh_token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            cookies = res.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as exc:
        raise PlaudError(f"Plaud session refresh failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise PlaudError(f"Plaud session refresh failed: {exc.reason}") from exc

    user_token = None
    new_refresh = None
    for header in cookies:
        cookie = SimpleCookie()
        cookie.load(header)
        if "pld_ut" in cookie and cookie["pld_ut"].value:
            user_token = cookie["pld_ut"].value
        if "pld_urt" in cookie and cookie["pld_urt"].value:
            new_refresh = cookie["pld_urt"].value
    if not user_token:
        raise PlaudError("Plaud session refresh did not return a user token")
    return user_token, new_refresh


def list_recordings(account_id: str | None = None) -> list[dict[str, Any]]:
    token, _refresh, region = _get_auth(account_id)
    data = _request_json("/file/simple/web", token=token, region=region)
    raw_items = data.get("data_file_list") or data.get("data") or []
    if not isinstance(raw_items, list):
        raw_items = []
    return [
        item for item in raw_items
        if isinstance(item, dict) and not bool(item.get("is_trash"))
    ]


def _recording_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("file_id") or "")


def _recording_name(item: dict[str, Any]) -> str:
    rid = _recording_id(item)
    return str(item.get("filename") or item.get("file_name") or rid or "Plaud Recording")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:"*?<>|]+', "-", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:100] or "Plaud Recording"


def _coerce_timestamp_seconds(value: Any) -> float:
    raw = str(value or "").strip()
    if raw.isdigit() and len(raw) in (12, 14) and raw.startswith("20"):
        fmt = "%Y%m%d%H%M%S" if len(raw) == 14 else "%Y%m%d%H%M"
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if ts <= 0:
        return 0.0
    # Plaud has used epoch seconds, milliseconds, and web payloads with
    # larger precision. Normalize by magnitude rather than trusting the
    # field name so rows sort correctly in the desktop table.
    if ts > 10_000_000_000_000:
        return ts / 1_000_000.0
    if ts > 10_000_000_000:
        return ts / 1_000.0
    return ts


def _item_timestamp(item: dict[str, Any]) -> float:
    for key in (
        "start_time",
        "created_at",
        "create_time",
        "ctime",
        "mtime",
        "updated_at",
    ):
        ts = _coerce_timestamp_seconds(item.get(key))
        if ts > 0:
            return ts
    return 0.0


def _date_parts(timestamp: Any) -> tuple[str, str]:
    ts = _coerce_timestamp_seconds(timestamp)
    if ts <= 0:
        return "", ""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y/%m/%d"), dt.strftime("%H:%M:%S")


def _folder_date(timestamp: Any) -> str:
    ts = _coerce_timestamp_seconds(timestamp)
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _duration_milliseconds(duration: int | float) -> float:
    try:
        value = float(duration or 0)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    # The standalone Plaud app treats Plaud's raw duration as milliseconds.
    # Keep a microsecond guard for unusually large API values.
    if value > 10_000_000:
        return value / 1_000_000.0
    return value / 1_000.0


def _item_duration(item: dict[str, Any]) -> float:
    for key in (
        "duration",
        "audio_duration",
        "duration_ms",
        "audio_duration_ms",
        "record_duration",
        "recording_duration",
        "duration_seconds",
        "audio_duration_seconds",
    ):
        raw = item.get(key) or 0
        if key.endswith("_seconds"):
            try:
                duration = float(raw)
            except (TypeError, ValueError):
                duration = 0.0
        else:
            duration = _duration_milliseconds(raw)
        if duration > 0:
            return duration
    return 0.0


def _raw_duration_for_transcript(item: dict[str, Any]) -> float:
    return _item_duration(item)


def _local_audio_duration(path: Path) -> tuple[float, bool]:
    if not path.exists():
        return 0.0, True
    try:
        from mutagen import File as MutagenFile
        mf = MutagenFile(str(path))
        if mf and mf.info and getattr(mf.info, "length", 0) > 0:
            return float(mf.info.length), False
    except ImportError as exc:
        print(
            f"[plaud] WARN: mutagen unavailable; duration for {path.name} "
            f"falling back to size estimate ({exc})",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(f"[plaud] WARN: mutagen read failed for {path}: {exc}", file=sys.stderr, flush=True)
    # Conservative fallback for Plaud MP3s when metadata is unavailable.
    return max(path.stat().st_size / 16_000.0, 0.0), True


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024.0 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(num_bytes)} B"


def _output_path_for(output_dir: Path, item: dict[str, Any], ext: str = ".mp3") -> Path:
    date = _folder_date(_item_timestamp(item))
    folder = output_dir / "Plaud"
    if date:
        folder = folder / date
    return folder / (_safe_filename(_recording_name(item)) + ext)


def _state_key(account_id: str, item_or_id: dict[str, Any] | str) -> str:
    rid = _recording_id(item_or_id) if isinstance(item_or_id, dict) else str(item_or_id)
    return f"plaud:{account_id}:{rid}"


def _catalog_key(account_id: str) -> str:
    return f"plaud:{account_id}"


def _matching_existing_file(base_path: Path) -> Path | None:
    candidates = [base_path, base_path.with_suffix(".opus"), base_path.with_suffix(".mp3")]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _status_recordings_from_items(
    output_dir: Path,
    state: dict[str, Any],
    account_id: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    downloads = state.get("downloads", {})
    recordings: list[dict[str, Any]] = []
    for item in items:
        rid = _recording_id(item)
        if not rid:
            continue
        state_key = _state_key(account_id, item)
        stored = downloads.get(state_key, {})
        base_path = Path(stored["output_path"]) if stored.get("output_path") else _output_path_for(output_dir, item)
        existing = _matching_existing_file(base_path)
        length = int(stored.get("length") or item.get("size") or item.get("file_size") or 0)
        date, create_time = _date_parts(_item_timestamp(item))
        downloaded = bool(stored.get("downloaded")) or existing is not None
        status = "downloaded" if downloaded else "on_device"
        if stored.get("last_error") and not downloaded:
            status = "failed"
        duration = _item_duration(item)
        duration_estimated = False
        if existing is not None:
            length = existing.stat().st_size
            duration, duration_estimated = _local_audio_duration(existing)
        elif duration <= 0:
            duration_estimated = True

        recordings.append({
            "name": rid,
            "createDate": date,
            "createTime": create_time,
            "length": length,
            "duration": duration,
            "durationEstimated": duration_estimated,
            "version": 0,
            "mode": "plaud",
            "signature": rid,
            "outputPath": str(existing or base_path),
            "outputName": Path(existing or base_path).name,
            "downloaded": downloaded,
            "localExists": existing is not None,
            "downloadedAt": stored.get("downloaded_at"),
            "lastError": stored.get("last_error"),
            "status": status,
            "humanLength": _human_size(length),
            "trimmed": bool(stored.get("trimmed")),
            "removed": bool(stored.get("removed")),
        })

    recordings.sort(key=lambda r: f'{r["createDate"]} {r["createTime"]}', reverse=True)
    return recordings


def _apply_storage(payload: dict[str, Any], recordings: list[dict[str, Any]]) -> None:
    payload["recordings"] = recordings
    payload["storage"] = {
        "totalFiles": len(recordings),
        "returnedFiles": len(recordings),
        "totalBytesReturned": sum(int(r.get("length") or 0) for r in recordings),
        "truncated": False,
    }


def status_payload(
    output_dir: Path,
    state: dict[str, Any],
    *,
    account_id: str,
) -> dict[str, Any]:
    payload = {
        "connected": False,
        "outputDir": str(output_dir),
        "statePath": "",
        "configPath": "",
        "recordings": [],
    }
    cache_key = _catalog_key(account_id)
    try:
        items = list_recordings(account_id)
    except Exception as exc:
        payload["error"] = str(exc)
        cached_items = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
        if cached_items:
            _apply_storage(payload, _status_recordings_from_items(output_dir, state, account_id, cached_items))
            payload["cached"] = True
        return payload

    catalogs = state.setdefault("catalogs", {})
    catalogs[cache_key] = {
        "recordings": items,
        "updated_at": _now_iso(),
        "source": "plaud",
        "account_id": account_id,
    }
    payload["connected"] = True
    _apply_storage(payload, _status_recordings_from_items(output_dir, state, account_id, items))
    return payload


def _get_mp3_url(recording_id: str, *, token: str, region: str) -> str | None:
    data = _request_json(f"/file/temp-url/{recording_id}?is_opus=false", token=token, region=region)
    url = (
        data.get("url")
        or ((data.get("data") or {}).get("url") if isinstance(data.get("data"), dict) else None)
        or (data.get("data") if isinstance(data.get("data"), str) else None)
        or data.get("temp_url")
    )
    return str(url) if url else None


def _download_url_to_path(url: str, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url)
    written = 0
    with urllib.request.urlopen(req, timeout=60) as res, out_path.open("wb") as fh:
        total = int(res.headers.get("Content-Length") or 0)
        while True:
            chunk = res.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            written += len(chunk)
            pct = int(written * 100 / total) if total else 0
            print(f"PROGRESS:{written}:{total}:{pct}", file=sys.stderr, flush=True)
    return written


def _download_api_to_path(recording_id: str, *, token: str, region: str, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        f"{_base_url(region)}/file/download/{recording_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Cookie": f"pld_ut={token}",
            "app-platform": "web",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    )
    written = 0
    with urllib.request.urlopen(req, timeout=60) as res, out_path.open("wb") as fh:
        total = int(res.headers.get("Content-Length") or 0)
        while True:
            chunk = res.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            written += len(chunk)
            pct = int(written * 100 / total) if total else 0
            print(f"PROGRESS:{written}:{total}:{pct}", file=sys.stderr, flush=True)
    return written


def _get_detail(recording_id: str, *, token: str, region: str) -> dict[str, Any]:
    data = _request_json(f"/file/detail/{recording_id}", token=token, region=region)
    return data.get("data") if isinstance(data.get("data"), dict) else data


def _extract_transcript(detail: dict[str, Any]) -> str:
    items = detail.get("pre_download_content_list") or []
    longest = ""
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                content = item.get("data_content")
                if isinstance(content, str) and len(content) > len(longest):
                    longest = content
    return longest


def download_one(
    recording_id: str,
    output_dir: Path,
    state: dict[str, Any],
    *,
    account_id: str,
    include_transcript: bool = True,
) -> dict[str, Any]:
    token, _refresh, region = _get_auth(account_id)
    items = list_recordings(account_id)
    item = next((r for r in items if _recording_id(r) == recording_id), None)
    if item is None:
        raise PlaudError(f"Plaud recording not found: {recording_id}")

    base_path = _output_path_for(output_dir, item)
    mp3_url = _get_mp3_url(recording_id, token=token, region=region)
    if mp3_url:
        final_path = base_path.with_suffix(".mp3")
        written = _download_url_to_path(mp3_url, final_path)
    else:
        final_path = base_path.with_suffix(".opus")
        written = _download_api_to_path(recording_id, token=token, region=region, out_path=final_path)

    if include_transcript:
        try:
            detail = _get_detail(recording_id, token=token, region=region)
            transcript = _extract_transcript(detail)
            if transcript:
                transcript_date, transcript_time = _date_parts(_item_timestamp(item))
                info = (
                    f"Title: {_recording_name(item)}\n"
                    f"Date: {transcript_date} {transcript_time}".rstrip() + "\n"
                    f"Duration: {max(1, int(_raw_duration_for_transcript(item) // 60))} min\n"
                    "Source: Plaud\n\n--- Transcript ---\n\n"
                    f"{transcript}"
                )
                final_path.with_suffix(".txt").write_text(info, encoding="utf-8")
        except Exception as exc:
            print(f"[plaud] WARN: transcript fetch failed for {recording_id}: {exc}", file=sys.stderr, flush=True)

    downloads = state.setdefault("downloads", {})
    state_key = _state_key(account_id, recording_id)
    downloads[state_key] = {
        **downloads.get(state_key, {}),
        "downloaded": written > 0,
        "downloaded_at": _now_iso(),
        "updated_at": _now_iso(),
        "output_path": str(final_path),
        "length": written,
        "last_error": None,
        "signature": recording_id,
        "source": "plaud",
        "account_id": account_id,
    }
    return {
        "filename": recording_id,
        "written": written,
        "expectedLength": written,
        "outputPath": str(final_path),
        "downloaded": written > 0,
    }


def download_new(output_dir: Path, state: dict[str, Any], *, account_id: str) -> dict[str, Any]:
    status = status_payload(output_dir, state, account_id=account_id)
    if not status["connected"]:
        return {
            "connected": False,
            "outputDir": status["outputDir"],
            "downloaded": [],
            "skipped": [],
            "error": status.get("error"),
        }

    downloaded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in status["recordings"]:
        if item["downloaded"]:
            skipped.append({"filename": item["name"], "reason": "already_downloaded"})
            continue
        if item.get("removed"):
            skipped.append({"filename": item["name"], "reason": "user_removed"})
            continue
        print(f"FILE_START:{item['name']}", file=sys.stderr, flush=True)
        try:
            downloaded.append(download_one(item["name"], output_dir, state, account_id=account_id))
        finally:
            print(f"FILE_DONE:{item['name']}", file=sys.stderr, flush=True)

    return {
        "connected": True,
        "outputDir": status["outputDir"],
        "downloaded": downloaded,
        "skipped": skipped,
    }
