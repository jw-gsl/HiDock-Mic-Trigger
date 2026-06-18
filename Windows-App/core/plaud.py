"""Plaud cloud account/token store for the HiDock Windows app.

Mirrors the macOS PlaudAuthStore / PlaudSession (PlaudAuth.swift). The app owns
Plaud authentication; the extractor (Windows-Script/plaud_client.py) never
persists secrets — it reads tokens from environment variables this module
builds via :func:`plaud_env`.

The short-lived Plaud user token (``pld_ut``) is a JWT that expires within
hours and is refreshed by the extractor using the long-lived refresh token
(``pld_urt``). Because the refresh token may rotate, the extractor surfaces any
rotated tokens as ``refreshedTokens`` in its JSON output; the caller feeds those
to :func:`apply_refreshed_tokens` so the persisted copy stays current — mirroring
the macOS ``persistRefreshedPlaudTokens`` flow.

Accounts are persisted via QSettings (org "HiDock" / app "HiDockTools") under
the ``plaudAccounts`` key as a JSON list.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

SETTINGS_ORG = "HiDock"
SETTINGS_APP = "HiDockTools"
SETTINGS_KEY = "plaudAccounts"


@dataclass
class PlaudAccount:
    """A signed-in Plaud cloud account and its session tokens."""

    account_id: str
    email: str | None = None
    display_name: str = "Plaud"
    region: str = "us"
    access_token: str = ""
    refresh_token: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlaudAccount":
        return cls(
            account_id=d.get("account_id", ""),
            email=d.get("email"),
            display_name=d.get("display_name") or "Plaud",
            region=d.get("region") or "us",
            access_token=d.get("access_token", ""),
            refresh_token=d.get("refresh_token"),
        )


def _settings():
    """Return a QSettings handle for the Plaud store.

    Imported lazily so this module can be used (and unit-tested) without a Qt
    event loop, and so the import section stays dependency-light.
    """
    from PyQt6.QtCore import QSettings

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def load_accounts(settings=None) -> list[PlaudAccount]:
    """Load all persisted Plaud accounts from QSettings."""
    settings = settings if settings is not None else _settings()
    raw = settings.value(SETTINGS_KEY, "[]")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else []
        items = parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        items = []
    return [PlaudAccount.from_dict(d) for d in items if isinstance(d, dict)]


def _save_accounts(accounts: list[PlaudAccount], settings=None) -> None:
    settings = settings if settings is not None else _settings()
    settings.setValue(SETTINGS_KEY, json.dumps([a.to_dict() for a in accounts]))


def save_account(acct: PlaudAccount, settings=None) -> None:
    """Persist (insert or replace by account_id) a Plaud account."""
    settings = settings if settings is not None else _settings()
    accounts = [a for a in load_accounts(settings) if a.account_id != acct.account_id]
    accounts.append(acct)
    _save_accounts(accounts, settings)


def forget_account(account_id: str, settings=None) -> None:
    """Remove a persisted Plaud account by id."""
    settings = settings if settings is not None else _settings()
    accounts = [a for a in load_accounts(settings) if a.account_id != account_id]
    _save_accounts(accounts, settings)


def get_account(account_id: str, settings=None) -> PlaudAccount | None:
    """Return the persisted Plaud account with this id, or None."""
    for a in load_accounts(settings):
        if a.account_id == account_id:
            return a
    return None


def _env_account_key(account_id: str) -> str:
    """Match plaud_client._token_env_name: uppercase, non-alphanumerics -> '_'."""
    return re.sub(r"[^A-Za-z0-9]", "_", account_id or "default").upper()


def plaud_env(acct: PlaudAccount) -> dict[str, str]:
    """Build the environment-variable dict the extractor reads for this account.

    Callers run the extractor with these vars merged into os.environ, e.g.::

        env = {**os.environ, **plaud_env(acct)}
        run_extractor([...], env=env)

    Sets both the per-account names (``PLAUD_<ACCOUNT>_ACCESS_TOKEN`` etc., where
    ``<ACCOUNT>`` is the uppercased account id with non-alphanumerics replaced by
    ``_``) and the global ``PLAUD_*`` fallbacks plus ``PLAUD_ACCOUNT_ID`` so the
    extractor resolves the right account.
    """
    key = _env_account_key(acct.account_id)
    region = acct.region or "us"
    env: dict[str, str] = {
        f"PLAUD_{key}_ACCESS_TOKEN": acct.access_token or "",
        f"PLAUD_{key}_REGION": region,
        # Global fallbacks + selected account id.
        "PLAUD_ACCOUNT_ID": acct.account_id,
        "PLAUD_ACCESS_TOKEN": acct.access_token or "",
        "PLAUD_REGION": region,
    }
    if acct.refresh_token:
        env[f"PLAUD_{key}_REFRESH_TOKEN"] = acct.refresh_token
        env["PLAUD_REFRESH_TOKEN"] = acct.refresh_token
    return env


def apply_refreshed_tokens(
    account_id: str, refreshed: dict, settings=None
) -> PlaudAccount | None:
    """Persist tokens rotated by the extractor's ``refreshedTokens`` payload.

    ``refreshed`` is the dict the extractor emits (``accessToken``, optional
    ``refreshToken``, optional ``region``). Returns the updated account, or None
    if there is nothing new to persist (no account, empty/unchanged token).
    Mirrors the macOS PlaudSession.applyingRefreshedTokens.
    """
    if not isinstance(refreshed, dict):
        return None
    acct = get_account(account_id, settings)
    if acct is None:
        return None
    access = refreshed.get("accessToken")
    if not access or access == acct.access_token:
        return None
    acct.access_token = access
    if refreshed.get("refreshToken"):
        acct.refresh_token = refreshed["refreshToken"]
    if refreshed.get("region"):
        acct.region = refreshed["region"]
    save_account(acct, settings)
    return acct
