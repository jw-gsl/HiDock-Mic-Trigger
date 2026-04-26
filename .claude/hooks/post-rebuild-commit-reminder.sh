#!/bin/bash
# PostToolUse hook for Bash. After a successful xcodebuild, surface a
# system message reminding Claude to commit the changes with a focused,
# single-purpose message — per the commit-on-rebuild policy.
#
# Why a reminder, not an auto-commit: a logical commit needs a thoughtful
# message describing what just shipped. A hook can't write that. It also
# can't tell whether the user wanted to test more before committing.
# Reminder + memory-driven message authoring is the right division of
# labour: hook enforces "don't forget"; Claude writes the message.

input=$(cat)
cmd=$(echo "$input" | jq -r '.tool_input.command // ""')
if ! echo "$cmd" | grep -qi xcodebuild; then
  exit 0
fi

# Pull stdout from the Bash result. PostToolUse exposes tool_response;
# Bash puts the captured output under tool_response.stdout (for newer
# Claude Code versions) or directly in the response string for older.
# Try both shapes.
out=$(echo "$input" | jq -r '
  .tool_response.stdout // .tool_response.output // ""
')

if echo "$out" | grep -q "BUILD SUCCEEDED"; then
  # systemMessage is rendered into the conversation so it's hard to miss.
  # Keep the text short and prescriptive.
  cat <<'JSON'
{"systemMessage": "BUILD SUCCEEDED — commit the changes now. The message should comprehensively cover EVERYTHING that changed in this rebuild cycle (backend + UI + tests + tweaks that shipped together). Per-cycle granularity is the goal, not minimalism within a cycle. See feedback_ask_before_rebuild.md. Skip only if the user explicitly says 'don't commit yet'."}
JSON
fi

exit 0
