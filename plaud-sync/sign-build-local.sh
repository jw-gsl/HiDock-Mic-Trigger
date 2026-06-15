#!/usr/bin/env bash
# Build a signed + notarized Plaud Sync locally using the credentials in
# .signing/signing.env. No secrets are baked into this script — it just reads
# your local, git-ignored env file. Safe to commit.
#
# Usage:  ./sign-build-local.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE=".signing/signing.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ Missing $ENV_FILE — fill in your App Store Connect API key details first." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

# --- Validate the values were filled in ------------------------------------
fail=0
check() {
  local name="$1" val="${2:-}"
  if [[ -z "$val" || "$val" == PASTE_* ]]; then
    echo "❌ $name is not set in $ENV_FILE (still a placeholder)." >&2
    fail=1
  fi
}
check APPLE_SIGNING_IDENTITY "${APPLE_SIGNING_IDENTITY:-}"
check APPLE_API_ISSUER       "${APPLE_API_ISSUER:-}"
check APPLE_API_KEY          "${APPLE_API_KEY:-}"
check APPLE_API_KEY_PATH     "${APPLE_API_KEY_PATH:-}"

# Resolve a relative .p8 path against the project dir, then confirm it exists.
if [[ -n "${APPLE_API_KEY_PATH:-}" && "$APPLE_API_KEY_PATH" != PASTE_* ]]; then
  case "$APPLE_API_KEY_PATH" in
    /*) : ;;                                   # already absolute
    *)  APPLE_API_KEY_PATH="$SCRIPT_DIR/$APPLE_API_KEY_PATH" ;;
  esac
  export APPLE_API_KEY_PATH
  if [[ ! -f "$APPLE_API_KEY_PATH" ]]; then
    echo "❌ .p8 key not found at: $APPLE_API_KEY_PATH" >&2
    echo "   Drop the AuthKey_*.p8 file into .signing/ and check the filename." >&2
    fail=1
  fi
fi

if [[ "$fail" -ne 0 ]]; then
  echo "" >&2
  echo "Fix the items above in $ENV_FILE, then re-run." >&2
  exit 1
fi

# Confirm the signing identity is actually in the keychain.
if ! security find-identity -v -p codesigning | grep -qF "$APPLE_SIGNING_IDENTITY"; then
  echo "❌ Signing identity not found in keychain:" >&2
  echo "   $APPLE_SIGNING_IDENTITY" >&2
  exit 1
fi

echo "✅ Credentials look good. Building signed + notarized Plaud Sync…"
echo "   Identity : $APPLE_SIGNING_IDENTITY"
echo "   API key  : $APPLE_API_KEY (issuer ${APPLE_API_ISSUER:0:8}…)"
echo "   .p8 path : $APPLE_API_KEY_PATH"
echo ""

npm run tauri build

echo ""
echo "✅ Done. Signed/notarized output is under src-tauri/target/release/bundle/."
