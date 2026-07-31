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

# Deployment became opt-in on 2026-07-31 (see the "Deploy to Applications" phase
# in hidock-mic-trigger/project.yml): a build without HIDOCK_DEPLOY=1 compiles and
# leaves /Applications alone. This hook exists to guard the *deploy*, so a build
# that cannot deploy has nothing to guard.
#
# That is also what made this dialog fire constantly. Env vars set inline on the
# command — `GITHUB_ACTIONS=true xcodebuild …` — apply to the child, not to this
# hook, so the check above never saw them and every compile-only verification
# build prompted. There were around twenty in one session.
if ! echo "$cmd" | grep -Eqi '(^|[[:space:]])HIDOCK_DEPLOY=(1|force)([[:space:]]|$)'; then
  exit 0
fi

# Belt and braces for the inline form of GITHUB_ACTIONS.
if echo "$cmd" | grep -Eqi '(^|[[:space:]])GITHUB_ACTIONS=true([[:space:]]|$)'; then
  exit 0
fi

# Collect busy-state signals so the prompt's reason explains what the
# rebuild is about to interrupt.
# Only work that would actually be *lost*. A status probe is not work: the app
# polls `extractor.py … status` and `plaud-status` every couple of minutes, so
# matching any extractor process meant the list was almost never empty and the
# dialog said BUSY nearly every time — the crying-wolf failure this reason string
# was rewritten on 2026-07-28 to end. Read-only probes finish in seconds and
# re-run on their own.
busy=()
if pgrep -f 'ffmpeg.*avfoundation' >/dev/null 2>&1; then
  # The mic trigger holds the HiDock input open with `-f avfoundation -i :<n>`;
  # there is no "HiDock" in its argv, so the old ffmpeg pattern never caught a
  # live capture — the costliest thing to lose.
  busy+=("a live recording — the mic trigger is holding the HiDock input open")
fi
if pgrep -f 'ffmpeg.*HiDock' >/dev/null 2>&1; then
  busy+=("an audio conversion")
fi
if pgrep -f 'extractor\.py.*(download|volume-import|mark-downloaded)' >/dev/null 2>&1; then
  busy+=("a device download")
fi
if pgrep -f 'transcription-pipeline.*transcribe.*\.py' >/dev/null 2>&1; then
  busy+=("a transcription or re-diarisation")
fi
if pgrep -f 'voice_training\.py' >/dev/null 2>&1; then
  busy+=("voice-library training")
fi

if [ ${#busy[@]} -gt 0 ]; then
  reason="This deploys and relaunches the HiDock app, and would kill in-flight work:"$'\n'
  for item in "${busy[@]}"; do
    reason+="  • $item"$'\n'
  done
else
  reason="This deploys and relaunches the HiDock app. Nothing is recording, downloading, converting or transcribing."
fi

# Ask through Claude Code rather than a native dialog.
#
# The osascript dialog this used to show was the one that needed clicking twice,
# every time: a dialog owned by a process the harness spawned opens without focus,
# so the first click is spent activating the window. It was also the *second*
# approval for one action — the deploy phase in project.yml already asks before it
# quits a running app — so the two together were the "popping up loads of times"
# problem.
#
# `ask` surfaces this where the user is already looking, and cannot be mis-focused.
# The guard is still meaningful in an autonomous session, which is why this is `ask`
# rather than dropping the hook: deployment is opt-in now, so reaching this point at
# all means something explicitly requested HIDOCK_DEPLOY.
jq -n --arg reason "$reason" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "ask",
    permissionDecisionReason: $reason
  }
}'
exit 0
