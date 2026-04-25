#!/bin/bash
# PreToolUse hook: intercept Bash commands that would rebuild the HiDock
# Mac app. The "Deploy to Applications" post-build script kills the
# running app and any child subprocesses (ffmpeg, extractor.py,
# transcribe.py), so a rebuild mid-work silently aborts the user.
#
# Deterministic always-block: every xcodebuild invocation exits 2.
# This forces Claude to surface the rebuild intent to the user and
# wait for an explicit go-ahead each time. The block message is
# enriched with live busy-state so Claude (and the user) can see
# whether anything is in flight.
#
# Bypass path: when the user approves a rebuild, Claude can touch
# /tmp/hidock-rebuild-approved. The hook consumes the sentinel
# (deletes it on use), so the approval is single-shot — a subsequent
# xcodebuild will block again. Staleness check: sentinel older than
# 120 seconds is ignored, so an orphaned approval from a previous
# conversation can't let a rebuild through unattended.

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

sentinel=/tmp/hidock-rebuild-approved
if [ -f "$sentinel" ]; then
  # Only honour sentinels freshly placed (within the last 120 seconds)
  # so an orphaned approval from a prior turn or session can't leak
  # through. `stat -f %m` is BSD mtime in seconds since epoch.
  mtime=$(stat -f %m "$sentinel" 2>/dev/null || echo 0)
  now=$(date +%s)
  age=$((now - mtime))
  if [ "$age" -ge 0 ] && [ "$age" -le 120 ]; then
    rm -f "$sentinel"
    if [ ${#busy[@]} -gt 0 ]; then
      printf 'check-rebuild: approved sentinel consumed BUT app is BUSY:\n' >&2
      for item in "${busy[@]}"; do
        printf '  - %s\n' "$item" >&2
      done
      printf 'Blocking anyway — rebuilding now will abort the in-flight work.\n' >&2
      exit 2
    fi
    printf 'check-rebuild: approved sentinel consumed (age %ss), app idle — allowing xcodebuild\n' "$age" >&2
    exit 0
  fi
  printf 'check-rebuild: stale approval sentinel (age %ss) — ignoring and blocking\n' "$age" >&2
fi

if [ ${#busy[@]} -gt 0 ]; then
  printf 'BLOCKED: about to rebuild the HiDock Mac app, which will KILL the running instance and any child subprocesses. The app appears BUSY:\n' >&2
  for item in "${busy[@]}"; do
    printf '  - %s\n' "$item" >&2
  done
  printf 'Ask the user to confirm before proceeding — rebuilding right now will abort the in-flight work.\n' >&2
else
  printf 'BLOCKED: about to rebuild the HiDock Mac app, which will kill the running instance. App appears idle (no ffmpeg, no extractor, no transcription). Ask the user to confirm, then touch /tmp/hidock-rebuild-approved to unblock the retry.\n' >&2
fi
exit 2
