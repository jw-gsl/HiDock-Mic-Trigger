<script lang="ts">
  import { listen } from "@tauri-apps/api/event";
  import { onMount } from "svelte";
  import { api } from "../api";
  import type { Recording, SyncInfo, SyncProgress, SyncResult } from "../types";
  import { formatDate, formatDuration } from "../utils";

  let {
    onOpenSettings,
    onLogout,
    name,
  }: {
    onOpenSettings: () => void;
    onLogout: () => void;
    name?: string;
  } = $props();

  type Filter = "all" | "new" | "downloaded";

  let recordings = $state<Recording[]>([]);
  let loading = $state(true);
  let listing = $state(false);
  let downloading = $state(false);
  let error = $state("");
  let status = $state("");
  let progress = $state<SyncProgress | null>(null);
  let needsFolder = $state(false);
  let search = $state("");
  let filter = $state<Filter>("all");
  let selected = $state<string[]>([]);

  let lastSynced = $state<number | null>(null);
  let syncInfo = $state<SyncInfo | null>(null);
  let nextSyncAt = $state<number | null>(null);
  let nowSec = $state(Math.floor(Date.now() / 1000));

  const downloadedCount = $derived(recordings.filter((r) => r.downloaded).length);
  const pendingCount = $derived(recordings.length - downloadedCount);
  // Manual selection only matters when auto-download is off.
  const showChecks = $derived(!syncInfo?.autoSync);

  const visible = $derived(
    recordings
      .filter((r) => {
        if (filter === "new" && r.downloaded) return false;
        if (filter === "downloaded" && !r.downloaded) return false;
        const q = search.trim().toLowerCase();
        return !q || r.filename.toLowerCase().includes(q);
      })
      // Newest first, regardless of source order (fresh list or cache).
      .sort((a, b) => b.startTime - a.startTime),
  );
  const visibleNew = $derived(visible.filter((r) => !r.downloaded));

  const lastSyncedLabel = $derived(relativeTime(lastSynced, nowSec));
  const countdownLabel = $derived(
    syncInfo?.autoSync && nextSyncAt != null ? countdown(Math.max(0, nextSyncAt - nowSec)) : null,
  );

  onMount(() => {
    void load();
    let elapsed = 0;
    const tick = setInterval(() => {
      nowSec = Math.floor(Date.now() / 1000);
      // Auto-sync runs ~every minute and only emits "auto-sync-complete" when
      // something downloads, so refresh the schedule periodically to keep the
      // countdown live (cheap, local — no Plaud call).
      if (syncInfo?.autoSync && ++elapsed % 15 === 0) void refreshSyncInfo();
    }, 1000);
    const unlistenProgress = listen<SyncProgress>("sync-progress", (e) => {
      progress = e.payload;
    });
    const unlistenAuto = listen<SyncResult>("auto-sync-complete", (e) => {
      if (e.payload?.downloaded || e.payload?.failed) status = e.payload.message;
      void refreshList();
      void refreshSyncInfo();
    });
    // Surface background auto-sync failures instead of swallowing them.
    const unlistenAutoErr = listen<string>("auto-sync-error", (e) => {
      error = `Auto-sync failed: ${e.payload}`;
    });
    return () => {
      clearInterval(tick);
      void unlistenProgress.then((fn) => fn());
      void unlistenAuto.then((fn) => fn());
      void unlistenAutoErr.then((fn) => fn());
    };
  });

  function relativeTime(ms: number | null, now: number): string {
    if (!ms) return "never";
    const s = now - Math.floor(ms / 1000);
    if (s < 10) return "just now";
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return `${Math.floor(s / 3600)}h ago`;
  }

  function countdown(secs: number): string {
    if (secs < 60) return `${secs}s`;
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return m < 60 ? `${m}m ${s}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
  }

  async function load() {
    loading = true;
    error = "";

    // 1) Instant render from the local cache (no network).
    try {
      const cached = await api.getCachedRecordings();
      if (cached.length) {
        recordings = cached;
        loading = false;
      }
    } catch {
      // no cache yet — fine
    }

    try {
      const settings = await api.getSettings();
      needsFolder = !settings.downloadDir?.trim();
    } catch {
      // ignore
    }

    // 2) Refresh from Plaud in the background.
    try {
      recordings = await api.listRecordings();
      lastSynced = Date.now();
      status = recordings.length
        ? `${pendingCount} new · ${downloadedCount} downloaded`
        : "No recordings found in your account yet.";
    } catch (e) {
      // Keep the cached list on a failed/offline refresh rather than blanking.
      if (recordings.length) {
        status = "Showing saved list — couldn't reach Plaud just now.";
      } else {
        error = String(e);
      }
    } finally {
      loading = false;
      await refreshSyncInfo();
    }
  }

  async function refreshList() {
    try {
      recordings = await api.listRecordings();
      lastSynced = Date.now();
      // Drop selections that no longer apply (now downloaded / gone).
      selected = selected.filter((id) =>
        recordings.some((r) => r.id === id && !r.downloaded),
      );
    } catch (e) {
      error = String(e);
    }
  }

  async function refreshSyncInfo() {
    try {
      const info = await api.getSyncInfo();
      syncInfo = info;
      nextSyncAt =
        info.secondsUntilNext != null
          ? Math.floor(Date.now() / 1000) + info.secondsUntilNext
          : null;
    } catch {
      // non-fatal
    }
  }

  async function chooseFolder() {
    const folder = await api.pickDownloadFolder();
    if (folder) {
      needsFolder = false;
      status = `Saving to ${folder}`;
    }
  }

  // "Sync" = refresh the recording list from Plaud (no download).
  async function syncList() {
    listing = true;
    error = "";
    try {
      await refreshList();
      status = recordings.length
        ? `${pendingCount} new · ${downloadedCount} downloaded`
        : "No recordings found in your account yet.";
    } finally {
      listing = false;
    }
  }

  // "Download" = fetch the selected recordings, or all new ones if no selection.
  async function download() {
    if (needsFolder) {
      await chooseFolder();
      if (needsFolder) return;
    }
    downloading = true;
    error = "";
    progress = null;
    try {
      const useSelection = showChecks && selected.length > 0;
      const result = useSelection ? await api.downloadSelected(selected) : await api.syncNow();
      status = result.message;
      if (result.failed > 0) error = result.message;
      selected = [];
      await refreshList();
      await refreshSyncInfo();
    } catch (e) {
      error = String(e);
    } finally {
      downloading = false;
      progress = null;
    }
  }

  function toggle(id: string) {
    selected = selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id];
  }

  function reveal(recording: Recording) {
    if (recording.downloaded) void api.revealRecording(recording);
  }

  const downloadLabel = $derived(
    downloading
      ? "Downloading…"
      : showChecks && selected.length > 0
        ? `Download (${selected.length})`
        : "Download",
  );
  const downloadDisabled = $derived(
    downloading ||
      loading ||
      (selected.length === 0 && pendingCount === 0 && !needsFolder),
  );
</script>

<div class="card">
  <div class="header-row">
    <div class="who">
      <h2>Recordings</h2>
      {#if name}<span class="who-sub">{name}</span>{/if}
    </div>
    <div class="nav-actions">
      <button class="btn btn-secondary btn-sm" onclick={onOpenSettings}>Settings</button>
      <button class="btn btn-ghost btn-sm" onclick={onLogout}>Sign out</button>
    </div>
  </div>

  <div class="toolbar">
    <input class="search" type="search" placeholder="Search…" bind:value={search} />
    <div class="segmented sm">
      <button class:active={filter === "all"} onclick={() => (filter = "all")}>All</button>
      <button class:active={filter === "new"} onclick={() => (filter = "new")}>New</button>
      <button class:active={filter === "downloaded"} onclick={() => (filter = "downloaded")}>
        Saved
      </button>
    </div>
    <button
      class="btn btn-secondary btn-sm"
      onclick={syncList}
      disabled={listing || downloading || loading}
      title="Check Plaud for new recordings (no download)"
    >
      {listing ? "Syncing…" : "Sync"}
    </button>
    <button
      class="btn btn-primary btn-sm"
      onclick={download}
      disabled={downloadDisabled}
      title="Download recordings to your folder"
    >
      {downloadLabel}
    </button>
  </div>

  {#if showChecks && visibleNew.length > 0}
    <div class="select-bar">
      <button class="link-button" onclick={() => (selected = visibleNew.map((r) => r.id))}>
        Select all new ({visibleNew.length})
      </button>
      {#if selected.length}
        <button class="link-button" onclick={() => (selected = [])}>Clear</button>
      {/if}
    </div>
  {/if}

  {#if error}
    <div class="status error">{error}</div>
  {/if}

  {#if needsFolder}
    <div class="status warn">
      Choose where to save recordings.
      <button class="link-button" onclick={chooseFolder}>Choose folder</button>
    </div>
  {/if}

  {#if downloading && progress}
    <div class="progress-wrap">
      <div class="progress-bar">
        <div style={`width: ${(progress.current / Math.max(progress.total, 1)) * 100}%`}></div>
      </div>
      <p class="meta">{progress.message}</p>
    </div>
  {/if}

  {#if loading}
    <p class="meta loading-line">Loading…</p>
  {:else if visible.length}
    <div class="rec-list">
      {#each visible as recording (recording.id)}
        {#if recording.downloaded}
          <button class="rec-row clickable" onclick={() => reveal(recording)} title="Reveal in Finder">
            <span class="dot done"></span>
            <span class="rec-name">{recording.filename}</span>
            <span class="rec-meta">
              {formatDate(recording.startTime)} · {formatDuration(recording.duration)}{#if recording.isTrans} · TXT{/if}
            </span>
            <span class="rec-state done">Saved</span>
          </button>
        {:else}
          <div class="rec-row" title="Not downloaded yet">
            {#if showChecks}
              <input
                type="checkbox"
                checked={selected.includes(recording.id)}
                onchange={() => toggle(recording.id)}
              />
            {:else}
              <span class="dot new"></span>
            {/if}
            <span class="rec-name">{recording.filename}</span>
            <span class="rec-meta">
              {formatDate(recording.startTime)} · {formatDuration(recording.duration)}{#if recording.isTrans} · TXT{/if}
            </span>
            <span class="rec-state new">New</span>
          </div>
        {/if}
      {/each}
    </div>
    <p class="meta foot">
      {visible.length} of {recordings.length} · {status}
      <span class="sep">·</span> Last synced {lastSyncedLabel}{#if countdownLabel}
        <span class="sep">·</span> next auto-sync in {countdownLabel}{/if}
    </p>
  {:else if recordings.length}
    <p class="meta loading-line">No recordings match “{search}”.</p>
  {:else}
    <p class="meta loading-line">{status}</p>
  {/if}
</div>

<style>
  .header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }
  .who {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }
  .who h2 {
    margin: 0;
  }
  .who-sub {
    color: var(--text-muted);
    font-size: 0.8rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }
  .search {
    flex: 1;
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 10px;
    background: var(--input-bg);
    color: var(--text);
    font-size: 0.85rem;
  }
  .select-bar {
    display: flex;
    gap: 14px;
    margin-bottom: 10px;
    font-size: 0.8rem;
  }
  .progress-wrap {
    margin: 8px 0;
  }
  .meta {
    color: var(--text-muted);
    font-size: 0.8rem;
  }
  .loading-line {
    margin-top: 12px;
  }
  .foot {
    margin: 10px 2px 0;
  }
  .sep {
    opacity: 0.5;
    margin: 0 2px;
  }

  .rec-list {
    display: flex;
    flex-direction: column;
  }
  .rec-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 8px;
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--border);
    text-align: left;
    font: inherit;
    color: inherit;
    width: 100%;
    border-radius: 6px;
  }
  .rec-row:last-child {
    border-bottom: none;
  }
  .rec-row.clickable {
    cursor: pointer;
  }
  .rec-row.clickable:hover {
    background: var(--surface-muted);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex: none;
  }
  .dot.done {
    background: var(--success);
  }
  .dot.new {
    background: var(--primary);
  }
  .rec-name {
    flex: 1;
    min-width: 0;
    font-size: 0.88rem;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rec-meta {
    color: var(--text-muted);
    font-size: 0.76rem;
    white-space: nowrap;
    flex: none;
  }
  .rec-state {
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    flex: none;
  }
  .rec-state.done {
    color: var(--success);
  }
  .rec-state.new {
    color: var(--primary);
  }
  .status.warn {
    background: var(--pending-bg);
    color: var(--pending-text);
  }
  @media (max-width: 640px) {
    .rec-meta {
      display: none;
    }
  }
</style>
