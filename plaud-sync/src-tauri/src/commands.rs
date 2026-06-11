use tauri::{AppHandle, State};
use tauri_plugin_dialog::DialogExt;

use crate::browser_login;
use crate::plaud::{PlaudAuth, PlaudClient};
use crate::plaud::types::PlaudRecording;
use crate::state::AppState;
use crate::storage::AppSettings;
use crate::sync::{example_path, mark_downloaded_status, sync_recordings};

pub use crate::app_types::AuthStatus;

#[tauri::command]
pub async fn get_auth_status(state: State<'_, AppState>) -> Result<AuthStatus, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?.clone();
    let creds = storage.get_credentials();
    let logged_in = storage.is_logged_in();
    let mut name = storage.get_display_name();

    // Backfill the display name (real nickname from /user/me) for sessions that
    // were created before we started capturing it. Best-effort — if the call
    // fails (offline), we just return without a name.
    if logged_in && name.is_none() {
        let region = storage.get_region();
        let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), region);
        if let Ok(user) = client.get_user_info().await {
            if !user.nickname.is_empty() {
                let _ = storage.save_display_name(&user.nickname);
                name = Some(user.nickname);
            }
        }
    }

    Ok(AuthStatus {
        logged_in,
        email: creds.as_ref().map(|c| c.email.clone()),
        region: creds.map(|c| c.region),
        name,
    })
}

// The account's region is detected automatically (US/EU), so the UI no longer
// asks for it. Each path starts at "us" and the backend corrects as needed.
const DEFAULT_REGION: &str = "us";

#[tauri::command]
pub async fn login_with_email(
    email: String,
    password: String,
    state: State<'_, AppState>,
) -> Result<AuthStatus, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?.clone();
    let auth = PlaudAuth::new(storage.clone());
    // Returns the region the account actually belongs to (auto-retried on
    // mismatch), so build the client against that.
    let region = auth
        .login_with_credentials(&email, &password, DEFAULT_REGION)
        .await?;

    let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), region.clone());
    let user = client.get_user_info().await?;
    if !user.nickname.is_empty() {
        let _ = storage.save_display_name(&user.nickname);
    }

    let creds = storage.get_credentials();
    Ok(AuthStatus {
        logged_in: true,
        email: creds.as_ref().map(|c| c.email.clone()),
        region: Some(region),
        name: storage.get_display_name(),
    })
}

#[tauri::command]
pub async fn login_with_browser(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<AuthStatus, String> {
    browser_login::login_with_browser(&app, DEFAULT_REGION, state).await
}

#[tauri::command]
pub async fn login_with_token(
    token: String,
    state: State<'_, AppState>,
) -> Result<AuthStatus, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?.clone();
    let token = token.trim();

    // A pasted JWT carries no region, so validate it against each region and
    // keep the one whose API accepts it.
    let mut last_err = "Token did not validate.".to_string();
    for region in ["us", "eu"] {
        let auth = PlaudAuth::new(storage.clone());
        // A decode failure is region-independent — fail fast.
        auth.login_with_jwt(token, region)?;

        let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), region.to_string());
        match client.get_user_info().await {
            Ok(user) => {
                if !user.nickname.is_empty() {
                    let _ = storage.save_display_name(&user.nickname);
                }
                let creds = storage.get_credentials();
                return Ok(AuthStatus {
                    logged_in: true,
                    email: creds.as_ref().map(|c| c.email.clone()),
                    region: Some(region.to_string()),
                    name: storage.get_display_name(),
                });
            }
            Err(e) => last_err = e,
        }
    }
    Err(format!(
        "Could not validate this token in the US or EU region. {last_err}"
    ))
}

#[tauri::command]
pub fn logout(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    browser_login::close_login_window(&app);
    let storage = state.storage.lock().map_err(|e| e.to_string())?;
    storage.clear_auth().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn list_recordings(state: State<'_, AppState>) -> Result<Vec<PlaudRecording>, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?.clone();
    let settings = storage.get_settings();
    let region = storage.get_region();
    let auth = PlaudAuth::new(storage.clone());
    let mut client = PlaudClient::new(auth, region);

    let mut recordings = client.list_recordings().await?;
    mark_downloaded_status(&mut recordings, &settings);
    // Cache the fresh list so the UI can render instantly next time / offline.
    let _ = storage.save_recordings_cache(&recordings);
    Ok(recordings)
}

/// Instant, network-free recordings list from the local cache, with downloaded
/// state re-derived from disk. Empty on first run (before any successful fetch).
#[tauri::command]
pub fn get_cached_recordings(state: State<'_, AppState>) -> Result<Vec<PlaudRecording>, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?;
    let settings = storage.get_settings();
    let mut recordings = storage.get_recordings_cache();
    mark_downloaded_status(&mut recordings, &settings);
    Ok(recordings)
}

#[tauri::command]
pub async fn sync_now(app: AppHandle, state: State<'_, AppState>) -> Result<crate::sync::SyncResult, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?.clone();
    let settings = storage.get_settings();

    if settings.download_dir.trim().is_empty() {
        return Err("Please choose a save folder in Settings first.".into());
    }

    let result = sync_recordings(&app, &storage, &settings).await?;
    state
        .last_sync_epoch
        .store(crate::state::now_epoch(), std::sync::atomic::Ordering::Relaxed);
    Ok(result)
}

#[tauri::command]
pub async fn download_selected(
    app: AppHandle,
    ids: Vec<String>,
    state: State<'_, AppState>,
) -> Result<crate::sync::SyncResult, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?.clone();
    let settings = storage.get_settings();

    if settings.download_dir.trim().is_empty() {
        return Err("Please choose a save folder in Settings first.".into());
    }

    let result = crate::sync::download_selected(&app, &storage, &settings, &ids).await?;
    state
        .last_sync_epoch
        .store(crate::state::now_epoch(), std::sync::atomic::Ordering::Relaxed);
    Ok(result)
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncInfo {
    pub auto_sync: bool,
    pub interval_minutes: u32,
    pub seconds_until_next: Option<i64>,
}

#[tauri::command]
pub fn get_sync_info(state: State<'_, AppState>) -> Result<SyncInfo, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?;
    let settings = storage.get_settings();
    let last = state
        .last_sync_epoch
        .load(std::sync::atomic::Ordering::Relaxed);
    // Auto-sync now checks every tick (see `sync::AUTO_SYNC_TICK_SECS`), not on
    // the legacy per-minutes interval, so the countdown reflects the real ~60s
    // cadence rather than `auto_sync_minutes`.
    let seconds_until_next = if settings.auto_sync {
        let interval = crate::sync::AUTO_SYNC_TICK_SECS as i64;
        Some((last + interval - crate::state::now_epoch()).max(0))
    } else {
        None
    };
    Ok(SyncInfo {
        auto_sync: settings.auto_sync,
        interval_minutes: settings.auto_sync_minutes,
        seconds_until_next,
    })
}

#[tauri::command]
pub fn get_settings(state: State<'_, AppState>) -> Result<AppSettings, String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?;
    Ok(storage.get_settings())
}

#[tauri::command]
pub fn save_settings(settings: AppSettings, state: State<'_, AppState>) -> Result<(), String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?;
    let was_auto = storage.get_settings().auto_sync;
    storage.save_settings(&settings).map_err(|e| e.to_string())?;

    // Turning auto-sync ON should pull everything not yet saved promptly rather
    // than waiting a full interval — force the loop to run on its next (~60s)
    // tick by resetting the "last sync" stamp.
    if settings.auto_sync && !was_auto {
        state
            .last_sync_epoch
            .store(0, std::sync::atomic::Ordering::Relaxed);
    }
    Ok(())
}

#[tauri::command]
pub fn get_path_example(settings: AppSettings) -> String {
    example_path(&settings)
}

#[tauri::command]
pub async fn pick_download_folder(app: AppHandle, state: State<'_, AppState>) -> Result<Option<String>, String> {
    let folder = app
        .dialog()
        .file()
        .set_title("Choose save folder")
        .blocking_pick_folder();

    if let Some(path) = folder {
        let path_str = path.to_string();
        let storage = state.storage.lock().map_err(|e| e.to_string())?;
        let mut settings = storage.get_settings();
        settings.download_dir = path_str.clone();
        storage.save_settings(&settings).map_err(|e| e.to_string())?;
        return Ok(Some(path_str));
    }

    Ok(None)
}

#[tauri::command]
pub fn open_download_folder(state: State<'_, AppState>) -> Result<(), String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?;
    let settings = storage.get_settings();
    open::that(&settings.download_dir).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn open_login_debug_log() -> Result<(), String> {
    crate::browser_login::open_debug_log()
}

#[tauri::command]
pub fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    use tauri_plugin_autostart::ManagerExt;
    let manager = app.autolaunch();
    if enabled {
        manager.enable().map_err(|e| e.to_string())
    } else {
        manager.disable().map_err(|e| e.to_string())
    }
}

#[tauri::command]
pub fn get_autostart(app: AppHandle) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

/// Reveal a recording's downloaded file in the OS file manager (Finder/Explorer).
/// Falls back to opening the folder it would live in if not downloaded yet.
#[tauri::command]
pub fn reveal_recording(
    recording: PlaudRecording,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let storage = state.storage.lock().map_err(|e| e.to_string())?;
    let settings = storage.get_settings();
    let root = std::path::PathBuf::from(&settings.download_dir);
    let base = crate::sync::build_audio_path(&root, &recording, &settings);

    // `base` ends in .mp3; the actual file may be .opus.
    let file = [base.clone(), base.with_extension("opus")]
        .into_iter()
        .find(|p| p.exists());

    match file {
        Some(path) => reveal_in_file_manager(&path),
        None => {
            let dir = base.parent().map(|p| p.to_path_buf()).unwrap_or(root);
            std::fs::create_dir_all(&dir).ok();
            open::that(dir).map_err(|e| e.to_string())
        }
    }
}

fn reveal_in_file_manager(path: &std::path::Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg("-R")
            .arg(path)
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(format!("/select,{}", path.display()))
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let dir = path.parent().unwrap_or(path);
        open::that(dir).map_err(|e| e.to_string())
    }
}