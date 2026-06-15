use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::{AppHandle, Manager, Runtime, State, Url, WebviewUrl, WebviewWindowBuilder};
use tauri::WindowEvent;
use tauri::webview::NewWindowResponse;
use tokio::sync::oneshot;
use tokio::time::timeout;

use crate::app_types::AuthStatus;
use crate::login_log;
use crate::state::{AppState, BrowserLogin};
use crate::plaud::{PlaudAuth, PlaudClient, SsoSession};
use crate::storage::Storage;

const LOGIN_WINDOW_LABEL: &str = "plaud-login";
const CALLBACK_SCHEME: &str = "plaudsync";
const LOGIN_TIMEOUT: Duration = Duration::from_secs(600);

/// Injected into web.plaud.ai to detect JWT and redirect OAuth popups.
const TOKEN_WATCHER_SCRIPT: &str = r#"
(function () {
  if (window.__plaudSyncHooked) return;
  window.__plaudSyncHooked = true;

  const fallbackRegion = "__PLAUD_REGION__";
  window.__plaudSyncLogBuffer = window.__plaudSyncLogBuffer || [];

  function plog(msg) {
    try {
      window.__plaudSyncLogBuffer.push(new Date().toISOString().slice(11, 19) + " " + String(msg));
    } catch (_) {}
  }

  function describeClickTarget(target) {
    if (!target || !target.tagName) return "unknown";
    const tag = target.tagName.toLowerCase();
    const id = target.id ? '#' + target.id : "";
    const cls = target.className && typeof target.className === "string"
      ? "." + target.className.trim().split(/\s+/).slice(0, 3).join(".")
      : "";
    const text = (target.innerText || target.textContent || "").trim().slice(0, 80);
    const href = target.href || target.getAttribute?.("href") || "";
    const role = target.getAttribute?.("role") || "";
    return tag + id + cls + " text=\"" + text + "\" href=\"" + href + "\" role=\"" + role + "\"";
  }

  plog("token watcher injected at " + location.href);

  function looksLikeJwt(value) {
    return typeof value === "string" && value.split(".").length === 3 && value.length > 80;
  }

  function extractToken(value) {
    if (!value) return null;
    if (looksLikeJwt(value)) return value;
    if (value.startsWith("Bearer ")) return value.slice(7).trim();
    try {
      const parsed = JSON.parse(value);
      if (parsed?.access_token && looksLikeJwt(parsed.access_token)) return parsed.access_token;
      if (parsed?.accessToken && looksLikeJwt(parsed.accessToken)) return parsed.accessToken;
      if (parsed?.token && looksLikeJwt(parsed.token)) return parsed.token;
    } catch (_) {}
    return null;
  }

  function scanStorage() {
    const keys = [];
    for (const store of [localStorage, sessionStorage]) {
      for (let i = 0; i < store.length; i++) {
        const key = store.key(i);
        keys.push(key);
        const token = extractToken(store.getItem(key));
        if (token) return token;
      }
    }
    window.__plaudSyncStorageKeys = keys;
    return null;
  }

  let detectedRegion = fallbackRegion;
  let completed = false;

  function finish(token) {
    if (completed || !token) return;
    completed = true;
    const params = new URLSearchParams({ token, region: detectedRegion });
    window.location.replace("plaudsync://auth?" + params.toString());
  }

  // Hand an SSO credential to the native side via a hidden iframe so this
  // window doesn't navigate to a blank plaudsync:// page (which left a white
  // window behind). The native side POSTs it to /auth/sso-callback.
  function closePopup() {
    try { if (window.__plaudPopup && !window.__plaudPopup.closed) window.__plaudPopup.close(); } catch (_) {}
  }

  function shipSso(params) {
    if (completed) return;
    completed = true;
    // Close the provider popup we opened (only the opener can — wry popups
    // aren't Tauri windows). Apple's web_message flow self-closes its popup, but
    // Google's GIS account chooser does not, so a single close() that's missed
    // (e.g. if the opener is torn down right after) leaves it orphaned. Retry a
    // few times; the native side also keeps this window alive a beat and
    // re-issues the close (see close_oauth_popup).
    closePopup();
    var popupTries = 0;
    var popupTimer = setInterval(function () {
      popupTries++;
      closePopup();
      if (popupTries >= 8 || !window.__plaudPopup || window.__plaudPopup.closed) {
        clearInterval(popupTimer);
      }
    }, 150);
    params.region = params.region || detectedRegion;
    const qs = new URLSearchParams(params).toString();
    try {
      const frame = document.createElement("iframe");
      frame.style.display = "none";
      frame.src = "plaudsync://sso?" + qs;
      (document.body || document.documentElement).appendChild(frame);
    } catch (_) {
      window.location.replace("plaudsync://sso?" + qs);
    }
  }

  // Preferred: replay the exact JSON body web.plaud.ai POSTs to
  // /auth/sso-callback — carries the correct sso_type / fields for ANY provider
  // (Google, Apple, Microsoft) without us reconstructing them.
  function finishSsoBody(rawBody) {
    if (completed || !rawBody) return;
    plog("captured sso-callback body, handing to native: " + String(rawBody).slice(0, 300));
    shipSso({ body: String(rawBody) });
  }

  // Fallback for when only an id_token is available (e.g. the GIS opener path):
  // the native side reconstructs a Google-style body.
  function finishSso(idToken, userArea) {
    if (completed || !looksLikeJwt(idToken)) return;
    plog("captured id_token (opener path), handing off to native sso-callback");
    shipSso({ id_token: idToken, sso_type: "google", user_area: userArea || "" });
  }

  // A GIS credential can arrive as a raw JWT string or as an object with a
  // `credential` / `id_token` field; pull the id_token out of either shape.
  function idTokenFromGisData(data) {
    if (!data) return null;
    if (looksLikeJwt(data)) return data;
    const candidate = data.credential || data.id_token || data.idToken;
    return looksLikeJwt(candidate) ? candidate : null;
  }

  function updateRegionFromUrl(url) {
    const text = String(url || "");
    if (text.includes("api-euc1") || text.includes("euc1")) detectedRegion = "eu";
    else if (text.includes("api-apse1") || text.includes("apse1")) detectedRegion = "apac";
    else if (text.includes("api.plaud.ai")) detectedRegion = "us";
  }

  const originalOpen = window.open;
  window.open = function (url, target, features) {
    plog("window.open url=\"" + url + "\" target=\"" + target + "\" features=\"" + features + "\"");
    if (url) updateRegionFromUrl(url);
    if (originalOpen) {
      const popup = originalOpen.call(window, url, target, features);
      // Keep the handle so we can close this popup once we've captured the
      // credential (wry-created popups aren't Tauri windows, so the native side
      // can't close them — only the opener can).
      if (popup) window.__plaudPopup = popup;
      plog("window.open native popup " + (popup ? "created" : "blocked"));
      return popup;
    }
    plog("window.open unavailable — returning stub");
    return { closed: false, close: function () {} };
  };

  // On the Google GIS transform page the id_token is delivered to the opener
  // via postMessage. In the embedded webview the opener handoff is unreliable,
  // so we intercept the credential here and ship the id_token straight to the
  // native side (which then calls /auth/sso-callback itself).
  if (location.hostname === "accounts.google.com" && location.pathname.indexOf("gsi/transform") !== -1) {
    plog("gsi/transform detected — intercepting GIS credential");
    const captureFromOpener = function (data) {
      const idToken = idTokenFromGisData(data);
      if (idToken) { plog("got id_token from opener.postMessage"); finishSso(idToken, detectedRegion); }
    };
    if (!window.opener) {
      window.opener = { closed: false, postMessage: function (data) { captureFromOpener(data); } };
    } else {
      const realPost = window.opener.postMessage && window.opener.postMessage.bind(window.opener);
      window.opener.postMessage = function (data, targetOrigin) {
        captureFromOpener(data);
        if (realPost) try { return realPost(data, targetOrigin); } catch (_) {}
      };
    }
  }

  document.addEventListener("click", function (event) {
    const target = event.target && event.target.closest
      ? event.target.closest("a,button,[role=button],input[type=submit]")
      : event.target;
    plog("click: " + describeClickTarget(target));
  }, true);

  window.addEventListener("error", function (event) {
    plog("js error: " + (event.message || event) + " at " + (event.filename || "?") + ":" + (event.lineno || "?"));
  });
  window.addEventListener("unhandledrejection", function (event) {
    plog("unhandled rejection: " + (event.reason && event.reason.message ? event.reason.message : event.reason));
  });
  window.addEventListener("beforeunload", function () {
    plog("beforeunload from " + location.href);
  });

  const originalFetch = window.fetch;
  window.fetch = function (...args) {
    const reqUrl = typeof args[0] === "string" ? args[0] : args[0]?.url;
    updateRegionFromUrl(reqUrl);
    // When web.plaud.ai posts an SSO credential to sso-callback, capture the
    // exact request body and replay it natively (works for Google, Apple,
    // Microsoft — the body already carries the right sso_type).
    try {
      if (reqUrl && String(reqUrl).indexOf("/auth/sso-callback") !== -1 && args[1] && typeof args[1].body === "string") {
        const raw = args[1].body;
        const parsed = JSON.parse(raw);
        if (parsed && (parsed.id_token || parsed.code)) finishSsoBody(raw);
      }
    } catch (_) {}
    return originalFetch.apply(this, args);
  };

  // web.plaud.ai performs the SSO exchange over XHR (not fetch), so the body
  // must be read from the XHR *request* body of sso-callback. Do NOT grab the
  // access_token from /auth/access-token-other-web — that is a Frill-scoped
  // token the main API rejects ("invalid auth header").
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__plaudUrl = String(url || "");
    updateRegionFromUrl(url);
    return open.call(this, method, url, ...rest);
  };

  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    const xhrUrl = this.__plaudUrl || "";
    try {
      if (xhrUrl.indexOf("/auth/sso-callback") !== -1 && typeof args[0] === "string") {
        const raw = args[0];
        const parsed = JSON.parse(raw);
        if (parsed && (parsed.id_token || parsed.code)) finishSsoBody(raw);
      }
    } catch (_) {}
    return send.apply(this, args);
  };

  // Note: token capture is handled natively by reading the pld_ut cookie from
  // the webview, plus the id_token interception above. We deliberately do NOT
  // scan localStorage/responses for JWTs — web.plaud.ai stores a Frill-scoped
  // token there that the main API rejects.
  let lastUrl = location.href;
  setInterval(function () {
    if (location.href !== lastUrl) {
      plog("location changed: " + lastUrl + " -> " + location.href);
      lastUrl = location.href;
    }
  }, 700);
})();
"#;

const FLUSH_JS_LOGS_SCRIPT: &str = r#"
(function () {
  if (!window.__plaudSyncLogBuffer || window.__plaudSyncLogBuffer.length === 0) return;
  const logs = window.__plaudSyncLogBuffer.splice(0, 30);
  logs.forEach(function (message) {
    try {
      const frame = document.createElement("iframe");
      frame.style.display = "none";
      frame.src = "plaudsync://log?m=" + encodeURIComponent(String(message).slice(0, 2000));
      (document.body || document.documentElement).appendChild(frame);
    } catch (_) {}
  });
})();
"#;

pub async fn login_with_browser<R: Runtime>(
    app: &AppHandle<R>,
    region: &str,
    state: State<'_, AppState>,
) -> Result<AuthStatus, String> {
    close_login_window(app);

    if let Ok(dir) = app.path().app_data_dir() {
        login_log::init(dir);
    }

    login_log::info(&format!("starting browser login (region={region})"));

    let storage = state.storage.lock().map_err(|e| e.to_string())?.clone();
    let (tx, rx) = oneshot::channel::<Result<BrowserLogin, String>>();

    {
        let mut slot = state
            .browser_login_tx
            .lock()
            .map_err(|e| e.to_string())?;
        *slot = Some(tx);
    }

    let script = TOKEN_WATCHER_SCRIPT.replace("__PLAUD_REGION__", region);
    let login_url = "https://web.plaud.ai";
    let app_handle = app.clone();
    let app_for_popup = app.clone();
    let default_region = region.to_string();
    let popup_region = region.to_string();

    // Reading cookies (`read_session_cookie` -> wry `cookies_for_url`) spins a
    // nested run loop on the *main* thread until WebKit's `getAllCookies`
    // callback fires. Doing that on a tight timer wedges the main thread during
    // the OAuth flow and hangs the whole app (the login window then can't even
    // close). Instead, only read cookies when a real navigation/page-load tells
    // us the session may have changed, and after it has settled. Seeded `true`
    // so an already-authenticated webview is captured on first load.
    let cookie_check = Arc::new(AtomicBool::new(true));
    let nav_cookie_check = cookie_check.clone();
    let load_cookie_check = cookie_check.clone();

    let window = WebviewWindowBuilder::new(
        app,
        LOGIN_WINDOW_LABEL,
        WebviewUrl::External(
            login_url
                .parse()
                .map_err(|e| format!("Invalid login URL: {e}"))?,
        ),
    )
    .title("Sign in to Plaud")
    .inner_size(520.0, 760.0)
    .center()
    .initialization_script(&script)
    .on_navigation(move |url| {
        // A real page navigation (not one of our `plaudsync://` callback/log
        // pings) can mean the session cookie just appeared — schedule one
        // settled cookie read rather than polling continuously.
        if url.scheme() != CALLBACK_SCHEME {
            nav_cookie_check.store(true, Ordering::Relaxed);
        }
        handle_navigation(&app_handle, &default_region, &url)
    })
    .on_new_window(move |url, _features| handle_new_window(&app_for_popup, &popup_region, &url))
    .on_page_load(move |window, payload| {
        login_log::info(&format!("page load: {}", payload.url()));
        let _ = window.eval(FLUSH_JS_LOGS_SCRIPT);
        load_cookie_check.store(true, Ordering::Relaxed);
    })
    .build()
    .map_err(|e| {
        login_log::error(&format!("failed to open login window: {e}"));
        format!("Failed to open login window: {e}")
    })?;

    let app_for_close = app.clone();
    // `CloseRequested` can fire repeatedly while the window tears down; only act
    // on the first one. (Previously this re-entered window.close() on every
    // event, spinning a feedback loop that wrote hundreds of thousands of log
    // lines.) We resolve the pending login with an error and let the window
    // close on its own — we must NOT call window.close() here.
    let close_handled = Arc::new(AtomicBool::new(false));
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { .. } = event {
            if close_handled.swap(true, Ordering::Relaxed) {
                return;
            }
            login_log::warn("sign-in window closed by user");
            resolve_login(&app_for_close, Err("Sign-in window closed before login completed.".to_string()));
        }
    });

    let poll_stop = Arc::new(AtomicBool::new(false));
    let poll_app = app.clone();
    let poll_stop_flag = poll_stop.clone();
    let poll_region = region.to_string();
    let poll_cookie_check = cookie_check.clone();
    tokio::spawn(async move {
        while !poll_stop_flag.load(Ordering::Relaxed) {
            tokio::time::sleep(Duration::from_millis(500)).await;
            if poll_app.get_webview_window(LOGIN_WINDOW_LABEL).is_none() {
                break;
            }
            // Cheap and non-blocking — safe to do on every tick.
            flush_js_logs(&poll_app);

            // Only touch cookies when a navigation/page-load asked us to (see
            // `cookie_check` above). This is the primary capture path: once
            // web.plaud.ai has a session its pld_ut cookie is present in the
            // webview. Robust to any SSO provider and to an already-authenticated
            // webview — without continuously wedging the main thread.
            if !poll_cookie_check.swap(false, Ordering::Relaxed) {
                continue;
            }
            // Let the just-loaded page settle so WebKit's cookie store is idle
            // when we query it (its callback runs on the main thread).
            tokio::time::sleep(Duration::from_millis(400)).await;
            let Some(window) = poll_app.get_webview_window(LOGIN_WINDOW_LABEL) else {
                break;
            };
            if let Some(login) = read_session_cookie(&window, &poll_region) {
                login_log::info("captured pld_ut session cookie from login webview");
                resolve_login(&poll_app, Ok(login));
                break;
            }
        }
    });

    let captured = match timeout(LOGIN_TIMEOUT, rx).await {
        Ok(Ok(result)) => result.map_err(|e| {
            poll_stop.store(true, Ordering::Relaxed);
            flush_js_logs(app);
            login_log::error(&e);
            append_log_hint(e)
        })?,
        Ok(Err(_)) => {
            poll_stop.store(true, Ordering::Relaxed);
            flush_js_logs(app);
            let msg = append_log_hint("Sign-in was interrupted.".into());
            login_log::error(&msg);
            return Err(msg);
        }
        Err(_) => {
            poll_stop.store(true, Ordering::Relaxed);
            flush_js_logs(app);
            close_login_window(app);
            clear_login_channel(state);
            let msg = append_log_hint("Sign-in timed out. Please try again.".into());
            login_log::error(&msg);
            return Err(msg);
        }
    };

    flush_js_logs(app);
    login_log::info("credential captured, completing login");

    // Keep the webview open while completing: an SSO identity with no linked
    // Plaud account (new sign-up) needs the user to finish registering on
    // web.plaud.ai, after which the session is captured from the webview cookies.
    let mut captured = captured;
    let result = loop {
        match captured {
            BrowserLogin::Jwt { token, region } => {
                break complete_browser_auth(storage.clone(), &token, &region).await;
            }
            BrowserLogin::SessionCookie {
                user_token,
                refresh_token,
                region,
            } => {
                break complete_session_cookie(
                    storage.clone(),
                    &user_token,
                    refresh_token.as_deref(),
                    &region,
                )
                .await;
            }
            BrowserLogin::Sso { body, region } => {
                match complete_sso(storage.clone(), &body, &region).await {
                    Ok(Some(status)) => break Ok(status),
                    Err(e) => break Err(append_log_hint(e)),
                    // Needs registration: leave the window open, re-arm the
                    // channel, and wait for the cookie poll to capture the
                    // session once the user finishes signing up.
                    Ok(None) => {
                        login_log::info("waiting for sign-up to complete in the webview…");
                        let (tx2, rx2) = oneshot::channel::<Result<BrowserLogin, String>>();
                        if let Ok(mut slot) = app.state::<AppState>().browser_login_tx.lock() {
                            *slot = Some(tx2);
                        }
                        cookie_check.store(true, Ordering::Relaxed);
                        match timeout(LOGIN_TIMEOUT, rx2).await {
                            Ok(Ok(Ok(next))) => {
                                captured = next;
                                continue;
                            }
                            Ok(Ok(Err(e))) => break Err(append_log_hint(e)),
                            Ok(Err(_)) => {
                                break Err(append_log_hint("Sign-up was interrupted.".into()))
                            }
                            Err(_) => break Err(append_log_hint(
                                "Sign-up timed out. Finish creating your Plaud account, then try again.".into(),
                            )),
                        }
                    }
                }
            }
        }
    };

    poll_stop.store(true, Ordering::Relaxed);
    flush_js_logs(app);
    // Close the orphaned provider popup (e.g. Google's account chooser) before
    // tearing down the login window, so nothing lingers after sign-in.
    close_oauth_popup(app);
    close_login_window(app);
    clear_login_channel(state);
    result
}

/// Read the Plaud session cookies (`pld_ut` / `pld_urt`) from the login webview.
/// Returns a `SessionCookie` login once a non-empty user token is present.
fn read_session_cookie<R: Runtime>(
    window: &tauri::WebviewWindow<R>,
    region: &str,
) -> Option<BrowserLogin> {
    let url = "https://api.plaud.ai".parse().ok()?;
    let cookies = window.cookies_for_url(url).ok()?;

    let mut user_token = None;
    let mut refresh_token = None;
    for cookie in &cookies {
        match cookie.name() {
            "pld_ut" if !cookie.value().is_empty() => user_token = Some(cookie.value().to_string()),
            "pld_urt" if !cookie.value().is_empty() => {
                refresh_token = Some(cookie.value().to_string())
            }
            _ => {}
        }
    }

    user_token.map(|user_token| BrowserLogin::SessionCookie {
        user_token,
        refresh_token,
        region: region.to_string(),
    })
}

async fn complete_session_cookie(
    storage: Storage,
    user_token: &str,
    refresh_token: Option<&str>,
    region: &str,
) -> Result<AuthStatus, String> {
    let auth = PlaudAuth::new(storage.clone());
    // web.plaud.ai already exchanged the SSO credential — just adopt its tokens.
    auth.adopt_session_tokens(user_token, refresh_token, region)?;

    let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), region.to_string());
    let user = client.get_user_info().await?;

    if !user.email.is_empty() {
        storage
            .save_credentials(&user.email, region)
            .map_err(|e| e.to_string())?;
    }
    if !user.nickname.is_empty() {
        let _ = storage.save_display_name(&user.nickname);
    }

    login_log::info(&format!("session-cookie sign-in complete for {}", user.email));

    Ok(AuthStatus {
        logged_in: true,
        email: if user.email.is_empty() {
            None
        } else {
            Some(user.email)
        },
        region: Some(region.to_string()),
        name: storage.get_display_name(),
    })
}

/// Complete an SSO exchange. Returns `Ok(Some(status))` once signed in, or
/// `Ok(None)` if the SSO identity isn't linked to a Plaud account yet — in which
/// case the caller keeps the webview open so the user can finish sign-up and the
/// session is then captured from the webview cookies.
async fn complete_sso(
    storage: Storage,
    body: &str,
    region: &str,
) -> Result<Option<AuthStatus>, String> {
    let auth = PlaudAuth::new(storage.clone());
    // `login_with_sso` may switch to the account's real region (e.g. the user
    // picked US but the account is EU); use the returned region from here on.
    let region = match auth.login_with_sso(body, region).await? {
        SsoSession::Authenticated { region, .. } => region,
        SsoSession::NeedsRegistration { sso_email } => {
            login_log::info(&format!(
                "sso identity {sso_email} not linked to a Plaud account — awaiting sign-up in webview"
            ));
            return Ok(None);
        }
    };

    let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), region.clone());
    let user = client.get_user_info().await?;

    if !user.email.is_empty() {
        storage
            .save_credentials(&user.email, &region)
            .map_err(|e| e.to_string())?;
    }
    if !user.nickname.is_empty() {
        let _ = storage.save_display_name(&user.nickname);
    }

    login_log::info(&format!(
        "sso sign-in complete for {} (region={region})",
        user.email
    ));

    Ok(Some(AuthStatus {
        logged_in: true,
        email: if user.email.is_empty() {
            None
        } else {
            Some(user.email)
        },
        region: Some(region),
        name: storage.get_display_name(),
    }))
}

fn append_log_hint(message: String) -> String {
    if let Some(path) = login_log::path() {
        format!("{message}\n\nDebug log: {}", path.display())
    } else {
        message
    }
}

fn handle_new_window<R: Runtime>(
    app: &AppHandle<R>,
    region: &str,
    url: &Url,
) -> NewWindowResponse<R> {
    login_log::info(&format!("popup/new-window requested: {url}"));

    if url.scheme() == CALLBACK_SCHEME {
        return NewWindowResponse::Deny;
    }

    if is_allowed_login_url(url) {
        login_log::info(&format!("allow popup: {url}"));
        reinject_script(app, region);
        return NewWindowResponse::Allow;
    }

    login_log::warn(&format!("blocked popup URL: {url}"));
    NewWindowResponse::Deny
}

fn flush_js_logs<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window(LOGIN_WINDOW_LABEL) {
        let _ = window.eval(FLUSH_JS_LOGS_SCRIPT);
    }
}

async fn complete_browser_auth(
    storage: Storage,
    token: &str,
    region: &str,
) -> Result<AuthStatus, String> {
    let auth = PlaudAuth::new(storage.clone());
    auth.login_with_jwt(token, region)?;

    let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), region.to_string());
    let user = client.get_user_info().await?;

    if !user.email.is_empty() {
        storage
            .save_credentials(&user.email, region)
            .map_err(|e| e.to_string())?;
    }
    if !user.nickname.is_empty() {
        let _ = storage.save_display_name(&user.nickname);
    }

    login_log::info(&format!("login complete for {}", user.email));

    Ok(AuthStatus {
        logged_in: true,
        email: if user.email.is_empty() {
            None
        } else {
            Some(user.email)
        },
        region: Some(region.to_string()),
        name: storage.get_display_name(),
    })
}

fn handle_navigation<R: Runtime>(app: &AppHandle<R>, default_region: &str, url: &Url) -> bool {
    if url.scheme() == CALLBACK_SCHEME {
        if url.host_str() == Some("log") {
            let query: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
            if let Some(message) = query.get("m") {
                login_log::debug(&format!("js: {message}"));
            } else {
                login_log::debug(&format!("js log callback without message: {url}"));
            }
            return false;
        }

        login_log::info(&format!("callback navigation: {url}"));
        if let Some(result) = parse_callback(url, default_region) {
            match &result {
                Ok(_) => login_log::info("auth callback parsed successfully"),
                Err(e) => login_log::error(&format!("auth callback error: {e}")),
            }
            resolve_login(app, result);
        }
        // Don't tear the login window down here — that races with the OAuth
        // popup's close() and can orphan it (notably Google's GIS account
        // chooser). Just nudge the popup closed; the main sign-in flow closes
        // this window once the credential has been processed.
        close_oauth_popup(app);
        return false;
    }

    if is_allowed_login_url(url) {
        login_log::info(&format!("allow navigation: {url}"));
        reinject_script(app, default_region);
        flush_js_logs(app);
        return true;
    }

    login_log::warn(&format!("block navigation: {url}"));
    false
}

fn parse_callback(url: &Url, default_region: &str) -> Option<Result<BrowserLogin, String>> {
    let query: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
    let region = query
        .get("region")
        .cloned()
        .filter(|r| r == "us" || r == "eu")
        .unwrap_or_else(|| default_region.to_string());

    match url.host_str() {
        Some("auth") => {
            let token = query.get("token").cloned().filter(|t| !t.is_empty())?;
            Some(Ok(BrowserLogin::Jwt { token, region }))
        }
        Some("sso") => {
            // Preferred: the exact JSON body web.plaud.ai POSTed to
            // /auth/sso-callback (carries the right sso_type for any provider).
            if let Some(body) = query.get("body").cloned().filter(|b| !b.is_empty()) {
                return Some(Ok(BrowserLogin::Sso { body, region }));
            }
            // Fallback: only an id_token was captured (e.g. the GIS opener path).
            // Reconstruct the Google-style body the backend expects.
            let id_token = query.get("id_token").cloned().filter(|t| !t.is_empty())?;
            let sso_type = query
                .get("sso_type")
                .cloned()
                .filter(|t| !t.is_empty())
                .unwrap_or_else(|| "google".to_string());
            let user_area = query.get("user_area").cloned().unwrap_or_default();
            let body = serde_json::json!({
                "sso_from": "web",
                "sso_type": sso_type,
                "id_token": id_token,
                "user_area": user_area,
            })
            .to_string();
            Some(Ok(BrowserLogin::Sso { body, region }))
        }
        _ => Some(Err("Unexpected sign-in callback.".into())),
    }
}

fn is_allowed_login_url(url: &Url) -> bool {
    if url.scheme() != "https" && url.scheme() != "http" {
        return false;
    }

    let host = url.host_str().unwrap_or("").to_lowercase();
    if host.is_empty() {
        return false;
    }

    let allowed_suffixes = [
        "plaud.ai",
        "google.com",
        "google.co.uk",
        "googleusercontent.com",
        "gstatic.com",
        "googleapis.com",
        "apple.com",
        "appleid.apple.com",
        "idmsa.apple.com",
        "facebook.com",
        "microsoft.com",
        "live.com",
        "office.com",
        "microsoftonline.com",
        "github.com",
    ];

    allowed_suffixes
        .iter()
        .any(|suffix| host == *suffix || host.ends_with(&format!(".{suffix}")))
}

fn reinject_script<R: Runtime>(app: &AppHandle<R>, region: &str) {
    if let Some(window) = app.get_webview_window(LOGIN_WINDOW_LABEL) {
        let script = TOKEN_WATCHER_SCRIPT.replace("__PLAUD_REGION__", region);
        let _ = window.eval(&script);
    }
}

fn resolve_login<R: Runtime>(app: &AppHandle<R>, result: Result<BrowserLogin, String>) {
    if let Ok(mut slot) = app.state::<AppState>().browser_login_tx.lock() {
        if let Some(tx) = slot.take() {
            let _ = tx.send(result);
        }
    }
}

fn clear_login_channel(state: State<'_, AppState>) {
    if let Ok(mut slot) = state.browser_login_tx.lock() {
        *slot = None;
    }
}

/// Close the login window and any auxiliary popups (e.g. the Google credential
/// picker, which doesn't always close itself in the embedded webview). Leaves
/// the main app window ("main") untouched.
/// Close the provider OAuth popup (Google account chooser, Apple ID, …) the
/// login webview opened via `window.open`.
///
/// Two mechanisms: a best-effort JS `close()` (honored by some webviews), and —
/// on macOS — a native AppKit sweep, because WebKit ignores an opener-initiated
/// `close()` for these popups, leaving them orphaned (Google's GIS chooser in
/// particular). The JS attempt is harmless where the native sweep does the work.
fn close_oauth_popup<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window(LOGIN_WINDOW_LABEL) {
        let _ = window.eval(
            "try { if (window.__plaudPopup && !window.__plaudPopup.closed) window.__plaudPopup.close(); } catch (e) {}",
        );
    }
    #[cfg(target_os = "macos")]
    close_orphaned_login_windows(app);
}

/// macOS: close any visible app window that isn't one of Tauri's own (the main
/// window or the login window). During the login flow the only such window is
/// the OAuth provider popup WebKit opened via `window.open` — which isn't a
/// Tauri window and can't be closed from JS, so we close its NSWindow directly.
/// Dispatched to the main thread (AppKit requirement).
#[cfg(target_os = "macos")]
fn close_orphaned_login_windows<R: Runtime>(app: &AppHandle<R>) {
    // Pointers to the NSWindows Tauri owns — captured now, never closed.
    let tracked: Vec<usize> = app
        .webview_windows()
        .values()
        .filter_map(|w| w.ns_window().ok())
        .map(|p| p as usize)
        .collect();

    let _ = app.run_on_main_thread(move || {
        use objc::runtime::{Object, BOOL, YES};
        use objc::{class, msg_send, sel, sel_impl};

        unsafe {
            let ns_app: *mut Object = msg_send![class!(NSApplication), sharedApplication];
            if ns_app.is_null() {
                return;
            }
            let windows: *mut Object = msg_send![ns_app, windows];
            if windows.is_null() {
                return;
            }
            let count: usize = msg_send![windows, count];
            for i in 0..count {
                let window: *mut Object = msg_send![windows, objectAtIndex: i];
                if window.is_null() || tracked.contains(&(window as usize)) {
                    continue;
                }
                // Only sweep visible windows — leave hidden/auxiliary AppKit
                // windows (panels, offscreen helpers) untouched.
                let visible: BOOL = msg_send![window, isVisible];
                if visible != YES {
                    continue;
                }
                login_log::info("closing orphaned OAuth popup window (native)");
                let _: () = msg_send![window, close];
            }
        }
    });
}

pub fn close_login_window<R: Runtime>(app: &AppHandle<R>) {
    for (label, window) in app.webview_windows() {
        if label != "main" {
            let _ = window.close();
        }
    }
}

pub fn open_debug_log() -> Result<(), String> {
    if let Some(path) = login_log::path() {
        open::that(path).map_err(|e| e.to_string())
    } else {
        Err("No login debug log available yet. Try signing in first.".into())
    }
}