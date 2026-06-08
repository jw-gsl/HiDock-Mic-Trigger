mod app_types;
mod browser_login;
mod commands;
mod login_log;
mod plaud;
mod state;
mod storage;
mod sync;

use state::AppState;
use storage::Storage;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .setup(|app| {
            let app_data_dir = app
                .path()
                .app_data_dir()
                .expect("failed to resolve app data directory");

            login_log::init(app_data_dir.clone());

            let storage = Storage::new(app_data_dir).expect("failed to initialize storage");
            app.manage(AppState {
                storage: std::sync::Mutex::new(storage),
                browser_login_tx: std::sync::Mutex::new(None),
                // Seed to 0 so that if auto-sync is already enabled, the loop
                // downloads any new recordings within ~60s of launch instead of
                // waiting a full interval.
                last_sync_epoch: std::sync::atomic::AtomicI64::new(0),
            });

            let sync_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                sync::auto_sync_loop(sync_handle).await;
            });

            // Start minimized if the user wants it to run as a background tool.
            let start_minimized = {
                let state = app.state::<AppState>();
                state
                    .storage
                    .lock()
                    .map(|s| s.get_settings().start_minimized)
                    .unwrap_or(false)
            };
            if start_minimized {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.minimize();
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_auth_status,
            commands::login_with_browser,
            commands::login_with_email,
            commands::login_with_token,
            commands::logout,
            commands::list_recordings,
            commands::sync_now,
            commands::get_settings,
            commands::save_settings,
            commands::get_path_example,
            commands::pick_download_folder,
            commands::open_download_folder,
            commands::open_login_debug_log,
            commands::reveal_recording,
            commands::get_sync_info,
            commands::download_selected,
            commands::get_cached_recordings,
            commands::set_autostart,
            commands::get_autostart,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}