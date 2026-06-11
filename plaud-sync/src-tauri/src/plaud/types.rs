use serde::{Deserialize, Serialize};

/// Refresh/re-login when the token is within this window of expiry. Kept small
/// because SSO `pld_ut` tokens only live 24h (a large buffer would mark them
/// perpetually "expiring"); email-login tokens live far longer and are unaffected.
pub const TOKEN_REFRESH_BUFFER_MS: i64 = 5 * 60 * 1000;

pub fn base_url(region: &str) -> &'static str {
    match region {
        "eu" => "https://api-euc1.plaud.ai",
        _ => "https://api.plaud.ai",
    }
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