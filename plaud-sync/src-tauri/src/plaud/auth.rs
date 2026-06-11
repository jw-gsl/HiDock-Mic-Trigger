use base64::Engine;
use chrono::Utc;

use super::types::{base_url, PlaudTokenData, TOKEN_REFRESH_BUFFER_MS};
use crate::storage::Storage;

pub struct PlaudAuth {
    storage: Storage,
}

impl PlaudAuth {
    pub fn new(storage: Storage) -> Self {
        Self { storage }
    }

    pub async fn get_token(&self) -> Result<String, String> {
        let stored = self.storage.get_token();

        if let Some(ref token) = stored {
            if !is_expiring_soon(token) {
                return Ok(token.access_token.clone());
            }
        }

        // Token is missing or near expiry. We can only silently refresh when a
        // password is stored (email/password accounts). Browser/JWT logins have
        // no password, so we keep using the existing token until it truly fails
        // rather than erroring out on a still-valid token.
        let has_password = matches!(self.storage.get_password(), Ok(Some(_)));
        if has_password {
            return self
                .login_with_stored_credentials()
                .await
                .map(|t| t.access_token);
        }

        // Google/SSO accounts: refresh the short-lived user token with the
        // long-lived refresh token. If refresh fails but we still hold a token,
        // fall back to it rather than blocking the user.
        let has_refresh = matches!(self.storage.get_refresh_token(), Ok(Some(_)));
        if has_refresh {
            match self.refresh_with_user_token().await {
                Ok(token) => return Ok(token.access_token),
                Err(e) if stored.is_none() => return Err(e),
                Err(_) => {}
            }
        }

        if let Some(token) = stored {
            return Ok(token.access_token);
        }

        Err("Not logged in. Please sign in first.".into())
    }

    /// Email/password sign-in. Tries the given region and, on a region mismatch,
    /// auto-retries against the region Plaud points to (so the user doesn't pick
    /// it). Returns the region the account actually belongs to.
    pub async fn login_with_credentials(
        &self,
        email: &str,
        password: &str,
        region: &str,
    ) -> Result<String, String> {
        self.storage
            .save_credentials(email, region)
            .map_err(|e| e.to_string())?;
        self.storage
            .save_password(password)
            .map_err(|e| e.to_string())?;

        match self.password_attempt(region).await? {
            PwOutcome::Session(_) => Ok(region.to_string()),
            PwOutcome::RegionRedirect(correct) if correct != region => {
                crate::login_log::info(&format!(
                    "password region mismatch — retrying in region '{correct}'"
                ));
                // Persist the corrected region so the session uses the right host.
                self.storage
                    .save_credentials(email, &correct)
                    .map_err(|e| e.to_string())?;
                match self.password_attempt(&correct).await? {
                    PwOutcome::Session(_) => Ok(correct),
                    _ => Err("Could not resolve your account's region.".to_string()),
                }
            }
            PwOutcome::RegionRedirect(_) => {
                Err("Could not resolve your account's region.".to_string())
            }
            PwOutcome::Error(msg) => Err(msg),
        }
    }

    /// Single attempt against the stored region — used to silently refresh the
    /// token for email/password accounts (region is already known here).
    pub async fn login_with_stored_credentials(&self) -> Result<PlaudTokenData, String> {
        let region = self.storage.get_region();
        match self.password_attempt(&region).await? {
            PwOutcome::Session(token) => Ok(token),
            PwOutcome::RegionRedirect(_) => {
                Err("Account region changed; please sign in again.".to_string())
            }
            PwOutcome::Error(msg) => Err(msg),
        }
    }

    /// One POST to `/auth/access-token` for `region`. On success saves the token;
    /// otherwise classifies the failure (region redirect vs. surfaced error).
    async fn password_attempt(&self, region: &str) -> Result<PwOutcome, String> {
        let creds = self
            .storage
            .get_credentials()
            .ok_or_else(|| "No credentials configured.".to_string())?;
        let password = self
            .storage
            .get_password()
            .map_err(|e| e.to_string())?
            .ok_or_else(|| "No password stored.".to_string())?;

        let client = reqwest::Client::new();
        let res = client
            .post(format!("{}/auth/access-token", base_url(region)))
            .form(&[
                ("username", creds.email.as_str()),
                ("password", password.as_str()),
            ])
            .send()
            .await
            .map_err(|e| format!("Network error: {e}"))?;

        let body_text = res.text().await.unwrap_or_default();
        let json: serde_json::Value =
            serde_json::from_str(&body_text).unwrap_or(serde_json::Value::Null);
        let app_status = json["status"].as_i64().unwrap_or(-1);
        let msg = json["msg"].as_str().unwrap_or("").to_string();
        crate::login_log::info(&format!(
            "access-token response region={region} status={app_status} msg=\"{msg}\""
        ));

        if app_status == 0 {
            if let Some(access_token) = json["access_token"].as_str() {
                let token =
                    build_token_data(access_token, json["token_type"].as_str())?;
                self.storage.save_token(&token).map_err(|e| e.to_string())?;
                return Ok(PwOutcome::Session(token));
            }
        }

        // Region mismatch: prefer the host Plaud points to, else flip us<->eu.
        let mismatch = json["data"]["domains"]["api"].is_string()
            || msg.to_lowercase().contains("region");
        if mismatch {
            let correct = json["data"]["domains"]["api"]
                .as_str()
                .and_then(region_from_api_host)
                .unwrap_or_else(|| if region == "eu" { "us" } else { "eu" }.to_string());
            return Ok(PwOutcome::RegionRedirect(correct));
        }

        Ok(PwOutcome::Error(if msg.is_empty() {
            "Login failed".to_string()
        } else {
            msg
        }))
    }

    /// Web SSO sign-in (Google, Apple, Microsoft, …). Replays the exact JSON
    /// `body` that web.plaud.ai POSTs to `/auth/sso-callback` — so the correct
    /// `sso_type` and provider-specific fields are carried through without us
    /// guessing them. The endpoint returns the user token (`pld_ut`) and refresh
    /// token (`pld_urt`) as Set-Cookie headers; we use `pld_ut` as a normal
    /// Bearer token (verified against the live API).
    ///
    /// If the account belongs to another region, Plaud replies `200 OK` with an
    /// in-body `{status:-302, msg:"user region mismatch", data.domains.api}` and
    /// no session cookie. We detect that and retry against the indicated region
    /// once, so the user doesn't have to pre-select the right region. Returns the
    /// token and the region the session actually belongs to.
    pub async fn login_with_sso(&self, body: &str, region: &str) -> Result<SsoSession, String> {
        let outcome = match self.sso_attempt(body, region).await? {
            SsoOutcome::RegionRedirect(correct) if correct != region => {
                crate::login_log::info(&format!(
                    "sso region mismatch — retrying in region '{correct}'"
                ));
                // Carry the resolved region with the session.
                match self.sso_attempt(body, &correct).await? {
                    SsoOutcome::Session => {
                        return Ok(SsoSession::Authenticated { region: correct })
                    }
                    other => other,
                }
            }
            other => other,
        };

        match outcome {
            SsoOutcome::Session => Ok(SsoSession::Authenticated {
                region: region.to_string(),
            }),
            SsoOutcome::NeedsRegistration { sso_email } => {
                Ok(SsoSession::NeedsRegistration { sso_email })
            }
            SsoOutcome::RegionRedirect(_) => {
                Err("Could not resolve your account's region.".to_string())
            }
            SsoOutcome::Error(msg) => Err(msg),
        }
    }

    /// One POST to `/auth/sso-callback` for `region`. On success saves the token
    /// and returns it; otherwise classifies the failure (region redirect vs. a
    /// surfaced error message).
    async fn sso_attempt(&self, body: &str, region: &str) -> Result<SsoOutcome, String> {
        let client = reqwest::Client::new();
        let res = client
            .post(format!("{}/auth/sso-callback", base_url(region)))
            .header("app-platform", "web")
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body.to_string())
            .send()
            .await
            .map_err(|e| format!("Network error: {e}"))?;

        let status = res.status();
        let headers = res.headers().clone();
        let cookie_names: Vec<String> = headers
            .get_all(reqwest::header::SET_COOKIE)
            .iter()
            .filter_map(|v| v.to_str().ok())
            .map(|s| s.split('=').next().unwrap_or("").trim().to_string())
            .collect();
        let body_text = res.text().await.unwrap_or_default();
        // Parse once; log a concise summary (no raw body — it carries a token id).
        let json: serde_json::Value =
            serde_json::from_str(&body_text).unwrap_or(serde_json::Value::Null);
        crate::login_log::info(&format!(
            "sso-callback response region={} http={} cookies=[{}] body.status={} msg=\"{}\"",
            region,
            status,
            cookie_names.join(","),
            json["status"].as_i64().unwrap_or(0),
            json["msg"].as_str().unwrap_or("")
        ));

        // Success is signalled by the session cookie, regardless of HTTP/body
        // status quirks.
        if let Some(user_token) = extract_set_cookie(&headers, "pld_ut") {
            let refresh_token = extract_set_cookie(&headers, "pld_urt");
            let token = build_token_data(&user_token, Some("Bearer"))?;
            self.storage
                .save_credentials("sso", region)
                .map_err(|e| e.to_string())?;
            self.storage.save_token(&token).map_err(|e| e.to_string())?;
            if let Some(refresh_token) = refresh_token {
                self.storage
                    .save_refresh_token(&refresh_token)
                    .map_err(|e| e.to_string())?;
            }
            return Ok(SsoOutcome::Session);
        }

        // No session cookie — inspect Plaud's in-body status. A region mismatch
        // tells us the correct API host; anything else is a surfaced error.
        {
            if let Some(api) = json["data"]["domains"]["api"].as_str() {
                if let Some(correct) = region_from_api_host(api) {
                    return Ok(SsoOutcome::RegionRedirect(correct));
                }
            }
            // Recognised SSO identity but no linked Plaud account: the backend
            // echoes the SSO identity with a null account `email`. This means the
            // user must complete sign-up/registration in the webview.
            if json["sso_id"].as_str().is_some() && json["email"].is_null() {
                let sso_email = json["sso_email"].as_str().unwrap_or("").to_string();
                return Ok(SsoOutcome::NeedsRegistration { sso_email });
            }

            let app_status = json["status"].as_i64().unwrap_or(0);
            if app_status != 0 {
                let msg = json["msg"].as_str().unwrap_or("").trim();
                if !msg.is_empty() {
                    return Ok(SsoOutcome::Error(format!("Sign-in failed: {msg}")));
                }
            }
        }

        if !status.is_success() {
            return Ok(SsoOutcome::Error(format!("SSO sign-in failed: {status}")));
        }
        Ok(SsoOutcome::Error(
            "SSO sign-in did not return a session token.".to_string(),
        ))
    }

    /// Adopt session tokens captured directly from the login webview's cookies
    /// (`pld_ut` as the bearer, optional `pld_urt` as the refresh token). Used
    /// when web.plaud.ai already completed the SSO exchange itself.
    pub fn adopt_session_tokens(
        &self,
        user_token: &str,
        refresh_token: Option<&str>,
        region: &str,
    ) -> Result<PlaudTokenData, String> {
        let token = build_token_data(user_token, Some("Bearer"))?;
        self.storage
            .save_credentials("sso", region)
            .map_err(|e| e.to_string())?;
        self.storage.save_token(&token).map_err(|e| e.to_string())?;
        if let Some(refresh_token) = refresh_token {
            self.storage
                .save_refresh_token(refresh_token)
                .map_err(|e| e.to_string())?;
        }
        Ok(token)
    }

    /// Exchange the stored `pld_urt` refresh token for a fresh `pld_ut` user
    /// token via `/auth/refresh-user-token`.
    pub async fn refresh_with_user_token(&self) -> Result<PlaudTokenData, String> {
        let refresh_token = self
            .storage
            .get_refresh_token()
            .map_err(|e| e.to_string())?
            .ok_or_else(|| "No refresh token stored.".to_string())?;
        let region = self.storage.get_region();

        let client = reqwest::Client::new();
        let res = client
            .post(format!("{}/auth/refresh-user-token", base_url(&region)))
            .header("app-platform", "web")
            .header("Cookie", format!("pld_urt={refresh_token}"))
            .send()
            .await
            .map_err(|e| format!("Network error: {e}"))?;

        if !res.status().is_success() {
            return Err(format!("Session refresh failed: {}", res.status()));
        }

        let user_token = extract_set_cookie(res.headers(), "pld_ut")
            .ok_or_else(|| "Session refresh did not return a new token.".to_string())?;

        // The endpoint may rotate the refresh token too.
        if let Some(new_refresh) = extract_set_cookie(res.headers(), "pld_urt") {
            let _ = self.storage.save_refresh_token(&new_refresh);
        }

        let token = build_token_data(&user_token, Some("Bearer"))?;
        self.storage.save_token(&token).map_err(|e| e.to_string())?;
        Ok(token)
    }

    pub fn login_with_jwt(&self, jwt: &str, region: &str) -> Result<PlaudTokenData, String> {
        let token = build_token_data(jwt, Some("Bearer"))?;
        self.storage
            .save_credentials("jwt-user", region)
            .map_err(|e| e.to_string())?;
        self.storage
            .save_token(&token)
            .map_err(|e| e.to_string())?;
        Ok(token)
    }
}

/// Return the value of a `Set-Cookie` header for `name`, taking the last
/// non-empty occurrence (the server emits an empty clearing cookie before the
/// real one).
fn extract_set_cookie(headers: &reqwest::header::HeaderMap, name: &str) -> Option<String> {
    let prefix = format!("{name}=");
    let mut found = None;
    for value in headers.get_all(reqwest::header::SET_COOKIE).iter() {
        if let Ok(text) = value.to_str() {
            if let Some(rest) = text.strip_prefix(&prefix) {
                // The server emits a clearing cookie `name=""` before the real
                // one; trim surrounding quotes so that reads as empty.
                let val = rest
                    .split(';')
                    .next()
                    .unwrap_or("")
                    .trim()
                    .trim_matches('"');
                if !val.is_empty() {
                    found = Some(val.to_string());
                }
            }
        }
    }
    found
}

/// Outcome of `login_with_sso`: either a live Plaud session, or the SSO identity
/// is recognised but not yet linked to a Plaud account (the user must finish
/// sign-up in the webview).
pub enum SsoSession {
    /// Signed in; the session token is already persisted. `region` is the region
    /// the account actually belongs to (may differ from the requested one).
    Authenticated { region: String },
    /// SSO identity recognised but not linked to a Plaud account yet.
    NeedsRegistration { sso_email: String },
}

/// Result of a single `/auth/access-token` (email/password) attempt.
enum PwOutcome {
    /// Signed in; token persisted.
    Session(PlaudTokenData),
    /// The account lives in another region; value is the region to retry in.
    RegionRedirect(String),
    /// A surfaced, user-facing failure.
    Error(String),
}

/// Result of a single `/auth/sso-callback` attempt.
enum SsoOutcome {
    /// A Plaud session was established and persisted (`pld_ut` captured).
    Session,
    /// The SSO identity is recognised but not linked to a Plaud account yet.
    NeedsRegistration { sso_email: String },
    /// The account lives in another region; value is the region to retry in.
    RegionRedirect(String),
    /// A surfaced, user-facing failure.
    Error(String),
}

/// Map a Plaud API host (from a region-mismatch response) to our region key.
/// e.g. `https://api-euc1.plaud.ai` → `eu`, `https://api.plaud.ai` → `us`.
fn region_from_api_host(api: &str) -> Option<String> {
    if api.contains("euc1") || api.contains("-eu") {
        Some("eu".to_string())
    } else if api.contains("api.plaud.ai") {
        Some("us".to_string())
    } else {
        None
    }
}

fn is_expiring_soon(token: &PlaudTokenData) -> bool {
    Utc::now().timestamp_millis() + TOKEN_REFRESH_BUFFER_MS > token.expires_at
}

fn build_token_data(access_token: &str, token_type: Option<&str>) -> Result<PlaudTokenData, String> {
    let (iat, exp) = decode_jwt_expiry(access_token)?;
    Ok(PlaudTokenData {
        access_token: access_token.to_string(),
        token_type: token_type.unwrap_or("Bearer").to_string(),
        issued_at: iat * 1000,
        expires_at: exp * 1000,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::header::{HeaderMap, HeaderValue, SET_COOKIE};

    #[test]
    fn extract_set_cookie_takes_last_non_empty_ignoring_clearing_cookie() {
        let mut headers = HeaderMap::new();
        // Clearing cookie (value is literally two quotes) comes first…
        headers.append(
            SET_COOKIE,
            HeaderValue::from_static("pld_ut=\"\"; Domain=.plaud.ai; Max-Age=0; Path=/"),
        );
        // …then the real token.
        headers.append(
            SET_COOKIE,
            HeaderValue::from_static("pld_ut=abc.def.ghi; Domain=.plaud.ai; Max-Age=86400"),
        );
        headers.append(SET_COOKIE, HeaderValue::from_static("other=zzz; Path=/"));

        assert_eq!(
            extract_set_cookie(&headers, "pld_ut"),
            Some("abc.def.ghi".to_string())
        );
        assert_eq!(extract_set_cookie(&headers, "pld_urt"), None);
    }

    #[test]
    fn extract_set_cookie_clearing_only_is_none() {
        let mut headers = HeaderMap::new();
        headers.append(SET_COOKIE, HeaderValue::from_static("pld_ut=\"\"; Max-Age=0"));
        assert_eq!(extract_set_cookie(&headers, "pld_ut"), None);
    }

    #[test]
    fn decode_jwt_expiry_reads_iat_and_exp() {
        // Header.{"iat":1000,"exp":2000}.sig — payload base64url of the JSON.
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(br#"{"iat":1000,"exp":2000}"#);
        let jwt = format!("aaa.{payload}.bbb");
        assert_eq!(decode_jwt_expiry(&jwt), Ok((1000, 2000)));
    }

    #[test]
    fn decode_jwt_expiry_rejects_malformed() {
        assert!(decode_jwt_expiry("not-a-jwt").is_err());
    }
}

fn decode_jwt_expiry(jwt: &str) -> Result<(i64, i64), String> {
    let parts: Vec<&str> = jwt.split('.').collect();
    if parts.len() != 3 {
        return Err("Invalid JWT token".into());
    }

    let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(parts[1])
        .or_else(|_| {
            base64::engine::general_purpose::STANDARD
                .decode(parts[1].replace('-', "+").replace('_', "/"))
        })
        .map_err(|_| "Failed to decode JWT payload".to_string())?;

    let json: serde_json::Value =
        serde_json::from_slice(&payload).map_err(|_| "Invalid JWT JSON".to_string())?;

    let iat = json["iat"].as_i64().unwrap_or(0);
    let exp = json["exp"].as_i64().unwrap_or(iat + 86400 * 300);
    Ok((iat, exp))
}