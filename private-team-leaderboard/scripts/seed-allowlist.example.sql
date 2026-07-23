-- Edit GitHub logins, then run in Supabase SQL Editor (or psql).
-- Logins are matched case-insensitively against GitHub OAuth metadata.

insert into public.team_allowlist (github_login, display_name, notes) values
  ('YOUR_GITHUB_LOGIN', 'You', 'owner'),
  ('teammate1', 'Teammate One', null),
  ('teammate2', 'Teammate Two', null)
on conflict (github_login) do update
  set display_name = excluded.display_name,
      notes = excluded.notes;
