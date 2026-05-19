#!/bin/bash
# PreToolUse hook: intercept Bash commands that would rebuild the HiDock
# Mac app. The "Deploy to Applications" post-build script kills the
# running app and any child subprocesses (ffmpeg, extractor.py,
# transcribe.py), so a rebuild mid-work silently aborts the user.
#
# Approval mechanism: emit a `permissionDecision: "ask"` hook response.
# Claude Code surfaces a yes/no prompt in the session UI itself, every
# time, and Claude has no way to bypass it. No file sentinel, no tty
# read, no Claude-touchable approval path.
#
# Refs: https://code.claude.com/docs/en/hooks.md

# Read the tool input JSON from stdin
cmd=$(jq -r '.tool_input.command // ""')

# Match xcodebuild anywhere in the command line (case-insensitive).
# Prefix-only matching can't catch `cd <path> && xcodebuild ...` which
# is how this is usually invoked.
if ! echo "$cmd" | grep -qi xcodebuild; then
  exit 0
fi

# Collect busy-state signals so the prompt's reason explains what the
# rebuild is about to interrupt.
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
  reason="HiDock rebuild will kill the running app AND in-flight work:"$'\n'
  for item in "${busy[@]}"; do
    reason+="  • $item"$'\n'
  done
  reason+="Approve rebuild?"
else
  reason="HiDock rebuild will kill the running app (idle — no ffmpeg/extractor/transcription detected). Approve rebuild?"
fi

# Emit the structured hook response. `permissionDecision: ask` tells
# Claude Code to prompt the user in the session, regardless of any
# allow-list / settings.json permissions.
jq -n --arg reason "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "ask",
    permissionDecisionReason: $reason
  }
}'
exit 0
