<script lang="ts">
  import LoginView from "$lib/components/LoginView.svelte";
  import SettingsView from "$lib/components/SettingsView.svelte";
  import SyncView from "$lib/components/SyncView.svelte";
  import { api } from "$lib/api";
  import type { AuthStatus } from "$lib/types";
  import { applyTheme } from "$lib/utils";
  import {
    checkForUpdate,
    downloadAndInstall,
    relaunch,
    type Update,
    type UpdateStatus,
  } from "$lib/updater";
  import { onMount } from "svelte";

  let auth = $state<AuthStatus | null>(null);
  let view = $state<"sync" | "settings">("sync");
  let loading = $state(true);

  let update = $state<UpdateStatus>({ kind: "idle" });
  let pendingUpdate: Update | null = null;

  onMount(async () => {
    try {
      const settings = await api.getSettings();
      applyTheme(settings.theme);
    } catch {
      // ignore — falls back to system theme
    }
    auth = await api.getAuthStatus();
    loading = false;
    // Silent check on launch — surfaces a banner only if an update exists.
    void checkUpdates(false);
  });

  async function checkUpdates(manual = false) {
    if (update.kind === "checking" || update.kind === "downloading") return;
    update = { kind: "checking" };
    try {
      const found = await checkForUpdate();
      if (found) {
        pendingUpdate = found;
        update = { kind: "available", version: found.version, notes: found.body };
      } else {
        // On a silent launch check don't leave an "up to date" banner lingering.
        update = manual ? { kind: "uptodate" } : { kind: "idle" };
      }
    } catch (e) {
      // Launch checks fail quietly (offline, or pubkey not yet configured);
      // a manual check surfaces the error.
      update = manual ? { kind: "error", message: String(e) } : { kind: "idle" };
    }
  }

  async function installUpdate() {
    if (!pendingUpdate) return;
    try {
      update = { kind: "downloading", percent: 0 };
      await downloadAndInstall(pendingUpdate, (percent) => {
        update = { kind: "downloading", percent };
      });
      update = { kind: "ready" };
    } catch (e) {
      update = { kind: "error", message: String(e) };
    }
  }

  async function restartApp() {
    try {
      await relaunch();
    } catch (e) {
      update = { kind: "error", message: String(e) };
    }
  }

  async function handleLoginSuccess() {
    auth = await api.getAuthStatus();
    view = "sync";
  }

  async function handleLogout() {
    await api.logout();
    auth = await api.getAuthStatus();
    view = "sync";
  }
</script>

<div class="app-shell">
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark">
        <img src="/logo.png" alt="Plaud Sync" />
      </div>
      <div>
        <h1>Plaud Sync</h1>
        <p>Download your recordings locally</p>
      </div>
    </div>
  </header>

  {#if update.kind === "available"}
    <div class="update-bar">
      <span>Version {update.version} is available.</span>
      <button class="btn btn-primary btn-sm" onclick={installUpdate}>Download &amp; install</button>
    </div>
  {:else if update.kind === "downloading"}
    <div class="update-bar">
      <span>Downloading update… {update.percent}%</span>
    </div>
  {:else if update.kind === "ready"}
    <div class="update-bar">
      <span>Update installed.</span>
      <button class="btn btn-primary btn-sm" onclick={restartApp}>Restart now</button>
    </div>
  {:else if update.kind === "error"}
    <div class="update-bar error">
      <span>Update failed: {update.message}</span>
      <button class="link-button" onclick={() => (update = { kind: "idle" })}>Dismiss</button>
    </div>
  {:else if update.kind === "uptodate"}
    <div class="update-bar">
      <span>You're on the latest version.</span>
      <button class="link-button" onclick={() => (update = { kind: "idle" })}>Dismiss</button>
    </div>
  {/if}

  <main class="content">
    {#if loading}
      <div class="card">
        <p>Loading...</p>
      </div>
    {:else if !auth?.loggedIn}
      <LoginView onSuccess={handleLoginSuccess} />
    {:else if view === "settings"}
      <SettingsView
        onBack={() => (view = "sync")}
        onCheckUpdates={() => checkUpdates(true)}
        updateStatus={update}
      />
    {:else}
      <SyncView
        name={auth.name ?? auth.email}
        onOpenSettings={() => (view = "settings")}
        onLogout={handleLogout}
      />
    {/if}
  </main>
</div>

<style>
  .update-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 0 0 12px;
    padding: 8px 14px;
    border-radius: 8px;
    background: var(--surface-muted);
    border: 1px solid var(--border);
    font-size: 0.85rem;
  }
  .update-bar.error {
    background: var(--pending-bg, var(--surface-muted));
    color: var(--pending-text, inherit);
  }
</style>
