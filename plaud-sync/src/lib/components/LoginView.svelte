<script lang="ts">
  import { api } from "../api";

  let {
    onSuccess,
  }: {
    onSuccess: () => void;
  } = $props();

  let mode = $state<"browser" | "email" | "token">("browser");
  let email = $state("");
  let password = $state("");
  let token = $state("");
  let loading = $state(false);
  let error = $state("");

  async function handleBrowserLogin() {
    error = "";
    loading = true;
    try {
      await api.loginWithBrowser();
      onSuccess();
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function handleEmailLogin(event: Event) {
    event.preventDefault();
    error = "";
    loading = true;
    try {
      await api.loginWithEmail(email.trim(), password);
      onSuccess();
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function handleTokenLogin(event: Event) {
    event.preventDefault();
    error = "";
    loading = true;
    try {
      await api.loginWithToken(token.trim());
      onSuccess();
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }
</script>

<div class="card login-card">
  <h2>Sign in to Plaud</h2>
  <p class="subtitle">
    Sign in through the Plaud website — works with Google, Apple, email, and more.
  </p>

  {#if error}
    <div class="status error">{error}</div>
  {/if}

  {#if mode === "browser"}
    <button class="btn btn-primary btn-large browser-btn" onclick={handleBrowserLogin} disabled={loading}>
      {loading ? "Waiting for sign-in..." : "Continue with Plaud"}
    </button>
    <p class="hint">
      A sign-in window will open. Use Google or any method you normally use on web.plaud.ai.
      This window will close automatically when you're signed in.
    </p>
    <p class="hint debug-hint">
      If Google sign-in does nothing, click it once, then open the debug log below and share the
      last few lines — they show clicks, popups, and blocked navigations.
    </p>
    <button class="link-button" type="button" onclick={() => api.openLoginDebugLog()}>
      Open login debug log
    </button>
  {:else if mode === "email"}
    <form onsubmit={handleEmailLogin}>
      <div class="field">
        <label for="email">Email</label>
        <input id="email" type="email" bind:value={email} placeholder="you@example.com" required />
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input id="password" type="password" bind:value={password} required />
      </div>
      <button class="btn btn-primary btn-large" type="submit" disabled={loading}>
        {loading ? "Signing in..." : "Continue"}
      </button>
      <p class="hint warn-hint">
        If you originally signed up to Plaud with Google or Apple, email/password sign-in may log
        you into a separate, empty account and your recordings won't appear. Use <strong>Browser</strong>
        sign-in instead.
      </p>
    </form>
  {:else}
    <form onsubmit={handleTokenLogin}>
      <div class="field">
        <label for="token">JWT Token</label>
        <textarea
          id="token"
          bind:value={token}
          placeholder="Paste your token from browser DevTools on web.plaud.ai"
          required
        ></textarea>
      </div>
      <button class="btn btn-primary btn-large" type="submit" disabled={loading}>
        {loading ? "Signing in..." : "Continue with Token"}
      </button>
    </form>
  {/if}

  <div class="segmented advanced" style="margin-top: 24px;">
    <button class:active={mode === "browser"} onclick={() => (mode = "browser")} disabled={loading}>
      Browser
    </button>
    <button class:active={mode === "email"} onclick={() => (mode = "email")} disabled={loading}>
      Email
    </button>
    <button class:active={mode === "token"} onclick={() => (mode = "token")} disabled={loading}>
      Token
    </button>
  </div>
</div>

<style>
  .browser-btn {
    margin-bottom: 12px;
  }

  .hint {
    margin: 0;
    color: var(--text-muted);
    font-size: 0.9rem;
    line-height: 1.5;
  }

  .debug-hint {
    margin-top: 8px;
  }

  .warn-hint {
    margin-top: 12px;
  }

  .advanced {
    width: 100%;
    display: flex;
  }

  .advanced button {
    flex: 1;
  }
</style>