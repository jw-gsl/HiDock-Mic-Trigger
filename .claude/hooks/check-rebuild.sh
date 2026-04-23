#!/bin/bash
# PreToolUse hook: intercept Bash commands that would rebuild the HiDock
# Mac app. The "Deploy to Applications" post-build script kills the
# running app and any child subprocesses (ffmpeg, extractor.py,
# transcribe.py), so a rebuild mid-work silently aborts the user.
#
# This hook ALWAYS blocks with exit 2 when xcodebuild is detected —
# forcing Claude to surface the rebuild intent to the user. It enriches
# the block message with live busy-state so Claude (and the user) can
# see whether anything is currently in flight.

# Read the tool input JSON from stdin
cmd=$(jq -r '.tool_input.command // ""')

# Match xcodebuild anywhere in the command line (case-insensitive).
# Prefix-only matching via the hook `if` field can't catch
# `cd <path> && xcodebuild ...` which is how it's usually invoked.
if ! echo "$cmd" | grep -qi xcodebuild; then
  exit 0
fi

# Collect busy-state signals
busy=()
if pgrep -f 'ffmpeg.*HiDock' >/dev/null 2>&1; then
  busy+=("ffmpeg is actively recording the HiDock")
fi
if pgrep -f 'usb-extractor.*extractor\.py' >/dev/null 2>&1; then
  busy+=("an extractor subprocess is running (download, status probe, or list-devices)")
fi
if pgrep -f 'transcription-pipeline.*transcribe.*\.py' >/dev/null 2>&1; then
  busy+=("a transcription subprocess is running (whisper/parakeet/cohere)")
fi

if [ ${#busy[@]} -gt 0 ]; then
  printf 'BLOCKED: about to rebuild the HiDock Mac app, which will KILL the running instance and any child subprocesses. The app appears BUSY:\n' >&2
  for item in "${busy[@]}"; do
    printf '  - %s\n' "$item" >&2
  done
  printf 'Ask the user to confirm before proceeding — rebuilding right now will abort the in-flight work.\n' >&2
else
  printf 'Blocked: about to rebuild the HiDock Mac app, which will kill the running instance. App appears idle (no ffmpeg, no extractor, no transcription). Ask the user to confirm before running xcodebuild.\n' >&2
fi
exit 2
