#!/usr/bin/env bash
# Bootstrap a private ai-token-monitor fork for Path A (private Supabase leaderboard).
#
# Usage:
#   ./scripts/bootstrap-private-fork.sh [target-dir]
#
# Default target: ../ai-token-monitor-private (sibling of hidock-tools)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TAG="${ATM_TAG:-v0.19.41}"
DEFAULT_TARGET="$(cd "$PKG_DIR/../.." && pwd)/ai-token-monitor-private"
TARGET="${1:-$DEFAULT_TARGET}"

echo "==> Cloning soulduse/ai-token-monitor @ $TAG → $TARGET"
if [[ -d "$TARGET/.git" ]]; then
  echo "    Target already a git repo; skipping clone (will re-apply patches)."
  git -C "$TARGET" fetch --tags --depth 1 origin "$TAG" 2>/dev/null || true
else
  rm -rf "$TARGET"
  git clone --depth 1 --branch "$TAG" https://github.com/soulduse/ai-token-monitor.git "$TARGET"
fi

echo "==> Applying app patches (env-based Supabase, fail-closed)"
cp "$PKG_DIR/app-patches/supabase.ts" "$TARGET/src/lib/supabase.ts"

# Badge URL → env-derived
python3 - <<'PY' "$TARGET/src/components/badge/BadgeOverlay.tsx"
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
old = 'const SUPABASE_BADGE_URL = "https://giunmtxxvapcgrpxjopq.supabase.co/functions/v1/badge";'
new = '''const SUPABASE_BADGE_URL = import.meta.env.VITE_SUPABASE_URL
  ? `${String(import.meta.env.VITE_SUPABASE_URL).replace(/\\/$/, "")}/functions/v1/badge`
  : "";'''
if old not in text:
    if "import.meta.env.VITE_SUPABASE_URL" in text and "SUPABASE_BADGE_URL" in text:
        print("    BadgeOverlay already patched")
    else:
        raise SystemExit("BadgeOverlay.tsx: expected badge URL constant not found; patch manually")
else:
    path.write_text(text.replace(old, new, 1))
    print("    BadgeOverlay.tsx patched")
PY

echo "==> Adding private-team allowlist migration (timestamp after upstream)"
MIG_DIR="$TARGET/supabase/migrations"
mkdir -p "$MIG_DIR"
cp "$PKG_DIR/migrations/001_team_allowlist_and_rls.sql" \
  "$MIG_DIR/20260714000000_team_allowlist_and_rls.sql"

echo "==> Writing .env.example"
cp "$PKG_DIR/.env.example" "$TARGET/.env.example"
if [[ ! -f "$TARGET/.env" ]]; then
  cp "$PKG_DIR/.env.example" "$TARGET/.env"
  echo "    Created $TARGET/.env — fill in VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY"
else
  echo "    .env already exists; left untouched"
fi

# Ensure .env is gitignored
if [[ -f "$TARGET/.gitignore" ]] && ! grep -qE '^\.env$' "$TARGET/.gitignore"; then
  printf '\n# Private team secrets\n.env\n.env.local\n.env.production\n' >> "$TARGET/.gitignore"
fi

echo "==> Done.
Next:
  1. Create a Supabase project; enable GitHub OAuth; add redirect ai-token-monitor://auth/callback
  2. cd $TARGET && supabase link --project-ref <ref> && supabase db push
     (or paste migrations in SQL Editor in order — see migrations/000_upstream_order.txt)
  3. Seed allowlist: edit $PKG_DIR/scripts/seed-allowlist.example.sql and run in SQL Editor
  4. Put project URL + anon key in $TARGET/.env
  5. npm install && npm run tauri dev   # or npm run tauri build
  6. Full runbook: docs/RUNBOOK-private-leaderboard.md (in hidock-tools)
"
