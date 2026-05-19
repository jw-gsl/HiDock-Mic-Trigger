# Security Review — HiDock Tools
Review date: 2026-04-25
Reviewer: Claude Code (automated scan + manual verification of every finding)
Scope: full repo at `/Users/jameswhiting/_git/hidock-tools/`

## Executive Summary

HiDock Tools is a hybrid macOS Swift + Python (extractor + transcription) + Windows PyQt6 application that handles raw USB protocol traffic, spawns subprocesses, and writes audio/transcripts to the user's home directory. The codebase shows generally good defensive engineering: filename validation rejects path traversal, subprocesses are invoked with argument arrays (not shell strings), JSON state files use atomic writes, and there are no `eval`/`exec`/`pickle`/unsafe-`yaml` calls.

The review surfaced **one real bug** worth fixing in the next session (a missing `payload_len` bounds check that could cause silent truncation or memory churn), plus a small handful of defence-in-depth improvements. **No critical RCE, no privilege escalation, no exposed secrets in git history.** A `feedback_token.txt` file does live in the working tree but is correctly `.gitignored` and was never committed — so the threat is local-disk only, not "anyone who clones the repo."

**Headline verdict:** No urgent security action required. One clear bug fix and two defence-in-depth improvements queued.

## Findings

### Critical
No findings.

### High
No findings.

### Medium

#### M1 — Missing `payload_len` bounds check in `read_raw_response_payload`
- **File:** `usb-extractor/extractor.py:534-535`
- **Code:**
  ```python
  payload_len = struct.unpack(">I", pending[start + 8 : start + 12])[0]
  total_needed = start + 12 + payload_len
  ```
- **Problem:** A malicious or malfunctioning USB device can send a frame whose `payload_len` field is up to `0xFFFFFFFF` (~4 GB). The function does not bounds-check it (the sibling function `parse_frame` at line 335-337 does — `if payload_len > MAX_PAYLOAD_SIZE: raise ...`), so the loop accumulates `pending += chunk` until either the deadline (default 5 s) hits or the impossibly large `total_needed` is reached.
- **Realistic blast radius:** bounded by USB bandwidth × `timeout_ms`. At USB 2.0 high-speed (~480 Mbps) and the default 5 s timeout, that's ~300 MB transient memory growth — uncomfortable but not RAM-exhausting. Worse, lines 542-544 then return whatever was received as a "best-effort" payload, so the caller receives silently truncated data instead of an error.
- **Fix:**
  ```python
  payload_len = struct.unpack(">I", pending[start + 8 : start + 12])[0]
  if payload_len > MAX_PAYLOAD_SIZE:
      raise HiDockProtocolError(f"payload too large: {payload_len} bytes (max {MAX_PAYLOAD_SIZE})")
  total_needed = start + 12 + payload_len
  ```
  Mirrors the existing guard in `parse_frame`.

#### M2 — `ffmpeg -safe 0` in concat-merge with state-file paths
- **File:** `hidock-mic-trigger/Sources/AppDelegate.swift:2095-2101` (and the equivalent in `Windows-App/ui/main_window.py:1204`)
- **Code:**
  ```swift
  let listPath = NSTemporaryDirectory() + "hidock-merge-list.txt"
  let listContent = entries.map { "file '\($0.recording.outputPath)'" }.joined(separator: "\n")
  try? listContent.write(toFile: listPath, atomically: true, encoding: .utf8)
  ...
  process.arguments = ["-y", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", outputPath]
  ```
- **Problem:** `-safe 0` disables ffmpeg's path-safety checks for entries in the concat list. Today the paths come from `entry.recording.outputPath`, which is `outputFolder + sanitised filename` — and the filename has been through the extractor's strict regex. So **a malicious USB device cannot directly inject a malicious path through this surface today.** But the design relies on every upstream caller continuing to sanitise; one future refactor that lets a user-typed string flow into `outputPath`, or a folder name containing a single quote, would break the concat-list quoting and produce broken merges (or, in the worst plausible case, ffmpeg interpreting filter-graph metacharacters).
- **Defence in depth fix:** drop `-safe 0` (use `-safe 1`, the default) and reject any entry whose `outputPath` contains characters that break the single-quoted concat format (`'`, newline, NUL).

### Low

#### L1 — Plaintext API token on disk in `feedback_token.txt`
- **File:** `feedback_token.txt` (94 bytes, contents: a `github_pat_…` token)
- **Verified:** the file is listed in `.gitignore` (line 46) and `git ls-files feedback_token.txt` returns nothing — i.e. it has **never been committed**. So cloning the repo does not expose the token. The risk is purely "the file sits in plaintext on this developer's disk."
- **Recommendation:** if the token is a real PAT used for live feedback submission, move it to the macOS Keychain via the existing app-secrets pattern. If it's a stub for local testing, add a comment to that effect so it doesn't get rotated unnecessarily.

#### L2 — Rebuild-approval sentinel uses world-writable `/tmp`
- **File:** `.claude/hooks/check-rebuild.sh:42-64`
- **Problem:** `/tmp/hidock-rebuild-approved` is in a world-writable directory. The 120 s mtime window narrows the race, and macOS `/tmp` has the sticky bit (only the owner can `rm` files), but on a multi-user workstation any local user could plant the sentinel just before a developer runs `xcodebuild` and bypass the gate.
- **Realistic blast radius:** single-user dev machines (the actual deployment) have zero attackers. Multi-user shared machines would expose this — but those aren't the target environment. **Low because the threat model rarely includes a hostile local user.**
- **Fix (defence in depth):** move the sentinel to `${HOME}/.hidock/rebuild-approved` (owner-only, `chmod 700` on the parent). Optionally use `mv` instead of `rm -f` so consumption is atomic against concurrent xcodebuild attempts.

### Informational

- **USB frame parsing complexity** (`extractor.py:384-458`). Bounds checks are present but scattered across multiple `cursor + N > len(data)` guards. No concrete bug found, but the parser would benefit from a fuzz harness covering malformed/truncated payloads. Worth filing as tech debt rather than a vulnerability.
- **`SAFE_FILENAME_RE` regex strictness.** The character class `[a-zA-Z0-9_\-. ]` is appropriate for HiDock-generated filenames (`2026Apr24-094349-Rec59.hda`) and explicitly does **not** match NUL bytes — so the regex itself enforces the missing-NUL-check that an earlier draft of this report flagged. Verified by direct test: `SAFE_FILENAME_RE.match("file.hda\x00.mp3")` returns `None`.
- **No insecure deserialisation.** No `pickle.load`, no `yaml.load` (unsafe variant), no `eval`/`exec` of untrusted input. JSON is decoded via `JSONDecoder()` / `json.load()` only.
- **No hardcoded API keys in committed code.** The only token-shaped string is the plaintext file above (gitignored, never committed).
- **Subprocess invocation is uniformly safe.** Every `Process.arguments`, `subprocess.run`, and ffmpeg call uses argument arrays — never `shell=True` or string concatenation into a shell.
- **Atomic state writes.** `state.json` and `config.json` use a temp-file + `os.replace()` pattern, which is correct.
- **No transcripts or credentials logged.** Verified by sampling `log()` call sites in Swift and `print(..., file=sys.stderr)` in Python.

## Defensive Measures Already In Place (retain)

- `validate_filename` (`extractor.py:157`) — explicit reject list (`/`, `\`, `..`) plus regex match.
- `_safe_resolve` (`extractor.py:1441`) — guards volume-import paths with `Path.resolve()` containment check.
- `MAX_PAYLOAD_SIZE = 100 MB` enforced in `parse_frame`.
- `transcriptionSubprocess` is killed via `SIGTERM` then `SIGKILL` after a 2-second grace, preventing zombies.
- `xcodebuild` cannot run while transcription/extractor/recording is in flight, thanks to the `check-rebuild.sh` PreToolUse hook.

## Recommended Next Steps (priority order)

1. **M1 fix** — add the `payload_len > MAX_PAYLOAD_SIZE` check in `read_raw_response_payload`. ~2 lines.
2. **M2 fix** — drop `-safe 0` from the merge `ffmpeg` invocation; add a path-character guard. ~5 lines, both Mac and Windows.
3. **L1 cleanup** — decide whether `feedback_token.txt` is real (move to Keychain) or stub (annotate).
4. **L2 cleanup** — relocate the rebuild-approval sentinel to `~/.hidock/`.
5. **Tech debt** — add a unit test that fuzzes `parse_frame` and `read_raw_response_payload` with truncated and oversized payloads to catch any future regression.

## Remediation Status (applied 2026-04-25)

- **M1 fixed** in both `usb-extractor/extractor.py:534` and `Windows-Script/extractor.py:477`. Bounds check matches `parse_frame`'s existing guard. 103 extractor unit tests pass post-change.
- **M2 fixed** in `hidock-mic-trigger/Sources/AppDelegate.swift` (merge path) and `Windows-App/ui/main_window.py:_do_merge`. Kept `-safe 0` (required for absolute paths — `-safe 1` would reject every recording path and break Merge entirely); added pre-flight rejection of paths containing `'`, `\n`, `\r`, `\\`, or `\0`. Mac path also now captures ffmpeg stderr instead of `/dev/null` so failures surface a real error. Pre-existing HiDock filenames pass the regex's `[a-zA-Z0-9_\-. ]+` whitelist, so no legitimate input is blocked.
- **L1 fixed** — `chmod 600 feedback_token.txt`. File is now owner-read-only. (No git-history scrub needed: confirmed never committed.)
- **L2 NOT applied.** Moving the rebuild-approval sentinel changes the contract of a hook the user explicitly approved this session; deferred pending separate decision. On a single-user dev machine the threat model is empty anyway.

USB download path was left untouched — none of the fixes flow through `download_one`, `download_new`, or `pull_file`. The M1 bounds check is on the response-payload reader (catalog queries only); legitimate catalogs are ~8 KB so the 100 MB ceiling has 1000× headroom.

## Discrepancies vs. the initial automated pass

For honesty's sake — the first sub-agent pass overstated several findings. After verification:
- "Critical: payload_len memory exhaustion" → **Medium** (bounded by USB bandwidth × deadline; realistic worst case is ~300 MB transient and silent truncation, not OOM).
- "High: hardcoded GitHub token in repo" → **Low** (file is `.gitignore`d and was never committed; threat is local-disk only, not git-history).
- "High: ffmpeg -safe 0" → **Medium** (upstream filename validation defangs the obvious injection; remaining concern is brittleness for future changes).
- "High: TOCTOU on /tmp sentinel" → **Low** (single-user dev machine has no attacker; macOS /tmp sticky bit blunts the race further).
- "Medium: missing NUL-byte check in `validate_filename`" → **Invalid** (`SAFE_FILENAME_RE` already rejects NUL). Removed.
- "Medium: bounds-check off-by-one in frame parser" → **Informational** (no concrete bug demonstrated; flagged as fuzz-test candidate).
