<script lang="ts">
  import { api } from "../api";
  import type { AppSettings } from "../types";
  import { applyTheme, type Theme } from "../utils";

  let {
    onBack,
  }: {
    onBack: () => void;
  } = $props();

  let settings = $state<AppSettings>({
    downloadDir: "",
    folderStructure: "by_date",
    customPrefix: "PlaudRecordings",
    filenameStyle: "clean",
    createInfoTxt: true,
    downloadTranscript: true,
    autoSync: false,
    autoSyncMinutes: 15,
    theme: "system",
    startMinimized: false,
  });
  let autostart = $state(false);

  function setTheme(theme: Theme) {
    settings.theme = theme;
    applyTheme(theme); // apply immediately for live preview
  }

  async function toggleAutostart(enabled: boolean) {
    autostart = enabled;
    try {
      await api.setAutostart(enabled);
    } catch (e) {
      error = String(e);
      autostart = !enabled; // revert on failure
    }
  }
  let example = $state("");
  let saving = $state(false);
  let saved = $state(false);
  let error = $state("");

  const structureOptions = [
    {
      id: "by_date",
      title: "By Date (recommended)",
      description: "Organize recordings into date folders.",
    },
    {
      id: "flat",
      title: "Flat",
      description: "Keep all recordings in one folder.",
    },
    {
      id: "by_date_device",
      title: "By Date + Device",
      description: "Add a device subfolder when available.",
    },
    {
      id: "custom_prefix",
      title: "Custom prefix",
      description: "Use your own top-level folder name.",
    },
  ] as const;

  $effect(() => {
    void refreshExample(settings);
  });

  async function load() {
    settings = await api.getSettings();
    await refreshExample(settings);
    try {
      autostart = await api.getAutostart();
    } catch {
      // ignore
    }
  }

  async function refreshExample(current: AppSettings) {
    example = await api.getPathExample(current);
  }

  async function pickFolder() {
    const folder = await api.pickDownloadFolder();
    if (folder) settings.downloadDir = folder;
  }

  async function save() {
    saving = true;
    saved = false;
    error = "";
    try {
      await api.saveSettings(settings);
      saved = true;
    } catch (e) {
      error = String(e);
    } finally {
      saving = false;
    }
  }

  load();
</script>

<div class="card">
  <div class="settings-header">
    <div>
      <h2>Settings</h2>
      <p class="subtitle">Choose how recordings are saved on your computer.</p>
    </div>
    <button class="btn btn-secondary" onclick={onBack}>Back</button>
  </div>

  {#if error}
    <div class="status error">{error}</div>
  {:else if saved}
    <div class="status success">Settings saved.</div>
  {/if}

  <div class="field">
    <label for="download-dir">Save location</label>
    <div class="folder-row">
      <input id="download-dir" type="text" bind:value={settings.downloadDir} readonly />
      <button class="btn btn-secondary" onclick={pickFolder}>Choose</button>
      <button class="btn btn-ghost" onclick={() => api.openDownloadFolder()}>Open</button>
    </div>
  </div>

  <div class="settings-grid">
    <div class="col">
      <fieldset class="field">
        <legend>Folder structure</legend>
        <div class="option-grid">
          {#each structureOptions as option}
            <button
              class="option-card {settings.folderStructure === option.id ? 'selected' : ''}"
              onclick={() => (settings.folderStructure = option.id)}
            >
              <strong>{option.title}</strong>
              <div class="meta">{option.description}</div>
            </button>
          {/each}
        </div>
      </fieldset>

      {#if settings.folderStructure === "custom_prefix"}
        <div class="field">
          <label for="prefix">Custom folder name</label>
          <input id="prefix" bind:value={settings.customPrefix} placeholder="WorkMeetings" />
        </div>
      {/if}
    </div>

    <div class="col">
      <fieldset class="field">
        <legend>Filename style</legend>
        <div class="segmented">
          <button
            class:active={settings.filenameStyle === "clean"}
            onclick={() => (settings.filenameStyle = "clean")}
            title={'Spaces and symbols become hyphens.\ne.g. “Team Standup, June” → Team-Standup-June.mp3'}
          >
            Clean names
          </button>
          <button
            class:active={settings.filenameStyle === "original"}
            onclick={() => (settings.filenameStyle = "original")}
            title={'Keeps Plaud’s original title (only illegal characters removed).\ne.g. “Team Standup, June” → Team Standup, June.mp3'}
          >
            Original Plaud names
          </button>
        </div>
      </fieldset>

      <div class="toggle-row">
        <div>
          <strong>Download transcript</strong>
          <div class="meta">Save a .txt file when a transcript exists in Plaud.</div>
        </div>
        <input type="checkbox" bind:checked={settings.downloadTranscript} />
      </div>

      <div class="toggle-row">
        <div>
          <strong>Create info .txt file</strong>
          <div class="meta">Include date, duration, and basic details for each recording.</div>
        </div>
        <input type="checkbox" bind:checked={settings.createInfoTxt} />
      </div>

      <div class="toggle-row">
        <div>
          <strong>Auto-sync new recordings</strong>
          <div class="meta">Check Plaud in the background and download new recordings automatically.</div>
        </div>
        <input type="checkbox" bind:checked={settings.autoSync} />
      </div>

      {#if settings.autoSync}
        <div class="field">
          <label for="auto-interval">Check every</label>
          <select id="auto-interval" bind:value={settings.autoSyncMinutes}>
            <option value={15}>15 minutes</option>
            <option value={30}>30 minutes</option>
            <option value={60}>1 hour</option>
            <option value={180}>3 hours</option>
          </select>
        </div>
      {/if}

      <div class="toggle-row">
        <div>
          <strong>Start at login</strong>
          <div class="meta">Launch Plaud Sync automatically when you sign in (macOS &amp; Windows).</div>
        </div>
        <input
          type="checkbox"
          checked={autostart}
          onchange={(e) => toggleAutostart(e.currentTarget.checked)}
        />
      </div>

      <div class="toggle-row">
        <div>
          <strong>Start minimized</strong>
          <div class="meta">Open minimized so it runs quietly as a background sync tool.</div>
        </div>
        <input type="checkbox" bind:checked={settings.startMinimized} />
      </div>
    </div>
  </div>

  <fieldset class="field">
    <legend>Appearance</legend>
    <div class="segmented">
      <button class:active={settings.theme === "system"} onclick={() => setTheme("system")}>
        System
      </button>
      <button class:active={settings.theme === "light"} onclick={() => setTheme("light")}>
        Light
      </button>
      <button class:active={settings.theme === "dark"} onclick={() => setTheme("dark")}>
        Dark
      </button>
    </div>
  </fieldset>

  <div class="example-box">
    <strong>Example path</strong>
    <code>{example}</code>
  </div>

  <button class="btn btn-primary btn-large" onclick={save} disabled={saving}>
    {saving ? "Saving..." : "Save Settings"}
  </button>
</div>

<style>
  .settings-header {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: flex-start;
    margin-bottom: 8px;
  }

  .settings-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px 20px;
    align-items: start;
  }

  .settings-grid .col {
    min-width: 0;
  }

  @media (max-width: 620px) {
    .settings-grid {
      grid-template-columns: 1fr;
    }
  }

  .folder-row {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 8px;
  }

  .option-card {
    text-align: left;
    width: 100%;
    cursor: pointer;
  }

  fieldset {
    border: none;
    padding: 0;
    margin: 0 0 16px;
  }

  legend {
    font-size: 0.9rem;
    font-weight: 600;
    margin-bottom: 6px;
  }

  .meta {
    color: var(--text-muted);
    font-size: 0.85rem;
    margin-top: 4px;
  }

  .example-box {
    margin: 14px 0;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--surface-muted);
  }

  .example-box code {
    display: block;
    margin-top: 8px;
    word-break: break-all;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
</style>