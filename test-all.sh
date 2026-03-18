#!/usr/bin/env bash
# Run all Python test suites. Exit with non-zero if any fail.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
failures=0

run_suite() {
    local dir="$1"
    local python="$2"
    echo "━━━ $dir ━━━"
    if ! "$python" -m pytest "$REPO_ROOT/$dir/tests/" -v --tb=short; then
        failures=$((failures + 1))
    fi
    echo ""
}

# usb-extractor
if [ -f "$REPO_ROOT/usb-extractor/.venv/bin/python" ]; then
    run_suite "usb-extractor" "$REPO_ROOT/usb-extractor/.venv/bin/python"
else
    echo "⚠ usb-extractor: venv not found, skipping"
fi

# transcription-pipeline
if [ -f "$REPO_ROOT/transcription-pipeline/.venv/bin/python" ]; then
    run_suite "transcription-pipeline" "$REPO_ROOT/transcription-pipeline/.venv/bin/python"
else
    echo "⚠ transcription-pipeline: venv not found, skipping"
fi

# Windows-App (use system python or any available)
if command -v python3 &>/dev/null; then
    run_suite "Windows-App" "python3"
else
    echo "⚠ Windows-App: python3 not found, skipping"
fi

if [ $failures -gt 0 ]; then
    echo "✗ $failures suite(s) failed"
    exit 1
fi
echo "✓ All suites passed"
