#!/usr/bin/env bash
# Build a self-contained Plaud Sync installer. No Node/Rust needed to *run* the app afterward.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DIST="$ROOT/dist"
OS="$(uname -s)"

cd "$ROOT"

echo "==> Plaud Sync — building self-contained installer"
echo "    Project: $ROOT"

# Ensure Rust is on PATH (rustup installs here by default)
if [ -f "$HOME/.cargo/env" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env"
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js is required to build. Install from https://nodejs.org/"
  exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "ERROR: Rust is required to build. Install from https://rustup.rs/"
  exit 1
fi

echo "==> Installing npm dependencies..."
npm ci

echo "==> Building production app..."
npm run tauri build

mkdir -p "$DIST"

case "$OS" in
  Darwin)
    APP_SRC="$ROOT/src-tauri/target/release/bundle/macos/Plaud Sync.app"
    DMG_SRC=$(find "$ROOT/src-tauri/target/release/bundle/dmg" -name "*.dmg" | head -1)

    if [ ! -d "$APP_SRC" ]; then
      echo "ERROR: App bundle not found at $APP_SRC"
      exit 1
    fi

    rm -rf "$DIST/Plaud Sync.app"
    cp -R "$APP_SRC" "$DIST/Plaud Sync.app"

    if [ -n "$DMG_SRC" ] && [ -f "$DMG_SRC" ]; then
      cp "$DMG_SRC" "$DIST/"
      echo "==> DMG: $DIST/$(basename "$DMG_SRC")"
    fi

    echo "==> App: $DIST/Plaud Sync.app"
    echo ""
    echo "Install: drag 'Plaud Sync.app' to Applications, or open the .dmg."
    echo "Run:     open '$DIST/Plaud Sync.app'"
    ;;
  MINGW* | MSYS* | CYGWIN* | Windows_NT)
    # Tauri emits the NSIS installer under bundle/nsis and the MSI under bundle/msi.
    EXE_SRC=$(find "$ROOT/src-tauri/target/release/bundle" \( -name "*-setup.exe" -o -name "*.msi" \) | head -1)
    if [ -n "$EXE_SRC" ] && [ -f "$EXE_SRC" ]; then
      cp "$EXE_SRC" "$DIST/"
      echo "==> Installer: $DIST/$(basename "$EXE_SRC")"
    else
      echo "ERROR: Windows installer not found in src-tauri/target/release/bundle/"
      exit 1
    fi
    ;;
  *)
    echo "ERROR: Unsupported OS: $OS"
    exit 1
    ;;
esac

# Bundle the install/usage guide alongside the installer so it ends up in the
# distributed zip.
if [ -f "$ROOT/INSTALL.txt" ]; then
  cp "$ROOT/INSTALL.txt" "$DIST/"
  echo "==> Guide: $DIST/INSTALL.txt"
fi

du -sh "$DIST"/*
echo ""
echo "Done. The app in dist/ is self-contained — end users do not need Node or Rust."