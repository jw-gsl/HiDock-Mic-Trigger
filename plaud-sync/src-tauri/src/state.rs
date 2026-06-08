use std::sync::atomic::AtomicI64;
use std::sync::Mutex;

use tokio::sync::oneshot;

use crate::storage::Storage;

/// Current unix time in seconds (0 on the impossible pre-epoch case).
pub fn now_epoch() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// What the login webview captured. Either a Plaud JWT detected directly, or a
/// Google `id_token` that still needs to be exchanged via `/auth/sso-callback`.
pub enum BrowserLogin {
    Jwt {
        token: String,
        region: String,
    },
    GoogleSso {
        id_token: String,
        user_area: String,
        region: String,
    },
    /// The login webview already holds a Plaud session — captured directly from
    /// its `pld_ut` / `pld_urt` cookies (works for any SSO provider and for an
    /// already-authenticated webview).
    SessionCookie {
        user_token: String,
        refresh_token: Option<String>,
        region: String,
    },
}

pub struct AppState {
    pub storage: Mutex<Storage>,
    pub browser_login_tx: Mutex<Option<oneshot::Sender<Result<BrowserLogin, String>>>>,
    /// Unix-seconds of the last download sync (manual or auto). Seeded at launch
    /// so the "next auto-sync" countdown is valid from startup. Shared with the
    /// auto-sync loop so the countdown matches the real schedule.
    pub last_sync_epoch: AtomicI64,
}