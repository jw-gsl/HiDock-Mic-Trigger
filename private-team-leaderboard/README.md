# Private team leaderboard (Path A)

Scaffolding to run **AI Token Monitor** against **your own Supabase project** so only your team appears on the leaderboard.

| Item | Detail |
|------|--------|
| Upstream | [soulduse/ai-token-monitor](https://github.com/soulduse/ai-token-monitor) `@v0.19.41` |
| Plan | [`docs/PLAN-private-team-leaderboard-supabase-2026-07-14.md`](../docs/PLAN-private-team-leaderboard-supabase-2026-07-14.md) |
| Runbook | [`docs/RUNBOOK-private-leaderboard.md`](../docs/RUNBOOK-private-leaderboard.md) |

## What’s in this folder

```
private-team-leaderboard/
├── migrations/
│   ├── 000_upstream_order.txt          # order of stock SQL migrations
│   └── 001_team_allowlist_and_rls.sql  # allowlist + tighter RLS
├── app-patches/
│   ├── supabase.ts                     # VITE_SUPABASE_* fail-closed client
│   └── BadgeOverlay.badge-url.patch.txt
├── scripts/
│   ├── bootstrap-private-fork.sh       # clone tag + apply patches
│   └── seed-allowlist.example.sql
├── .env.example
└── README.md
```

## Quick start

```bash
# From this directory
chmod +x scripts/bootstrap-private-fork.sh
./scripts/bootstrap-private-fork.sh                    # → ../../ai-token-monitor-private
# or: ./scripts/bootstrap-private-fork.sh /path/to/ai-token-monitor-private

# Then follow docs/RUNBOOK-private-leaderboard.md
```

Default clone path if you omit the argument: sibling of `hidock-tools`, named `ai-token-monitor-private`.

## Security model (Path A)

- Isolation = **your Supabase project** (public upstream never sees your data).
- Membership = **`team_allowlist`** of GitHub logins (enforced on `auth.users` insert).
- App build **must** set `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` or leaderboard stays offline (no accidental public upload).
