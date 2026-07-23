# Runbook: private team leaderboard (Path A)

Date: 2026-07-14  
Upstream pin: **ai-token-monitor v0.19.41**  
Package: `private-team-leaderboard/`

This gets a small trusted team on a **private** Supabase-backed leaderboard using a private build of AI Token Monitor. No multi-tenant “teams” UI — one project = one team.

---

## Prerequisites

- Supabase account (free tier is fine to start)
- GitHub OAuth capability (Supabase can use its GitHub provider or a GitHub OAuth App you own)
- Node 18+, Rust, Tauri v2 prerequisites ([upstream README](https://github.com/soulduse/ai-token-monitor))
- macOS Apple Silicon for the primary tray app (Windows optional)
- `supabase` CLI optional but recommended (`brew install supabase/tap/supabase`)

---

## 1. Create Supabase project

1. [supabase.com](https://supabase.com) → New project  
2. Note **Project URL** and **anon public** key (Settings → API)  
3. Do **not** put the **service_role** key in any desktop app

### Auth: GitHub

1. Authentication → Providers → **GitHub** → enable  
2. Create a GitHub OAuth App (or use Supabase-hosted instructions):
   - Homepage URL: your choice (e.g. private repo or `https://github.com/<org>`)
   - Authorization callback URL:  
     `https://<PROJECT_REF>.supabase.co/auth/v1/callback`
3. Paste Client ID / Secret into Supabase GitHub provider settings  

### Redirect URLs (Auth → URL configuration)

Add:

| URL | When |
|-----|------|
| `ai-token-monitor://auth/callback` | Production / installed app (required) |
| `http://localhost:1420/**` | `tauri dev` if that is your Vite port (confirm in `vite.config.ts`) |

Site URL can stay the Supabase default or a private docs page.

---

## 2. Apply schema

### Option A — CLI (preferred)

```bash
# After bootstrap-private-fork.sh (see §3)
cd /path/to/ai-token-monitor-private
supabase login
supabase link --project-ref YOUR_PROJECT_REF
supabase db push
```

This applies all upstream migrations plus  
`supabase/migrations/20260714000000_team_allowlist_and_rls.sql`  
(copied from `private-team-leaderboard/migrations/001_…`).

### Option B — SQL Editor

1. Open each file under upstream `supabase/migrations/` in the order listed in  
   `private-team-leaderboard/migrations/000_upstream_order.txt`  
2. Run them one-by-one in the Supabase SQL Editor  
3. Finally run `private-team-leaderboard/migrations/001_team_allowlist_and_rls.sql`

### What 001 adds

- `team_allowlist(github_login, …)`  
- Trigger on `auth.users` **BEFORE INSERT** — rejects OAuth users not on the list  
- `profiles` / `daily_snapshots` SELECT restricted to **authenticated** (no anon public scrape)

---

## 3. Bootstrap private app fork

From the hidock-tools repo:

```bash
cd private-team-leaderboard
chmod +x scripts/bootstrap-private-fork.sh
./scripts/bootstrap-private-fork.sh   # → ../ai-token-monitor-private by default
```

What it does:

1. Shallow-clones `soulduse/ai-token-monitor` @ `v0.19.41`  
2. Replaces `src/lib/supabase.ts` with env-based **fail-closed** client  
3. Points badge URL at `VITE_SUPABASE_URL` (badge Edge Function not required)  
4. Adds allowlist migration into `supabase/migrations/`  
5. Writes `.env` / `.env.example`

Edit `.env`:

```env
VITE_SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
VITE_SUPABASE_ANON_KEY=eyJ...
```

---

## 4. Seed the allowlist **before** anyone signs in

```sql
insert into public.team_allowlist (github_login, display_name, notes) values
  ('your-github-login', 'You', 'owner'),
  ('teammate', 'Teammate', null)
on conflict (github_login) do nothing;
```

Template: `private-team-leaderboard/scripts/seed-allowlist.example.sql`

**Order matters:** seed allowlist → then people open the app and Sign in with GitHub.  
If someone signs in first, the trigger raises `PRIVATE_TEAM_NOT_ALLOWLISTED`.

To add someone later: insert their GitHub login, then ask them to sign in.

To remove access:

```sql
delete from public.team_allowlist where lower(github_login) = lower('ex-teammate');
-- Optionally ban existing session users:
-- delete from auth.users where ...  (or disable in Auth dashboard)
update public.profiles set leaderboard_hidden = true where id = '<uuid>';
```

---

## 5. Build and run

```bash
cd /path/to/ai-token-monitor-private
npm install
npm run tauri dev      # local test
# npm run tauri build  # ship DMG / installer privately
```

Per user:

1. Install **this** private build (not the public GitHub Release)  
2. Settings → Account → **Sign in with GitHub**  
3. Enable **Share Usage Data** / leaderboard opt-in  
4. Use Claude Code / Codex as usual  
5. Open **Leaderboard** tab — only allowlisted teammates with uploads appear  

Tell people **not** to enable Share Usage Data on the **public** App Store / GitHub release if they also install that build — private isolation is by which binary + which Supabase URL it was built with.

---

## 6. Pilot checklist (2–3 people)

| Check | Pass? |
|-------|--------|
| Non-allowlisted GitHub user cannot complete signup | |
| Allowlisted user signs in; profile nickname appears | |
| Local Overview numbers move after a Claude session | |
| After ≤15 min (or manual backfill), user appears on Leaderboard | |
| Second user sees first user’s rank | |
| Anon REST without JWT cannot `select * from daily_snapshots` | |
| Public upstream leaderboard does **not** show your team | |

Manual backfill (first visit / Settings path in app) uploads ~60 days of history once — useful for a meaningful pilot board.

---

## 7. Ops

| Topic | Guidance |
|-------|----------|
| IO budget | Upstream hit Free/Nano limits; keep stock 15‑min upload throttle; don’t reintroduce 60‑day auto upload |
| Abuse / hide | `update profiles set leaderboard_hidden = true where id = …` |
| Chat | Global among project members if they opt in; disable Realtime or ignore the Chat tab if you don’t want it |
| Badges | Live SVG badge function not deployed by default; local PNG/SVG export still works |
| Upgrades | Re-pin tag, re-run bootstrap or rebase patches, `db push` new upstream migrations, re-test allowlist |
| Secrets | Only anon key in app; service_role only in SQL Editor / CI / server |

---

## 8. Distribution options

1. **Each engineer builds** from the private git remote (simplest security)  
2. **Internal DMG** on a shared drive / private GH Release (sign with your Developer ID if you want Gatekeeper-friendly installs)  
3. Do **not** publish the private-env binary as a public “official” release

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Leaderboard “not configured” | Missing `VITE_SUPABASE_*` at build time |
| OAuth opens then nothing | Redirect URL / deep link `ai-token-monitor://auth/callback` not registered |
| Signup error / blank after GitHub | Not on `team_allowlist`, or login metadata field mismatch (check `auth.users.raw_user_meta_data`) |
| Opted in but never ranks | Opt-in false; no local sessions; upload throttle; RPC error in console |
| Wrong people visible | You pointed the build at the **public** upstream project URL |

### Debug allowlist metadata

```sql
select id, email, raw_user_meta_data
from auth.users
order by created_at desc
limit 5;
```

Confirm the GitHub login field used by the trigger (`user_name` / `preferred_username` / `login`).

---

## 10. Done when

- [ ] Private Supabase project live with full migrations + 001  
- [ ] Allowlist seeded  
- [ ] Private fork built with your env  
- [ ] ≥2 teammates ranked on the in-app Leaderboard  
- [ ] Confirmed isolation from public upstream project  

Next optional steps (not Path A): manager-only web dashboard; multi-team Path B (see plan doc).
