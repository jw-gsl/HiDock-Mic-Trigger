#!/bin/bash
# PreToolUse hook: intercept Bash commands that would rebuild the HiDock
# Mac app. The "Deploy to Applications" post-build script kills the
# running app and any child subprocesses (ffmpeg, extractor.py,
# transcribe.py), so a rebuild mid-work silently aborts the user.
#
# Approval mechanism: show the same native macOS approval dialog used by the
# Xcode post-build deployment step. The hook returns `allow` only when the
# user clicks "Approve Build"; cancellation, dialog failure, or headless
# execution all fail closed with `deny`.
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

if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  exit 0
fi

# A local validation build may set CI=true to suppress deployment. It still
# needs the build approval; only an actual headless CI runner should bypass
# the client-side dialog.
non_deploying=false
if [ "${CI:-}" = "true" ] || echo "$cmd" | grep -Eqi '(^|[[:space:]])CI=true([[:space:]]|$)'; then
  non_deploying=true
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

if [ "$non_deploying" = "true" ]; then
  reason="A local HiDock validation build is requested. Deployment is disabled (CI=true), but approve starting the build?"
elif [ ${#busy[@]} -gt 0 ]; then
  reason="HiDock rebuild will kill the running app AND in-flight work:"$'\n'
  for item in "${busy[@]}"; do
    reason+="  • $item"$'\n'
  done
  reason+="Approve rebuild?"
else
  reason="HiDock rebuild will kill the running app (idle — no ffmpeg/extractor/transcription detected). Approve rebuild?"
fi

# Keep the hook-level approval separate from the post-build deployment
# approval: this one authorises starting the build; the Xcode script still
# asks before replacing the installed app.
decision=$(
  /usr/bin/osascript \
    -e 'on run argv' \
    -e 'set reason to item 1 of argv' \
    -e 'try' \
    -e 'set answer to button returned of (display dialog reason buttons {"Cancel", "Approve Build"} default button "Cancel" cancel button "Cancel" with title "Approve HiDock rebuild")' \
    -e 'return answer' \
    -e 'on error' \
    -e 'return "Cancel"' \
    -e 'end try' \
    -e 'end run' \
    "$reason" 2>/dev/null || true
)

if [ "$decision" = "Approve Build" ]; then
  jq -n --arg reason "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "allow",
      permissionDecisionReason: $reason
    }
  }'
else
  jq -n --arg reason "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: ($reason + "\n\nBuild cancelled in the macOS approval dialog.")
    }
  }'
fi
exit 0
