#!/bin/bash
# Codex PreToolUse hook: require an explicit user approval before any HiDock
# xcodebuild. The project's post-build phase deploys to /Applications and
# kills the running app plus in-flight recording/extraction/transcription.

set -euo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null || true)

# This hook is registered without a matcher so it remains effective across
# Codex tool-name changes. Only inspect commands that can invoke xcodebuild.
if ! printf '%s' "$cmd" | grep -qi 'xcodebuild'; then
  exit 0
fi

busy=()
if pgrep -f 'ffmpeg.*HiDock' >/dev/null 2>&1; then
  busy+=("ffmpeg is actively recording the HiDock")
fi
if pgrep -f 'usb-extractor.*extractor\.py' >/dev/null 2>&1; then
  busy+=("an extractor subprocess is running")
fi
if pgrep -f 'transcription-pipeline.*transcribe.*\.py' >/dev/null 2>&1; then
  busy+=("a transcription subprocess is running")
fi

if [ "${#busy[@]}" -gt 0 ]; then
  reason=$'HiDock rebuild will kill the running app and may interrupt in-flight work:\n'
  for item in "${busy[@]}"; do
    reason+="  • $item"$'\n'
  done
  reason+="Approve this rebuild?"
else
  reason="HiDock rebuild will replace the installed app and kill the running app. No active recording, extraction, or transcription was detected. Approve this rebuild?"
fi

# Codex surfaces permissionDecision=ask to the user. This is the important
# part: a reminder in AGENTS.md cannot enforce an approval boundary.
jq -n --arg reason "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "ask",
    permissionDecisionReason: $reason
  }
}'
