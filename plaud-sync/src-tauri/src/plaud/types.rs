use serde::{Deserialize, Serialize};

/// Refresh/re-login when the token is within this window of expiry. Kept small
/// because SSO `pld_ut` tokens only live 24h (a large buffer would mark them
/// perpetually "expiring"); email-login tokens live far longer and are unaffected.
pub const TOKEN_REFRESH_BUFFER_MS: i64 = 5 * 60 * 1000;

/// Browser-shaped User-Agent. Plaud's API sits behind anti-bot filtering that
/// is friendlier to requests that look like the web app, so our bare HTTP calls
/// (list/download/refresh) advertise the same UA web.plaud.ai uses.
pub const PLAUD_USER_AGENT: &str =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36";

/// Resolve a region value to its API base URL. `region` is normally one of the
/// known keys (`us`/`eu`/`apac`), but a region redirect may have stored a full
/// `https://…plaud.ai` base — in which case we trust it verbatim (after a host
/// check) so a region Plaud adds later works without a code change.
pub fn base_url(region: &str) -> String {
    if region.starts_with("http") {
        if is_valid_plaud_api_url(region) {
            return region.trim_end_matches('/').to_string();
        }
        // Anything that isn't a valid Plaud host falls through to the default.
    }
    match region {
        "eu" => "https://api-euc1.plaud.ai".to_string(),
        "apac" | "apse1" => "https://api-apse1.plaud.ai".to_string(),
        _ => "https://api.plaud.ai".to_string(),
    }
}

/// HTTPS + `*.plaud.ai` host check. Mirrors the guard Riffado applies before
/// trusting a redirect-supplied API base, so a malformed or hostile
/// `data.domains.api` can never point us at an arbitrary server.
pub fn is_valid_plaud_api_url(url: &str) -> bool {
    let authority = match url.strip_prefix("https://") {
        Some(rest) => rest.split(['/', '?', '#']).next().unwrap_or(""),
        None => return false,
    };
    // Drop any userinfo (`user@`) and port (`:443`) to isolate the host.
    let host = authority.rsplit('@').next().unwrap_or(authority);
    let host = host.split(':').next().unwrap_or(host);
    host == "plaud.ai" || host.ends_with(".plaud.ai")
}

/// Resolve a region-redirect `data.domains.api` value into the region string we
/// persist: a friendly key for the three known hosts, or the full (validated)
/// API base for any other Plaud region. Returns `None` if the host isn't a
/// valid Plaud API URL, so callers can ignore a bogus redirect.
pub fn region_from_redirect(api: &str) -> Option<String> {
    if !is_valid_plaud_api_url(api) {
        return None;
    }
    let normalized = api.trim_end_matches('/');
    if normalized.contains("euc1") {
        Some("eu".to_string())
    } else if normalized.contains("apse1") {
        Some("apac".to_string())
    } else if normalized.contains("api.plaud.ai") {
        Some("us".to_string())
    } else {
        // A valid but unfamiliar region — keep the full base URL so we talk to
        // the right host without needing a new key.
        Some(normalized.to_string())
    }
}

/// Layer browser-shaped headers onto a Plaud API request so bare programmatic
/// calls resemble the web app. Callers add `Authorization`/`Content-Type` after
/// this; those win on conflict. Deliberately omits `Accept-Encoding` — reqwest
/// negotiates and decodes compression itself.
pub fn browser_headers(req: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
    req.header("User-Agent", PLAUD_USER_AGENT)
        .header("Origin", "https://web.plaud.ai")
        .header("Referer", "https://web.plaud.ai/")
        .header("Accept", "application/json, text/plain, */*")
        .header("Accept-Language", "en-US,en;q=0.9")
        .header(
            "sec-ch-ua",
            "\"Google Chrome\";v=\"142\", \"Chromium\";v=\"142\", \"Not?A_Brand\";v=\"24\"",
        )
        .header("sec-ch-ua-mobile", "?0")
        .header("sec-ch-ua-platform", "\"Windows\"")
        .header("sec-fetch-site", "same-site")
        .header("sec-fetch-mode", "cors")
        .header("sec-fetch-dest", "empty")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlaudCredentials {
    pub email: String,
    pub region: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlaudTokenData {
    pub access_token: String,
    pub token_type: String,
    pub issued_at: i64,
    pub expires_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlaudRecording {
    pub id: String,
    pub filename: String,
    pub duration: i64,
    pub start_time: i64,
    pub is_trans: bool,
    pub serial_number: String,
    #[serde(default)]
    pub downloaded: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlaudRecordingDetail {
    pub id: String,
    pub filename: String,
    pub duration: i64,
    pub start_time: i64,
    pub transcript: String,
    pub serial_number: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlaudUserInfo {
    pub email: String,
    pub nickname: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base_url_maps_known_region_keys() {
        assert_eq!(base_url("us"), "https://api.plaud.ai");
        assert_eq!(base_url("eu"), "https://api-euc1.plaud.ai");
        assert_eq!(base_url("apac"), "https://api-apse1.plaud.ai");
        // Unknown key falls back to the global host.
        assert_eq!(base_url("zz"), "https://api.plaud.ai");
    }

    #[test]
    fn base_url_trusts_a_valid_stored_full_host() {
        assert_eq!(
            base_url("https://api-apse1.plaud.ai/"),
            "https://api-apse1.plaud.ai"
        );
        // A non-plaud host stored as the "region" is rejected → default.
        assert_eq!(base_url("https://evil.example.com"), "https://api.plaud.ai");
    }

    #[test]
    fn valid_plaud_api_url_accepts_only_https_plaud_hosts() {
        assert!(is_valid_plaud_api_url("https://api.plaud.ai"));
        assert!(is_valid_plaud_api_url("https://api-apse1.plaud.ai/path"));
        assert!(is_valid_plaud_api_url("https://plaud.ai"));
        assert!(!is_valid_plaud_api_url("http://api.plaud.ai")); // not https
        assert!(!is_valid_plaud_api_url("https://plaud.ai.evil.com")); // suffix trick
        assert!(!is_valid_plaud_api_url("https://evil.com@plaud.ai.evil.com"));
        assert!(!is_valid_plaud_api_url("not-a-url"));
    }

    #[test]
    fn region_from_redirect_resolves_known_hosts_to_keys() {
        assert_eq!(
            region_from_redirect("https://api-euc1.plaud.ai"),
            Some("eu".to_string())
        );
        assert_eq!(
            region_from_redirect("https://api-apse1.plaud.ai"),
            Some("apac".to_string())
        );
        assert_eq!(
            region_from_redirect("https://api.plaud.ai/"),
            Some("us".to_string())
        );
    }

    #[test]
    fn region_from_redirect_keeps_full_url_for_unknown_region() {
        assert_eq!(
            region_from_redirect("https://api-usw2.plaud.ai"),
            Some("https://api-usw2.plaud.ai".to_string())
        );
    }

    #[test]
    fn region_from_redirect_rejects_non_plaud_hosts() {
        assert_eq!(region_from_redirect("https://evil.example.com"), None);
        assert_eq!(region_from_redirect("http://api.plaud.ai"), None);
    }
}