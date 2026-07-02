"""Plaud cloud recording backend for the HiDock desktop app.

This module deliberately mirrors extractor.py's JSON contracts so Plaud can be
treated as another paired device by the Swift app. Authentication is owned by
the app and passed to this subprocess via environment variables; this file does
not persist Plaud secrets.

The short-lived Plaud user token (`pld_ut`) is a JWT that expires within hours.
When it is near expiry we transparently refresh it with the long-lived refresh
token (`pld_urt`), mirroring the plaud-sync app. Because the refresh token may
rotate, any refreshed tokens are surfaced via `pop_refreshed_tokens()` so the
app can persist them — this file still never writes secrets to disk itself.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any


API_US = "https://api.plaud.ai"
API_EU = "https://api-euc1.plaud.ai"
API_APAC = "https://api-apse1.plaud.ai"
AUDIO_EXTENSIONS = {".mp3", ".opus"}

# Surfaced when the session is unusable (expired access token + dead/absent
# refresh token, or an HTTP 401). Must contain "not signed in" — the desktop
# app maps that phrase to its signed-out state and prompts re-authentication.
SIGNED_OUT_MESSAGE = "Plaud is not signed in: your session expired, please sign in again"


class PlaudError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_valid_plaud_api_url(url: str) -> bool:
    """HTTPS + ``*.plaud.ai`` host. Guards against trusting a malformed or
    hostile redirect host from a ``-302`` response."""
    if not url.startswith("https://"):
        return False
    authority = url[len("https://"):].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0]  # drop userinfo + port
    return host == "plaud.ai" or host.endswith(".plaud.ai")


def _base_url(region: str) -> str:
    # A region redirect may have handed us a full API base; trust it if it's a
    # valid Plaud host, so a region Plaud adds later works without a code change.
    if region.startswith("http"):
        return region.rstrip("/") if _is_valid_plaud_api_url(region) else API_US
    if region == "eu":
        return API_EU
    if region in ("apac", "apse1"):
        return API_APAC
    return API_US


def _region_from_redirect(api: str) -> str | None:
    """Resolve a ``-302`` ``data.domains.api`` value to the region string we
    use: a key for the three known hosts, the full validated URL for any other
    Plaud region, or ``None`` if it isn't a valid Plaud host (ignore it)."""
    if not _is_valid_plaud_api_url(api):
        return None
    api = api.rstrip("/")
    if "euc1" in api:
        return "eu"
    if "apse1" in api:
        return "apac"
    if "api.plaud.ai" in api:
        return "us"
    return api


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


# Refresh the user token once it is within this many seconds of expiry,
# matching plaud-sync's TOKEN_REFRESH_BUFFER_MS (5 minutes).
TOKEN_REFRESH_BUFFER_S = 5 * 60

# Tokens refreshed during this process, keyed by account id:
# {account: (access_token, refresh_token, region)}. Populated by
# `_ensure_fresh_token` and drained by `pop_refreshed_tokens` so the app can
# persist the (possibly rotated) tokens. Cached per process so we refresh at
# most once per account — a second refresh with the already-rotated refresh
# token would fail.
_REFRESHED: dict[str, tuple[str, str | None, str]] = {}


def _account_key(account_id: str | None) -> str:
    return account_id or os.environ.get("PLAUD_ACCOUNT_ID", "default")


def _jwt_exp(token: str) -> int | None:
    """Return the `exp` (unix seconds) claim from a JWT, or None if it can't
    be decoded (opaque token / malformed)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64 padding
    try:
        raw = base64.urlsafe_b64decode(payload)
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    exp = data.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def _is_expiring_soon(token: str) -> bool:
    exp = _jwt_exp(token)
    if exp is None:
        # Can't read expiry — don't force a refresh on an opaque token.
        return False
    return time.time() + TOKEN_REFRESH_BUFFER_S > exp


def _is_expired(token: str) -> bool:
    """True only if the JWT `exp` is already in the past (no buffer). Opaque
    tokens return False — we can't prove a token we can't read is dead."""
    exp = _jwt_exp(token)
    return exp is not None and time.time() >= exp


def _ensure_fresh_token(account_id: str | None = None) -> tuple[str, str]:
    """Return a (access_token, region) good for API calls, refreshing the
    user token first if it is near expiry and a refresh token is available.

    Refreshing is best-effort: if the refresh call fails we fall back to the
    existing token rather than blocking the user (same as plaud-sync's
    get_token()). Refreshed tokens are stashed in `_REFRESHED` for the app to
    persist via `pop_refreshed_tokens`."""
    account = _account_key(account_id)
    cached = _REFRESHED.get(account)
    if cached:
        access, _refresh, region = cached
        return access, region

    access, refresh, region = _get_auth(account_id)
    if _is_expiring_soon(access):
        if refresh:
            try:
                new_access, new_refresh = refresh_user_token(refresh, region)
            except PlaudError as exc:
                print(f"Plaud token refresh failed: {exc}", file=sys.stderr)
            else:
                _REFRESHED[account] = (new_access, new_refresh or refresh, region)
                return new_access, region
        # Refresh unavailable or failed. If the existing token is actually
        # expired, the session is dead — surface a signed-out error rather than
        # sending a dead token, which Plaud answers with an empty 200 that
        # otherwise masquerades as "connected, 0 recordings".
        if _is_expired(access):
            raise PlaudError(SIGNED_OUT_MESSAGE)
    return access, region


def pop_refreshed_tokens(account_id: str | None = None) -> dict[str, str] | None:
    """Return and clear any tokens refreshed during this process for
    `account_id`. The extractor includes these in its JSON output so the app
    can persist the rotated tokens; otherwise the next run reuses the stale
    env token and the refresh token rotation is lost."""
    rec = _REFRESHED.pop(_account_key(account_id), None)
    if not rec:
        return None
    access, refresh, region = rec
    out = {"accessToken": access, "region": region}
    if refresh:
        out["refreshToken"] = refresh
    return out


def _request_json(
    path: str,
    *,
    token: str,
    region: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    _redirects: int = 0,
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
        body_text = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 401:
            # Auth rejected — treat as signed out so the app prompts re-login
            # instead of surfacing a raw HTTP 401.
            raise PlaudError(SIGNED_OUT_MESSAGE) from exc
        detail = f": {body_text[:500]}" if body_text else ""
        raise PlaudError(f"Plaud API error: HTTP {exc.code}{detail}") from exc
    except urllib.error.URLError as exc:
        raise PlaudError(f"Plaud network error: {exc.reason}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PlaudError("Invalid Plaud API response") from exc

    if data.get("status") == -302 and _redirects < 3:
        domain = (((data.get("data") or {}).get("domains") or {}).get("api") or "")
        # Trust the host Plaud points us at (validated to a plaud.ai host), and
        # only retry if it actually changes the base URL — so a redirect that
        # resolves to the same host can't loop forever.
        target = _region_from_redirect(domain)
        if target and _base_url(target) != _base_url(region):
            return _request_json(
                path,
                token=token,
                region=target,
                method=method,
                body=body,
                headers=headers,
                _redirects=_redirects + 1,
            )
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
    token, region = _ensure_fresh_token(account_id)
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
    # Render in the machine's LOCAL timezone, not UTC. Plaud names the file in
    # local wall-clock (e.g. "2026-06-13 04-04-24" in BST), but createTime was
    # rendered in UTC — so the "Created" column showed an hour earlier than the
    # filename. astimezone() (no arg) converts the UTC instant to local so the
    # two agree. On a UTC host (e.g. CI) this is a no-op.
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y/%m/%d"), dt.strftime("%H:%M:%S")


def _folder_date(timestamp: Any) -> str:
    ts = _coerce_timestamp_seconds(timestamp)
    if ts <= 0:
        return ""
    # Local timezone, matching _date_parts and the Plaud filename.
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


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


def _recordings_from_downloads(
    state: dict[str, Any], account_id: str
) -> list[dict[str, Any]]:
    """Build recording rows from locally-downloaded files recorded in `state`,
    so saved Plaud recordings still show when the cloud is unavailable / signed
    out. Only files that actually exist on disk are included."""
    out: list[dict[str, Any]] = []
    prefix = f"plaud:{account_id}:"
    for key, stored in (state.get("downloads") or {}).items():
        if not key.startswith(prefix):
            continue
        if stored.get("source") and stored.get("source") != "plaud":
            continue
        base = Path(stored["output_path"]) if stored.get("output_path") else None
        existing = _matching_existing_file(base) if base else None
        if existing is None:
            continue
        rid = stored.get("signature") or key[len(prefix):]
        length = existing.stat().st_size
        duration, duration_estimated = _local_audio_duration(existing)
        # Prefer the YYYY-MM-DD parent folder for the date; fall back to mtime.
        date, create_time = "", ""
        try:
            dt = datetime.strptime(existing.parent.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            date = dt.strftime("%Y/%m/%d")
        except ValueError:
            dt = datetime.fromtimestamp(existing.stat().st_mtime, tz=timezone.utc)
            date, create_time = dt.strftime("%Y/%m/%d"), dt.strftime("%H:%M:%S")
        out.append({
            "name": rid,
            "createDate": date,
            "createTime": create_time,
            "length": length,
            "duration": duration,
            "durationEstimated": duration_estimated,
            "version": 0,
            "mode": "plaud",
            "signature": rid,
            "outputPath": str(existing),
            "outputName": existing.name,
            "downloaded": True,
            "localExists": True,
            "downloadedAt": stored.get("downloaded_at"),
            "lastError": None,
            "status": "downloaded",
            "humanLength": _human_size(length),
            "trimmed": bool(stored.get("trimmed")),
            "removed": bool(stored.get("removed")),
        })
    out.sort(key=lambda r: f'{r["createDate"]} {r["createTime"]}', reverse=True)
    return out


def cached_status_payload(
    output_dir: Path,
    state: dict[str, Any],
    *,
    account_id: str,
) -> dict[str, Any]:
    """Network-free status for painting the table instantly on launch: the
    cached catalog from state.json if present, else locally-downloaded files.
    Never touches the cloud, so it returns in milliseconds and works offline.
    `connected` stays False — the live plaud-status probe sets that later."""
    payload: dict[str, Any] = {
        "connected": False,
        "outputDir": str(output_dir),
        "statePath": "",
        "configPath": "",
        "recordings": [],
        "cached": True,
    }
    cache_key = _catalog_key(account_id)
    cached_items = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
    if cached_items:
        _apply_storage(payload, _status_recordings_from_items(output_dir, state, account_id, cached_items))
    else:
        local = _recordings_from_downloads(state, account_id)
        if local:
            _apply_storage(payload, local)
    return payload


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
        else:
            # No usable catalog (never populated, or a dead session left it
            # empty). Still surface locally-downloaded recordings so saved files
            # don't vanish from the list while offline / signed out.
            local = _recordings_from_downloads(state, account_id)
            if local:
                _apply_storage(payload, local)
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


def _stream_response_to_path(res, out_path: Path) -> int:
    """Stream an HTTP response body to out_path atomically.

    Writes to a .downloading temp file and renames onto the final path only
    after the full body arrived. A partial file at the final path would be
    treated as a completed download forever by the existence checks in
    status/download-new, so on any failure the temp file is removed and the
    final path is left untouched.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".downloading")
    written = 0
    total = int(res.headers.get("Content-Length") or 0)
    try:
        with tmp_path.open("wb") as fh:
            while True:
                chunk = res.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
                pct = int(written * 100 / total) if total else 0
                print(f"PROGRESS:{written}:{total}:{pct}", file=sys.stderr, flush=True)
        if total and written != total:
            # Early EOF from the server reads as a clean end-of-stream —
            # without this check a short body would be renamed into place
            # and marked downloaded.
            raise PlaudError(
                f"download incomplete: got {written} of {total} bytes for {out_path.name}"
            )
        os.replace(str(tmp_path), str(out_path))
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return written


def _download_url_to_path(url: str, out_path: Path) -> int:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as res:
        return _stream_response_to_path(res, out_path)


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
    with urllib.request.urlopen(req, timeout=60) as res:
        return _stream_response_to_path(res, out_path)


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
    token, region = _ensure_fresh_token(account_id)
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
    errors: list[dict[str, Any]] = []
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
        except Exception as exc:
            # One failed recording must not abort the batch: the remaining
            # files still get their chance, the CLI still emits JSON (the
            # desktop app parses stdout), and the failure is recorded so
            # status can surface it.
            message = str(exc)
            print(f"[plaud] ERROR: download failed for {item['name']}: {message}", file=sys.stderr, flush=True)
            errors.append({"filename": item["name"], "error": message})
            downloads = state.setdefault("downloads", {})
            state_key = _state_key(account_id, item["name"])
            downloads[state_key] = {
                **downloads.get(state_key, {}),
                "downloaded": False,
                "last_error": message,
                "updated_at": _now_iso(),
                "source": "plaud",
                "account_id": account_id,
            }
        finally:
            print(f"FILE_DONE:{item['name']}", file=sys.stderr, flush=True)

    return {
        "connected": True,
        "outputDir": status["outputDir"],
        "downloaded": downloaded,
        "errors": errors,
        "skipped": skipped,
    }
