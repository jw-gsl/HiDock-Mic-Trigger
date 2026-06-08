export interface AuthStatus {
  loggedIn: boolean;
  email?: string;
  region?: string;
  name?: string;
}

export interface Recording {
  id: string;
  filename: string;
  duration: number;
  startTime: number;
  isTrans: boolean;
  serialNumber: string;
  downloaded: boolean;
}

export interface AppSettings {
  downloadDir: string;
  folderStructure: "by_date" | "flat" | "by_date_device" | "custom_prefix";
  customPrefix: string;
  filenameStyle: "clean" | "original";
  createInfoTxt: boolean;
  downloadTranscript: boolean;
  autoSync: boolean;
  autoSyncMinutes: number;
  theme: "system" | "light" | "dark";
  startMinimized: boolean;
}

export interface SyncInfo {
  autoSync: boolean;
  intervalMinutes: number;
  secondsUntilNext: number | null;
}

export interface SyncProgress {
  current: number;
  total: number;
  message: string;
  filename: string;
}

export interface SyncResult {
  downloaded: number;
  skipped: number;
  failed: number;
  total: number;
  message: string;
}