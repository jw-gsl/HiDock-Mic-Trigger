#!/bin/bash
# Codex PreToolUse hook: warn before any HiDock rebuild or direct replacement
# of the installed app. The project-local exec rule and the Xcode post-build
# approval dialog provide the actual approval boundary; this hook supplies the
# context about what the operation can interrupt.

set -euo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null || true)

# This hook is registered without a matcher so it remains effective across
# Codex tool-name changes. Inspect builds and commands that can replace the
# installed app.
if ! printf '%s' "$cmd" | grep -Eqi 'xcodebuild|codesign[[:space:]].*(--force|--sign).*HiDock Mic Trigger\.app|(^|[;&|[:space:]])(cp|mv|install)[[:space:]].*(/Applications|/Users/[^[:space:]]+/Applications)/.*HiDock Mic Trigger\.app'; then
  exit 0
fi

if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  exit 0
fi

non_deploying=false
if printf '%s' "$cmd" | grep -Eqi '(^|[[:space:]])CI=true([[:space:]]|$)' || [ "${CI:-}" = "true" ]; then
  non_deploying=true
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

if [ "$non_deploying" = "true" ]; then
  reason="A local HiDock validation build is requested with deployment disabled (CI=true). Approve starting the build?"
elif [ "${#busy[@]}" -gt 0 ]; then
  reason=$'HiDock rebuild will kill the running app and may interrupt in-flight work:\n'
  for item in "${busy[@]}"; do
    reason+="  • $item"$'\n'
  done
  reason+="Approve this rebuild?"
else
  reason="HiDock rebuild will replace the installed app and kill the running app. No active recording, extraction, or transcription was detected. Approve this rebuild?"
fi

# Codex PreToolUse currently supports systemMessage for this event. The
# approval prompt itself comes from the exec rule and the post-build script;
# permissionDecision is a Claude Code response field and is intentionally not
# emitted here.
jq -n --arg reason "$reason" '{systemMessage: $reason}'
