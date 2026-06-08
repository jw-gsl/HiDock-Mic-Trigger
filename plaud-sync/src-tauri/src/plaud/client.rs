use serde_json::Value;

use super::auth::PlaudAuth;
use super::types::{base_url, PlaudRecording, PlaudRecordingDetail, PlaudUserInfo};

pub struct PlaudClient {
    auth: PlaudAuth,
    region: String,
    http: reqwest::Client,
}

impl PlaudClient {
    pub fn new(auth: PlaudAuth, region: String) -> Self {
        Self {
            auth,
            region,
            http: reqwest::Client::new(),
        }
    }

    fn base_url(&self) -> &str {
        base_url(&self.region)
    }

    async fn request(&mut self, path: &str) -> Result<Value, String> {
        let token = self.auth.get_token().await?;
        let url = format!("{}{}", self.base_url(), path);

        let res = self
            .http
            .get(&url)
            .header("Authorization", format!("Bearer {token}"))
            .header("Content-Type", "application/json")
            .send()
            .await
            .map_err(|e| format!("Network error: {e}"))?;

        if !res.status().is_success() {
            return Err(format!("Plaud API error: {}", res.status()));
        }

        let data: Value = res
            .json()
            .await
            .map_err(|e| format!("Invalid API response: {e}"))?;

        if data.get("status").and_then(|s| s.as_i64()) == Some(-302) {
            if let Some(domain) = data
                .pointer("/data/domains/api")
                .and_then(|d| d.as_str())
            {
                self.region = if domain.contains("euc1") {
                    "eu".to_string()
                } else {
                    "us".to_string()
                };
                return Box::pin(self.request(path)).await;
            }
        }

        Ok(data)
    }

    pub async fn list_recordings(&mut self) -> Result<Vec<PlaudRecording>, String> {
        let data = self.request("/file/simple/web").await?;
        let list = data
            .get("data_file_list")
            .or_else(|| data.get("data"))
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        let recordings: Vec<PlaudRecording> = list
            .into_iter()
            .filter(|item| !item.get("is_trash").and_then(|v| v.as_bool()).unwrap_or(false))
            .filter_map(|item| parse_recording(&item))
            .collect();

        Ok(recordings)
    }

    pub async fn get_recording(&mut self, id: &str) -> Result<PlaudRecordingDetail, String> {
        let data = self.request(&format!("/file/detail/{id}")).await?;
        let raw = data.get("data").unwrap_or(&data);

        let mut transcript = String::new();
        if let Some(items) = raw
            .get("pre_download_content_list")
            .and_then(|v| v.as_array())
        {
            for item in items {
                if let Some(content) = item.get("data_content").and_then(|c| c.as_str()) {
                    if content.len() > transcript.len() {
                        transcript = content.to_string();
                    }
                }
            }
        }

        Ok(PlaudRecordingDetail {
            id: raw
                .get("file_id")
                .or_else(|| raw.get("id"))
                .and_then(|v| v.as_str())
                .unwrap_or(id)
                .to_string(),
            filename: raw
                .get("file_name")
                .or_else(|| raw.get("filename"))
                .and_then(|v| v.as_str())
                .unwrap_or(id)
                .to_string(),
            duration: raw.get("duration").and_then(|v| v.as_i64()).unwrap_or(0),
            start_time: raw.get("start_time").and_then(|v| v.as_i64()).unwrap_or(0),
            transcript,
            serial_number: raw
                .get("serial_number")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
        })
    }

    pub async fn get_user_info(&mut self) -> Result<PlaudUserInfo, String> {
        let data = self.request("/user/me").await?;
        let user = data
            .get("data_user")
            .or_else(|| data.get("data"))
            .unwrap_or(&data);

        Ok(PlaudUserInfo {
            email: user
                .get("email")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            nickname: user
                .get("nickname")
                .and_then(|v| v.as_str())
                .unwrap_or("Plaud User")
                .to_string(),
        })
    }

    pub async fn download_audio_bytes(&mut self, id: &str) -> Result<(Vec<u8>, String), String> {
        if let Some(url) = self.get_mp3_url(id).await? {
            let res = self
                .http
                .get(&url)
                .send()
                .await
                .map_err(|e| format!("Download failed: {e}"))?;
            if !res.status().is_success() {
                return Err(format!("Download failed: {}", res.status()));
            }
            let bytes = res
                .bytes()
                .await
                .map_err(|e| format!("Download failed: {e}"))?;
            return Ok((bytes.to_vec(), "mp3".to_string()));
        }

        let token = self.auth.get_token().await?;
        let res = self
            .http
            .get(format!("{}/file/download/{id}", self.base_url()))
            .header("Authorization", format!("Bearer {token}"))
            .send()
            .await
            .map_err(|e| format!("Download failed: {e}"))?;

        if !res.status().is_success() {
            return Err(format!("Download failed: {}", res.status()));
        }

        let bytes = res
            .bytes()
            .await
            .map_err(|e| format!("Download failed: {e}"))?;
        Ok((bytes.to_vec(), "opus".to_string()))
    }

    async fn get_mp3_url(&mut self, id: &str) -> Result<Option<String>, String> {
        let data = self
            .request(&format!("/file/temp-url/{id}?is_opus=false"))
            .await?;
        Ok(data
            .get("url")
            .or_else(|| data.pointer("/data/url"))
            .or_else(|| data.get("data").filter(|v| v.is_string()))
            .or_else(|| data.get("temp_url"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()))
    }
}

fn parse_recording(item: &Value) -> Option<PlaudRecording> {
    let id = item
        .get("id")
        .or_else(|| item.get("file_id"))
        .and_then(|v| v.as_str())?
        .to_string();

    let filename = item
        .get("filename")
        .or_else(|| item.get("file_name"))
        .and_then(|v| v.as_str())
        .unwrap_or(&id)
        .to_string();

    Some(PlaudRecording {
        id,
        filename,
        duration: item.get("duration").and_then(|v| v.as_i64()).unwrap_or(0),
        start_time: item.get("start_time").and_then(|v| v.as_i64()).unwrap_or(0),
        is_trans: item.get("is_trans").and_then(|v| v.as_bool()).unwrap_or(false),
        serial_number: item
            .get("serial_number")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        downloaded: false,
    })
}