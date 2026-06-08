use std::fs;
use std::path::{Path, PathBuf};

use chrono::{TimeZone, Utc};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::plaud::{PlaudAuth, PlaudClient};
use crate::plaud::types::PlaudRecording;
use crate::state::AppState;
use crate::storage::{AppSettings, Storage};

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncProgress {
    pub current: usize,
    pub total: usize,
    pub message: String,
    pub filename: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncResult {
    pub downloaded: usize,
    pub skipped: usize,
    pub failed: usize,
    pub total: usize,
    pub message: String,
}

/// Download every recording in the account that isn't already on disk.
pub async fn sync_recordings(
    app: &AppHandle,
    storage: &Storage,
    settings: &AppSettings,
) -> Result<SyncResult, String> {
    let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), storage.get_region());
    let recordings = client.list_recordings().await?;
    download_list(app, &mut client, &recordings, settings).await
}

/// Download only the recordings whose ids are in `ids` (manual selection).
pub async fn download_selected(
    app: &AppHandle,
    storage: &Storage,
    settings: &AppSettings,
    ids: &[String],
) -> Result<SyncResult, String> {
    let mut client = PlaudClient::new(PlaudAuth::new(storage.clone()), storage.get_region());
    let all = client.list_recordings().await?;
    let subset: Vec<PlaudRecording> = all
        .into_iter()
        .filter(|r| ids.iter().any(|id| id == &r.id))
        .collect();
    download_list(app, &mut client, &subset, settings).await
}

/// Shared download loop. A failure on a single recording is non-fatal: it's
/// logged and counted, and the loop carries on (so one bad/processing recording
/// can't block the rest or abort an auto-sync silently).
async fn download_list(
    app: &AppHandle,
    client: &mut PlaudClient,
    recordings: &[PlaudRecording],
    settings: &AppSettings,
) -> Result<SyncResult, String> {
    let total = recordings.len();
    let download_root = PathBuf::from(&settings.download_dir);
    fs::create_dir_all(&download_root).map_err(|e| e.to_string())?;

    let mut downloaded = 0usize;
    let mut skipped = 0usize;
    let mut failed = 0usize;

    for (index, recording) in recordings.iter().enumerate() {
        let _ = app.emit(
            "sync-progress",
            SyncProgress {
                current: index + 1,
                total,
                message: format!("Checking {}...", recording.filename),
                filename: recording.filename.clone(),
            },
        );

        // `build_audio_path` returns a `.mp3` base; the file may end up `.opus`.
        let audio_path = build_audio_path(&download_root, recording, settings);
        if audio_path.exists() || audio_path.with_extension("opus").exists() {
            skipped += 1;
            continue;
        }

        let _ = app.emit(
            "sync-progress",
            SyncProgress {
                current: index + 1,
                total,
                message: format!("Downloading {}...", recording.filename),
                filename: recording.filename.clone(),
            },
        );

        match download_one(client, recording, settings, &audio_path).await {
            Ok(()) => downloaded += 1,
            Err(e) => {
                failed += 1;
                crate::login_log::warn(&format!(
                    "download failed for \"{}\" (id {}): {e}",
                    recording.filename, recording.id
                ));
            }
        }
    }

    let message = if failed > 0 {
        format!("Downloaded {downloaded}, {skipped} already saved, {failed} failed (see debug log).")
    } else if downloaded > 0 {
        format!("Downloaded {downloaded} file(s). {skipped} already on disk.")
    } else if skipped > 0 {
        "Already up to date — all recordings are downloaded.".to_string()
    } else {
        "No recordings found in your Plaud account.".to_string()
    };

    Ok(SyncResult {
        downloaded,
        skipped,
        failed,
        total,
        message,
    })
}

async fn download_one(
    client: &mut PlaudClient,
    recording: &PlaudRecording,
    settings: &AppSettings,
    audio_path: &Path,
) -> Result<(), String> {
    if let Some(parent) = audio_path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }

    let (bytes, ext) = client.download_audio_bytes(&recording.id).await?;
    if bytes.is_empty() {
        return Err("server returned an empty file (recording may still be processing)".into());
    }
    // Honour the extension the API actually served (mp3 or opus).
    let final_path = audio_path.with_extension(&ext);
    fs::write(&final_path, &bytes).map_err(|e| e.to_string())?;

    if settings.download_transcript && recording.is_trans {
        let detail = client.get_recording(&recording.id).await?;
        if !detail.transcript.is_empty() {
            let transcript_path = final_path.with_extension("txt");
            let content = if settings.create_info_txt {
                build_info_file(
                    &detail.filename,
                    detail.start_time,
                    detail.duration,
                    &detail.transcript,
                )
            } else {
                detail.transcript
            };
            fs::write(&transcript_path, content).map_err(|e| e.to_string())?;
        }
    } else if settings.create_info_txt {
        let info_path = final_path.with_extension("txt");
        let content = build_info_file(
            &recording.filename,
            recording.start_time,
            recording.duration,
            "",
        );
        fs::write(&info_path, content).map_err(|e| e.to_string())?;
    }

    Ok(())
}

/// Background loop: when auto-sync is enabled, download new recordings on the
/// configured interval. Re-reads settings every tick so toggling the setting or
/// changing the interval takes effect without a restart.
pub async fn auto_sync_loop(app: AppHandle) {
    use std::sync::atomic::Ordering;

    loop {
        tokio::time::sleep(std::time::Duration::from_secs(60)).await;

        let (storage, settings, logged_in, last) = {
            let state = app.state::<AppState>();
            let Ok(guard) = state.storage.lock() else {
                continue;
            };
            let storage = guard.clone();
            let settings = storage.get_settings();
            let logged_in = storage.is_logged_in();
            let last = state.last_sync_epoch.load(Ordering::Relaxed);
            (storage, settings, logged_in, last)
        };

        if !settings.auto_sync {
            continue; // toggle off — nothing to do (quiet)
        }
        if !logged_in || settings.download_dir.trim().is_empty() {
            crate::login_log::debug(&format!(
                "auto-sync on but waiting: logged_in={logged_in}, dir_set={}",
                !settings.download_dir.trim().is_empty()
            ));
            continue;
        }

        let interval_secs = i64::from(settings.auto_sync_minutes.max(1)) * 60;
        let due_in = last + interval_secs - crate::state::now_epoch();
        if due_in > 0 {
            crate::login_log::debug(&format!("auto-sync: next download in {due_in}s"));
            continue;
        }

        crate::login_log::info("auto-sync: downloading new recordings…");
        match sync_recordings(&app, &storage, &settings).await {
            Ok(result) => {
                app.state::<AppState>()
                    .last_sync_epoch
                    .store(crate::state::now_epoch(), Ordering::Relaxed);
                crate::login_log::info(&format!(
                    "auto-sync complete: {} downloaded, {} skipped, {} failed",
                    result.downloaded, result.skipped, result.failed
                ));
                let _ = app.emit("auto-sync-complete", result);
            }
            Err(e) => {
                crate::login_log::error(&format!("auto-sync failed: {e}"));
                let _ = app.emit("auto-sync-error", e);
            }
        }
    }
}

pub fn mark_downloaded_status(
    recordings: &mut [PlaudRecording],
    settings: &AppSettings,
) {
    let root = PathBuf::from(&settings.download_dir);
    for rec in recordings.iter_mut() {
        let path = build_audio_path(&root, rec, settings);
        rec.downloaded = path.exists()
            || path.with_extension("mp3").exists()
            || path.with_extension("opus").exists();
    }
}

pub fn build_audio_path(
    root: &Path,
    recording: &PlaudRecording,
    settings: &AppSettings,
) -> PathBuf {
    let date = format_date(recording.start_time);
    let filename = build_filename(recording, settings);
    let prefix = sanitize_folder_name(&settings.custom_prefix);

    match settings.folder_structure.as_str() {
        "flat" => root.join(&prefix).join(&filename).with_extension("mp3"),
        "by_date_device" => {
            let device = device_folder_name(&recording.serial_number);
            root.join(&prefix)
                .join(&date)
                .join(&device)
                .join(&filename)
                .with_extension("mp3")
        }
        "custom_prefix" => root
            .join(&prefix)
            .join(&date)
            .join(&filename)
            .with_extension("mp3"),
        _ => root
            .join(&prefix)
            .join(&date)
            .join(&filename)
            .with_extension("mp3"),
    }
}

pub fn example_path(settings: &AppSettings) -> String {
    let sample = PlaudRecording {
        id: "sample".into(),
        filename: "Team Standup".into(),
        duration: 1_800_000,
        start_time: Utc.with_ymd_and_hms(2025, 6, 8, 10, 0, 0).unwrap().timestamp_millis(),
        is_trans: true,
        serial_number: "NOTE-PRO-001".into(),
        downloaded: false,
    };
    build_audio_path(Path::new(settings.download_dir.as_str()), &sample, settings)
        .to_string_lossy()
        .replace('\\', "/")
}

fn build_filename(recording: &PlaudRecording, settings: &AppSettings) -> String {
    match settings.filename_style.as_str() {
        "original" => sanitize_filename(&recording.filename),
        _ => slugify(&recording.filename),
    }
}

fn build_info_file(name: &str, start_time: i64, duration_ms: i64, transcript: &str) -> String {
    let date = format_date(start_time);
    let duration = format_duration(duration_ms);
    let mut content = format!(
        "Title: {name}\nDate: {date}\nDuration: {duration}\nSource: Plaud\n"
    );
    if !transcript.is_empty() {
        content.push_str("\n--- Transcript ---\n\n");
        content.push_str(transcript);
    }
    content
}

fn format_date(timestamp_ms: i64) -> String {
    if timestamp_ms <= 0 {
        return "unknown-date".to_string();
    }
    let secs = timestamp_ms / 1000;
    Utc.timestamp_opt(secs, 0)
        .single()
        .map(|dt| dt.format("%Y-%m-%d").to_string())
        .unwrap_or_else(|| "unknown-date".to_string())
}

fn format_duration(duration_ms: i64) -> String {
    let minutes = (duration_ms / 60_000).max(1);
    format!("{minutes} min")
}

fn slugify(input: &str) -> String {
    let slug: String = input
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '-' })
        .collect();
    let trimmed = slug.trim_matches('-');
    if trimmed.is_empty() {
        "recording".to_string()
    } else {
        trimmed.chars().take(80).collect()
    }
}

fn sanitize_filename(input: &str) -> String {
    let cleaned: String = input
        .chars()
        .map(|c| match c {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '-',
            _ => c,
        })
        .collect();
    if cleaned.trim().is_empty() {
        "recording".to_string()
    } else {
        cleaned
    }
}

fn sanitize_folder_name(input: &str) -> String {
    let cleaned = input.trim();
    if cleaned.is_empty() {
        "PlaudRecordings".to_string()
    } else {
        sanitize_filename(cleaned)
    }
}

fn device_folder_name(serial: &str) -> String {
    if serial.is_empty() {
        "PlaudDevice".to_string()
    } else {
        slugify(serial)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> PlaudRecording {
        PlaudRecording {
            id: "id1".into(),
            filename: "Team Standup, June".into(),
            duration: 1_800_000,
            // 2025-06-08 (timestamp_millis)
            start_time: Utc.with_ymd_and_hms(2025, 6, 8, 10, 0, 0).unwrap().timestamp_millis(),
            is_trans: true,
            serial_number: "NOTE-PRO-1".into(),
            downloaded: false,
        }
    }

    fn settings(structure: &str, style: &str) -> AppSettings {
        AppSettings {
            download_dir: "/tmp/plaud".into(),
            folder_structure: structure.into(),
            custom_prefix: "MyRecordings".into(),
            filename_style: style.into(),
            ..AppSettings::default()
        }
    }

    #[test]
    fn slugify_replaces_non_alphanumeric_with_hyphens() {
        assert_eq!(slugify("Team Standup, June"), "Team-Standup--June");
        assert_eq!(slugify("   "), "recording");
    }

    #[test]
    fn sanitize_filename_keeps_title_strips_illegal() {
        assert_eq!(sanitize_filename("Team Standup, June"), "Team Standup, June");
        assert_eq!(sanitize_filename("a/b:c*?\"<>|d"), "a-b-c------d");
    }

    #[test]
    fn build_audio_path_flat_clean() {
        let p = build_audio_path(Path::new("/tmp/plaud"), &sample(), &settings("flat", "clean"));
        assert_eq!(
            p.to_string_lossy().replace('\\', "/"),
            "/tmp/plaud/MyRecordings/Team-Standup--June.mp3"
        );
    }

    #[test]
    fn build_audio_path_by_date_original() {
        let p = build_audio_path(Path::new("/tmp/plaud"), &sample(), &settings("by_date", "original"));
        assert_eq!(
            p.to_string_lossy().replace('\\', "/"),
            "/tmp/plaud/MyRecordings/2025-06-08/Team Standup, June.mp3"
        );
    }

    #[test]
    fn build_audio_path_by_date_device() {
        let p = build_audio_path(
            Path::new("/tmp/plaud"),
            &sample(),
            &settings("by_date_device", "clean"),
        );
        assert_eq!(
            p.to_string_lossy().replace('\\', "/"),
            "/tmp/plaud/MyRecordings/2025-06-08/NOTE-PRO-1/Team-Standup--June.mp3"
        );
    }

    #[test]
    fn format_date_handles_invalid() {
        assert_eq!(format_date(0), "unknown-date");
        assert_eq!(format_date(-5), "unknown-date");
    }
}