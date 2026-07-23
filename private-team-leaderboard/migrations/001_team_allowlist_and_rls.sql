-- Path A private team lock-down
-- Apply AFTER all upstream ai-token-monitor migrations (v0.19.41).
--
-- What this does:
-- 1. team_allowlist — GitHub logins permitted to use this project
-- 2. Block auth.users insert when GitHub login is not allowlisted
-- 3. Tighten profiles / daily_snapshots SELECT to authenticated only
--    (upstream init used `using (true)` which is too open for a private team)

-- ---------------------------------------------------------------------------
-- Allowlist
-- ---------------------------------------------------------------------------
create table if not exists public.team_allowlist (
  github_login text primary key,
  display_name text,
  notes text,
  added_at timestamptz not null default now()
);

comment on table public.team_allowlist is
  'GitHub usernames allowed to sign in and join the private leaderboard. Matching is case-insensitive.';

alter table public.team_allowlist enable row level security;

-- Members can see who else is allowlisted (helps "who should I invite?").
-- Only service role / SQL editor can insert/update/delete (no client policies).
drop policy if exists "allowlist_select_authenticated" on public.team_allowlist;
create policy "allowlist_select_authenticated"
  on public.team_allowlist
  for select
  to authenticated
  using (true);

-- ---------------------------------------------------------------------------
-- Enforce allowlist at signup (GitHub OAuth → auth.users)
-- ---------------------------------------------------------------------------
create or replace function public.enforce_team_allowlist()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_login text;
begin
  -- GitHub provider metadata shapes (Supabase may use any of these)
  v_login := coalesce(
    nullif(trim(new.raw_user_meta_data->>'user_name'), ''),
    nullif(trim(new.raw_user_meta_data->>'preferred_username'), ''),
    nullif(trim(new.raw_user_meta_data->>'login'), '')
  );

  -- Also accept email local-part only if it is an exact allowlist entry
  -- (not recommended as primary — prefer github_login).
  if v_login is null and new.email is not null then
    v_login := split_part(new.email, '@', 1);
  end if;

  if v_login is null
     or not exists (
       select 1
       from public.team_allowlist a
       where lower(a.github_login) = lower(v_login)
     )
  then
    raise exception 'PRIVATE_TEAM_NOT_ALLOWLISTED: % is not on the team allowlist',
      coalesce(v_login, '(unknown)')
      using errcode = '42501';
  end if;

  return new;
end;
$$;

drop trigger if exists trg_enforce_team_allowlist on auth.users;
create trigger trg_enforce_team_allowlist
  before insert on auth.users
  for each row
  execute function public.enforce_team_allowlist();

-- ---------------------------------------------------------------------------
-- Tighten table RLS (authenticated-only reads)
-- ---------------------------------------------------------------------------
drop policy if exists "profiles_read" on public.profiles;
create policy "profiles_read"
  on public.profiles
  for select
  to authenticated
  using (true);

drop policy if exists "snapshots_read" on public.daily_snapshots;
create policy "snapshots_read"
  on public.daily_snapshots
  for select
  to authenticated
  using (true);

-- Chat (if present): already requires auth + having a snapshot; leave as-is.

-- ---------------------------------------------------------------------------
-- Helper: seed example (edit then run in SQL editor)
-- ---------------------------------------------------------------------------
-- insert into public.team_allowlist (github_login, display_name) values
--   ('alice', 'Alice'),
--   ('bob', 'Bob')
-- on conflict (github_login) do nothing;
