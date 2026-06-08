use std::fs;
use std::path::PathBuf;

use keyring::Entry;
use serde::{Deserialize, Serialize};

use crate::plaud::types::{PlaudCredentials, PlaudRecording, PlaudTokenData};

const SERVICE_NAME: &str = "com.jameswhiting.plaud-sync";
const PASSWORD_ACCOUNT: &str = "plaud-password";
const REFRESH_TOKEN_ACCOUNT: &str = "plaud-refresh-token";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub download_dir: String,
    pub folder_structure: String,
    pub custom_prefix: String,
    pub filename_style: String,
    pub create_info_txt: bool,
    pub download_transcript: bool,
    #[serde(default)]
    pub auto_sync: bool,
    #[serde(default = "default_auto_sync_minutes")]
    pub auto_sync_minutes: u32,
    #[serde(default = "default_theme")]
    pub theme: String,
    #[serde(default)]
    pub start_minimized: bool,
}

fn default_auto_sync_minutes() -> u32 {
    15
}

fn default_theme() -> String {
    "system".to_string()
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            download_dir: default_download_dir(),
            folder_structure: "by_date".to_string(),
            custom_prefix: "PlaudRecordings".to_string(),
            filename_style: "clean".to_string(),
            create_info_txt: true,
            download_transcript: true,
            auto_sync: false,
            auto_sync_minutes: default_auto_sync_minutes(),
            theme: default_theme(),
            start_minimized: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct StoredConfig {
    credentials: Option<PlaudCredentials>,
    token: Option<PlaudTokenData>,
    settings: Option<AppSettings>,
    #[serde(default)]
    display_name: Option<String>,
}

#[derive(Clone)]
pub struct Storage {
    config_path: PathBuf,
}

impl Storage {
    pub fn new(app_data_dir: PathBuf) -> Result<Self, std::io::Error> {
        fs::create_dir_all(&app_data_dir)?;
        Ok(Self {
            config_path: app_data_dir.join("config.json"),
        })
    }

    fn load(&self) -> StoredConfig {
        match fs::read_to_string(&self.config_path) {
            Ok(raw) => serde_json::from_str(&raw).unwrap_or_default(),
            Err(_) => StoredConfig::default(),
        }
    }

    fn save(&self, config: &StoredConfig) -> Result<(), std::io::Error> {
        let raw = serde_json::to_string_pretty(config).map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, e.to_string())
        })?;
        fs::write(&self.config_path, raw)
    }

    pub fn get_credentials(&self) -> Option<PlaudCredentials> {
        self.load().credentials
    }

    pub fn save_credentials(&self, email: &str, region: &str) -> Result<(), std::io::Error> {
        let mut config = self.load();
        config.credentials = Some(PlaudCredentials {
            email: email.to_string(),
            region: region.to_string(),
        });
        self.save(&config)
    }

    pub fn save_password(&self, password: &str) -> Result<(), Box<dyn std::error::Error>> {
        let entry = Entry::new(SERVICE_NAME, PASSWORD_ACCOUNT)?;
        entry.set_password(password)?;
        Ok(())
    }

    pub fn get_password(&self) -> Result<Option<String>, Box<dyn std::error::Error>> {
        let entry = Entry::new(SERVICE_NAME, PASSWORD_ACCOUNT)?;
        match entry.get_password() {
            Ok(password) => Ok(Some(password)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    pub fn save_refresh_token(&self, token: &str) -> Result<(), Box<dyn std::error::Error>> {
        let entry = Entry::new(SERVICE_NAME, REFRESH_TOKEN_ACCOUNT)?;
        entry.set_password(token)?;
        Ok(())
    }

    pub fn get_refresh_token(&self) -> Result<Option<String>, Box<dyn std::error::Error>> {
        let entry = Entry::new(SERVICE_NAME, REFRESH_TOKEN_ACCOUNT)?;
        match entry.get_password() {
            Ok(token) => Ok(Some(token)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    pub fn get_token(&self) -> Option<PlaudTokenData> {
        self.load().token
    }

    pub fn save_token(&self, token: &PlaudTokenData) -> Result<(), std::io::Error> {
        let mut config = self.load();
        config.token = Some(token.clone());
        self.save(&config)
    }

    pub fn get_display_name(&self) -> Option<String> {
        self.load().display_name
    }

    pub fn save_display_name(&self, name: &str) -> Result<(), std::io::Error> {
        let mut config = self.load();
        config.display_name = Some(name.to_string());
        self.save(&config)
    }

    fn cache_path(&self) -> PathBuf {
        self.config_path.with_file_name("recordings.json")
    }

    /// Cached recordings list (metadata only; downloaded state is re-derived
    /// from disk on read). Lets the UI render instantly and survive a failed
    /// or offline refresh.
    pub fn save_recordings_cache(&self, recordings: &[PlaudRecording]) -> Result<(), std::io::Error> {
        let raw = serde_json::to_string(recordings).map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, e.to_string())
        })?;
        fs::write(self.cache_path(), raw)
    }

    pub fn get_recordings_cache(&self) -> Vec<PlaudRecording> {
        match fs::read_to_string(self.cache_path()) {
            Ok(raw) => serde_json::from_str(&raw).unwrap_or_default(),
            Err(_) => Vec::new(),
        }
    }

    pub fn get_settings(&self) -> AppSettings {
        self.load().settings.unwrap_or_default()
    }

    pub fn save_settings(&self, settings: &AppSettings) -> Result<(), std::io::Error> {
        let mut config = self.load();
        config.settings = Some(settings.clone());
        self.save(&config)
    }

    pub fn clear_auth(&self) -> Result<(), std::io::Error> {
        let mut config = self.load();
        config.credentials = None;
        config.token = None;
        config.display_name = None;
        self.save(&config)?;

        if let Ok(entry) = Entry::new(SERVICE_NAME, PASSWORD_ACCOUNT) {
            let _ = entry.delete_credential();
        }
        if let Ok(entry) = Entry::new(SERVICE_NAME, REFRESH_TOKEN_ACCOUNT) {
            let _ = entry.delete_credential();
        }

        Ok(())
    }

    pub fn is_logged_in(&self) -> bool {
        self.get_token().is_some()
    }

    pub fn get_region(&self) -> String {
        self.get_credentials()
            .map(|c| c.region)
            .unwrap_or_else(|| "us".to_string())
    }
}

fn default_download_dir() -> String {
    dirs::document_dir()
        .or_else(dirs::home_dir)
        .map(|p| p.join("PlaudRecordings").to_string_lossy().to_string())
        .unwrap_or_else(|| "PlaudRecordings".to_string())
}