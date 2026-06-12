# Plaud Sync — Updater Signing Setup (one-time)

This is the one-time setup that makes the **Release Plaud Sync** workflow work, so
installed copies of Plaud Sync can auto-update themselves.

You only do this **once**. After it's done, releasing is just: bump the version and
run the workflow.

> **Why it's needed:** Tauri's auto-updater only trusts updates signed with *your*
> private key. The app ships with the matching **public** key baked in, checks every
> downloaded update against it, and refuses anything that doesn't match. The release
> workflow currently fails on purpose (`preflight` step) until this is configured.

---

## What you'll end up with

- A **key pair**: a private key (secret, stays with you / in GitHub Secrets) and a
  public key (committed into the app config).
- The public key in `plaud-sync/src-tauri/tauri.conf.json`.
- Two GitHub repo secrets holding the private key + its password.

---

## Step 1 — Generate the key pair

On your Mac, in a terminal:

```bash
cd ~/_git/hidock-tools/plaud-sync
npm run tauri signer generate -- -w ~/.tauri/plaud-sync.key
```

- It will ask for a **password**. Pick one and **write it down** — you need it in Step 3.
  (You *can* leave it empty by pressing Enter, but a password is safer.)
- This creates two files:
  - `~/.tauri/plaud-sync.key`     ← **private key** (keep secret, never commit)
  - `~/.tauri/plaud-sync.key.pub` ← **public key**

The command also prints the public key to the screen.

> ⚠️ **Back up `~/.tauri/plaud-sync.key` and its password somewhere safe** (e.g. a
> password manager). If you lose them, you can't ship updates that existing installs
> will accept — users would have to reinstall manually.

---

## Step 2 — Put the PUBLIC key in the app config

1. Show the public key:

   ```bash
   cat ~/.tauri/plaud-sync.key.pub
   ```

2. Open `plaud-sync/src-tauri/tauri.conf.json` and find this line (around line 43):

   ```json
   "pubkey": "REPLACE_WITH_TAURI_UPDATER_PUBLIC_KEY"
   ```

3. Replace the placeholder with the **whole** contents of the `.pub` file (one long
   string), keeping the quotes:

   ```json
   "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6...the rest of your key..."
   ```

4. Commit this on a branch and merge it (it's a normal code change — safe to commit):

   ```bash
   git checkout -b chore/plaud-sync-updater-pubkey
   git add plaud-sync/src-tauri/tauri.conf.json
   git commit -m "plaud-sync: add updater public key"
   git push -u origin chore/plaud-sync-updater-pubkey
   gh pr create --fill
   ```

> The public key is **not** secret — it's meant to be in the app.

---

## Step 3 — Add the PRIVATE key as GitHub repo secrets

The release workflow reads two secrets. Add them once:

1. Print the private key (the whole file is the secret value):

   ```bash
   cat ~/.tauri/plaud-sync.key
   ```

2. Go to the repo on GitHub → **Settings** → **Secrets and variables** → **Actions**
   → **New repository secret**, and add:

   | Secret name | Value |
   |---|---|
   | `TAURI_SIGNING_PRIVATE_KEY` | the full contents of `~/.tauri/plaud-sync.key` |
   | `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | the password you chose in Step 1 (leave empty if you didn't set one) |

   *(CLI alternative, run on your Mac):*
   ```bash
   gh secret set TAURI_SIGNING_PRIVATE_KEY < ~/.tauri/plaud-sync.key
   gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD   # it'll prompt for the value
   ```

> ⚠️ Never paste the **private** key into code, the config file, chat, or a commit.
> It only ever lives in `~/.tauri/` and in GitHub Secrets.

---

## Step 4 — Release

Once Steps 1–3 are done:

1. **Bump the version** in `plaud-sync/src-tauri/tauri.conf.json` (e.g. `0.2.0` → `0.2.1`)
   so clients see it as newer. Commit + merge.
2. Run the workflow: GitHub → **Actions** → **Release Plaud Sync** → **Run workflow**.
   *(CLI: `gh workflow run "Release Plaud Sync"`)*

It builds signed macOS + Windows bundles, generates `latest.json`, and publishes them
to the rolling release tagged **`plaud-sync-latest`** — the URL the app's updater polls
(`plugins.updater.endpoints` in `tauri.conf.json`).

That's it. From now on, releasing = bump version → run workflow.

---

## Quick reference

| Thing | Where |
|---|---|
| Private key file | `~/.tauri/plaud-sync.key` (secret) |
| Public key file | `~/.tauri/plaud-sync.key.pub` |
| Public key goes in | `plaud-sync/src-tauri/tauri.conf.json` → `plugins.updater.pubkey` |
| Private key goes in | GitHub secret `TAURI_SIGNING_PRIVATE_KEY` |
| Key password goes in | GitHub secret `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` |
| Update channel | release tag `plaud-sync-latest` |
| Release workflow | `.github/workflows/release-plaud-sync.yml` |
