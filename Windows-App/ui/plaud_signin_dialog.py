"""Plaud sign-in dialog.

Mirrors the macOS PlaudLoginWindowController (PlaudAuth.swift): preferably a
QWebEngineView that loads the Plaud web sign-in (Google/Apple SSO supported by
the web page) and polls the profile's cookie store for the ``pld_ut`` (access)
and ``pld_urt`` (refresh) cookies. On capture it resolves the account — decoding
the access-token JWT for an email / account id where possible — and accepts.

QtWebEngine (PyQt6-WebEngine) is an optional dependency. If it is not installed
this module STILL imports: it falls back to a manual token-paste form where the
user pastes the ``pld_ut`` / ``pld_urt`` cookie values (copied from the
web.plaud.ai DevTools), picks a region, and optionally enters their email.

Use :meth:`PlaudSignInDialog.result_account` to retrieve the captured
``core.plaud.PlaudAccount`` (or None if cancelled).
"""
from __future__ import annotations

import base64
import json
import re

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from core.plaud import PlaudAccount

# Optional WebEngine backend — guarded so the module imports without it.
try:
    from PyQt6.QtWebEngineCore import QWebEngineProfile  # noqa: F401
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    _HAS_WEBENGINE = True
except Exception:  # pragma: no cover - exercised only when WebEngine present
    _HAS_WEBENGINE = False

# The Plaud web sign-in entry point (matches PlaudAuth.swift / plaud-sync).
PLAUD_SIGNIN_URL = "https://web.plaud.ai"

# Region options offered in the manual form (value, label).
REGIONS = [
    ("us", "US / Global (api.plaud.ai)"),
    ("eu", "Europe (api-euc1.plaud.ai)"),
    ("apac", "Asia-Pacific (api-apse1.plaud.ai)"),
]


def _b64url_decode(segment: str) -> bytes:
    segment += "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment)


def _jwt_claims(token: str) -> dict:
    """Best-effort decode of a JWT payload; {} if it can't be read."""
    parts = (token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        return json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception:
        return {}


def _account_id_from(email: str | None, access_token: str) -> str:
    """Mirror PlaudAuth.swift accountId(): slugified email, else token hash."""
    if email:
        slug = re.sub(r"[^a-z0-9]+", "-", email.lower()).strip("-")
        if slug:
            return slug
    # Deterministic md5-based hash (NOT builtin hash(), which is salted per
    # process) so a no-email account keeps the same id across restarts —
    # otherwise its stored tokens, plaud:<id> device_id, and state.json
    # download keys would orphan on the next launch.
    from core.models import _stable_hash
    return str(_stable_hash(access_token))


def account_from_tokens(
    access_token: str,
    refresh_token: str | None,
    region: str,
    email: str | None = None,
) -> PlaudAccount:
    """Build a PlaudAccount from captured tokens, deriving email/account id
    from the access-token JWT when not supplied."""
    claims = _jwt_claims(access_token)
    email = email or claims.get("email") or claims.get("user_email")
    account_id = (
        _account_id_from(email, access_token)
        if email
        else (
            str(claims.get("user_id") or claims.get("uid") or claims.get("sub") or "")
            or _account_id_from(None, access_token)
        )
    )
    display = email or claims.get("nickname") or "Plaud"
    return PlaudAccount(
        account_id=account_id,
        email=email,
        display_name=str(display),
        region=region or "us",
        access_token=access_token,
        refresh_token=refresh_token or None,
    )


class PlaudSignInDialog(QDialog):
    """Sign in to Plaud and capture a session.

    Args:
        region: initial region hint (``us`` / ``eu`` / ``apac``).
        parent: parent widget.

    After ``exec()`` returns ``Accepted``, call :meth:`result_account` for the
    captured :class:`core.plaud.PlaudAccount` (or None if cancelled / failed).
    """

    def __init__(self, region: str = "us", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to Plaud")
        self._region = region or "us"
        self._account: PlaudAccount | None = None
        self._captured = False
        self.backend = "webengine" if _HAS_WEBENGINE else "manual"

        if _HAS_WEBENGINE:
            self._build_webengine_ui()
        else:
            self._build_manual_ui()

    # ----- result accessor -------------------------------------------------
    def result_account(self) -> PlaudAccount | None:
        """Return the captured Plaud account, or None if cancelled."""
        return self._account

    # ----- WebEngine backend ----------------------------------------------
    def _build_webengine_ui(self):
        self.resize(560, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hint = QLabel("Sign in with your Plaud account (Google/Apple SSO supported).")
        hint.setStyleSheet("color: gray; font-size: 11px; padding: 8px 12px;")
        layout.addWidget(hint)

        # Dedicated off-the-record profile so we get a fresh login each time and
        # can poll its cookie store for pld_ut / pld_urt.
        self._profile = QWebEngineProfile(self)
        self._cookie_store = self._profile.cookieStore()
        self._cookies: dict[str, str] = {}
        self._cookie_store.cookieAdded.connect(self._on_cookie_added)

        self._view = QWebEngineView(self)
        from PyQt6.QtWebEngineCore import QWebEnginePage

        self._view.setPage(QWebEnginePage(self._profile, self._view))
        self._view.load(_qurl(PLAUD_SIGNIN_URL))
        layout.addWidget(self._view, stretch=1)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 12, 12)
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        layout.addLayout(footer)

        # Belt-and-braces poll in case cookieAdded misses an already-set cookie.
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll_cookies)
        self._timer.start()

    def _on_cookie_added(self, cookie):
        try:
            name = bytes(cookie.name()).decode("utf-8", "replace")
            value = bytes(cookie.value()).decode("utf-8", "replace")
        except Exception:
            return
        if name in ("pld_ut", "pld_urt") and value:
            self._cookies[name] = value
            self._try_capture()

    def _poll_cookies(self):
        # cookieAdded is the primary path; this just retries capture if both
        # cookies have landed but capture hasn't fired yet.
        self._try_capture()

    def _try_capture(self):
        if self._captured:
            return
        access = self._cookies.get("pld_ut")
        if not access:
            return
        self._captured = True
        self._timer.stop()
        refresh = self._cookies.get("pld_urt")
        self._account = account_from_tokens(access, refresh, self._region)
        self.accept()

    # ----- Manual paste fallback ------------------------------------------
    def _build_manual_ui(self):
        self.resize(480, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Sign in to Plaud")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        instructions = QLabel(
            "The embedded browser (PyQt6-WebEngine) is not installed, so paste "
            "your session tokens manually:\n"
            "1. Open https://web.plaud.ai and sign in.\n"
            "2. Open DevTools (F12) > Application > Cookies > web.plaud.ai.\n"
            "3. Copy the values of the 'pld_ut' and 'pld_urt' cookies below."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(instructions)

        form = QFormLayout()
        self._access_input = QLineEdit()
        self._access_input.setPlaceholderText("pld_ut cookie value (access token)")
        form.addRow("Access token (pld_ut):", self._access_input)

        self._refresh_input = QLineEdit()
        self._refresh_input.setPlaceholderText("pld_urt cookie value (optional but recommended)")
        form.addRow("Refresh token (pld_urt):", self._refresh_input)

        self._region_combo = QComboBox()
        for value, label in REGIONS:
            self._region_combo.addItem(label, value)
        idx = self._region_combo.findData(self._region)
        if idx >= 0:
            self._region_combo.setCurrentIndex(idx)
        form.addRow("Region:", self._region_combo)

        self._email_input = QLineEdit()
        self._email_input.setPlaceholderText("(optional)")
        form.addRow("Email:", self._email_input)
        layout.addLayout(form)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #f38ba8; font-size: 11px;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        layout.addStretch()

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self._signin_btn = QPushButton("Save")
        self._signin_btn.setDefault(True)
        self._signin_btn.clicked.connect(self._on_manual_submit)
        footer.addWidget(self._signin_btn)
        layout.addLayout(footer)

    def _on_manual_submit(self):
        access = self._access_input.text().strip()
        if not access:
            self._error_label.setText("An access token (pld_ut) is required.")
            return
        refresh = self._refresh_input.text().strip() or None
        region = self._region_combo.currentData() or "us"
        email = self._email_input.text().strip() or None
        self._account = account_from_tokens(access, refresh, region, email=email)
        self.accept()


def _qurl(url: str):
    from PyQt6.QtCore import QUrl

    return QUrl(url)
