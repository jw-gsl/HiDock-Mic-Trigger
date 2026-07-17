#!/bin/bash
# Codex PostToolUse hook: after a successful xcodebuild, remind the agent to
# commit the complete rebuild cycle. This deliberately does not auto-commit.

set -euo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null || true)
if ! printf '%s' "$cmd" | grep -qi 'xcodebuild'; then
  exit 0
fi

# Codex may expose tool_response as a string or an object. tostring preserves
# stdout/output/content from either shape for the success-marker check.
out=$(printf '%s' "$input" | jq -r '(.tool_response // "") | tostring' 2>/dev/null || true)
if printf '%s' "$out" | grep -q 'BUILD SUCCEEDED'; then
  jq -n '{
    systemMessage: "BUILD SUCCEEDED — commit the complete rebuild cycle now, covering backend, UI, tests, and related changes. Skip only if the user explicitly said not to commit yet."
  }'
fi
