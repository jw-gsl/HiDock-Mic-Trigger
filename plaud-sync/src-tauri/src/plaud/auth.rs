use base64::Engine;
use chrono::Utc;

use super::types::{base_url, LoginResponse, PlaudTokenData, TOKEN_REFRESH_BUFFER_MS};
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

    pub async fn login_with_credentials(
        &self,
        email: &str,
        password: &str,
        region: &str,
    ) -> Result<PlaudTokenData, String> {
        self.storage
            .save_credentials(email, region)
            .map_err(|e| e.to_string())?;
        self.storage
            .save_password(password)
            .map_err(|e| e.to_string())?;

        self.login_with_stored_credentials().await
    }

    pub async fn login_with_stored_credentials(&self) -> Result<PlaudTokenData, String> {
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
            .post(format!("{}/auth/access-token", base_url(&creds.region)))
            .form(&[
                ("username", creds.email.as_str()),
                ("password", password.as_str()),
            ])
            .send()
            .await
            .map_err(|e| format!("Network error: {e}"))?;

        let data: LoginResponse = res
            .json()
            .await
            .map_err(|e| format!("Invalid login response: {e}"))?;

        if data.status != 0 {
            return Err(data.msg.unwrap_or_else(|| "Login failed".to_string()));
        }

        let access_token = data
            .access_token
            .ok_or_else(|| "No access token in response".to_string())?;

        let token = build_token_data(&access_token, data.token_type.as_deref())?;
        self.storage
            .save_token(&token)
            .map_err(|e| e.to_string())?;
        Ok(token)
    }

    /// Google (and other web SSO) sign-in. Exchanges a Google `id_token` for a
    /// Plaud session via `/auth/sso-callback`, which returns the user token
    /// (`pld_ut`) and refresh token (`pld_urt`) as Set-Cookie headers. We use
    /// `pld_ut` as a normal Bearer token (verified against the live API).
    pub async fn login_with_google_sso(
        &self,
        id_token: &str,
        user_area: &str,
        region: &str,
    ) -> Result<PlaudTokenData, String> {
        let client = reqwest::Client::new();
        let res = client
            .post(format!("{}/auth/sso-callback", base_url(region)))
            .header("app-platform", "web")
            .json(&serde_json::json!({
                "sso_from": "web",
                "sso_type": "google",
                "id_token": id_token,
                "user_area": user_area,
            }))
            .send()
            .await
            .map_err(|e| format!("Network error: {e}"))?;

        if !res.status().is_success() {
            return Err(format!("Google sign-in failed: {}", res.status()));
        }

        let user_token = extract_set_cookie(res.headers(), "pld_ut")
            .ok_or_else(|| "Google sign-in did not return a session token.".to_string())?;
        let refresh_token = extract_set_cookie(res.headers(), "pld_urt");

        let token = build_token_data(&user_token, Some("Bearer"))?;
        self.storage
            .save_credentials("google-sso", region)
            .map_err(|e| e.to_string())?;
        self.storage.save_token(&token).map_err(|e| e.to_string())?;
        if let Some(refresh_token) = refresh_token {
            self.storage
                .save_refresh_token(&refresh_token)
                .map_err(|e| e.to_string())?;
        }
        Ok(token)
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