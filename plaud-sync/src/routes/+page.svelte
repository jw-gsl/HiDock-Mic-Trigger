<script lang="ts">
  import LoginView from "$lib/components/LoginView.svelte";
  import SettingsView from "$lib/components/SettingsView.svelte";
  import SyncView from "$lib/components/SyncView.svelte";
  import { api } from "$lib/api";
  import type { AuthStatus } from "$lib/types";
  import { applyTheme } from "$lib/utils";
  import { onMount } from "svelte";

  let auth = $state<AuthStatus | null>(null);
  let view = $state<"sync" | "settings">("sync");
  let loading = $state(true);

  onMount(async () => {
    try {
      const settings = await api.getSettings();
      applyTheme(settings.theme);
    } catch {
      // ignore — falls back to system theme
    }
    auth = await api.getAuthStatus();
    loading = false;
  });

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

  <main class="content">
    {#if loading}
      <div class="card">
        <p>Loading...</p>
      </div>
    {:else if !auth?.loggedIn}
      <LoginView onSuccess={handleLoginSuccess} />
    {:else if view === "settings"}
      <SettingsView onBack={() => (view = "sync")} />
    {:else}
      <SyncView
        name={auth.name ?? auth.email}
        onOpenSettings={() => (view = "settings")}
        onLogout={handleLogout}
      />
    {/if}
  </main>
</div>