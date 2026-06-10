// Thin wrapper around the Tauri updater + process plugins so the UI can check
// for, download, and apply updates without dealing with the event plumbing.
//
// Note: an installed build can only self-update if it already ships the
// updater (i.e. 0.2.0+). The endpoint + signing pubkey are configured in
// `src-tauri/tauri.conf.json` under `plugins.updater`.
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

export type UpdateStatus =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "available"; version: string; notes?: string }
  | { kind: "downloading"; percent: number }
  | { kind: "ready" } // downloaded + installed; awaiting relaunch
  | { kind: "uptodate" }
  | { kind: "error"; message: string };

/** Returns the pending `Update` (with metadata) or null when already current. */
export async function checkForUpdate(): Promise<Update | null> {
  return await check();
}

/** Download + install `update`, reporting 0–100% download progress. */
export async function downloadAndInstall(
  update: Update,
  onPercent: (percent: number) => void,
): Promise<void> {
  let total = 0;
  let received = 0;
  await update.downloadAndInstall((event) => {
    switch (event.event) {
      case "Started":
        total = event.data.contentLength ?? 0;
        onPercent(0);
        break;
      case "Progress":
        received += event.data.chunkLength;
        onPercent(total > 0 ? Math.min(100, Math.round((received / total) * 100)) : 0);
        break;
      case "Finished":
        onPercent(100);
        break;
    }
  });
}

export { relaunch };
export type { Update };
