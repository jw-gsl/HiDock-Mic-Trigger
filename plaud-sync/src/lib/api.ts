import { invoke } from "@tauri-apps/api/core";
import type { AppSettings, AuthStatus, Recording, SyncInfo, SyncResult } from "./types";

export const api = {
  getAuthStatus: () => invoke<AuthStatus>("get_auth_status"),
  loginWithBrowser: (region: string) =>
    invoke<AuthStatus>("login_with_browser", { region }),
  loginWithEmail: (email: string, password: string, region: string) =>
    invoke<AuthStatus>("login_with_email", { email, password, region }),
  loginWithToken: (token: string, region: string) =>
    invoke<AuthStatus>("login_with_token", { token, region }),
  logout: () => invoke<void>("logout"),
  listRecordings: () => invoke<Recording[]>("list_recordings"),
  getCachedRecordings: () => invoke<Recording[]>("get_cached_recordings"),
  setAutostart: (enabled: boolean) => invoke<void>("set_autostart", { enabled }),
  getAutostart: () => invoke<boolean>("get_autostart"),
  syncNow: () => invoke<SyncResult>("sync_now"),
  downloadSelected: (ids: string[]) =>
    invoke<SyncResult>("download_selected", { ids }),
  getSettings: () => invoke<AppSettings>("get_settings"),
  saveSettings: (settings: AppSettings) =>
    invoke<void>("save_settings", { settings }),
  getPathExample: (settings: AppSettings) =>
    invoke<string>("get_path_example", { settings }),
  pickDownloadFolder: () => invoke<string | null>("pick_download_folder"),
  openDownloadFolder: () => invoke<void>("open_download_folder"),
  openLoginDebugLog: () => invoke<void>("open_login_debug_log"),
  revealRecording: (recording: Recording) =>
    invoke<void>("reveal_recording", { recording }),
  getSyncInfo: () => invoke<SyncInfo>("get_sync_info"),
};