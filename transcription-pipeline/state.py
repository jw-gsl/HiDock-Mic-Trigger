"""Atomic state management for transcription pipeline."""
from __future__ import annotations

import contextlib
import fcntl
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from config import HIDOCK_ROOT

STATE_PATH = HIDOCK_ROOT / "transcription-pipeline" / "state.json"

# How long lock acquisition waits before giving up (seconds). Writers hold
# the lock only for a json.dump + rename, so contention windows are tiny.
LOCK_TIMEOUT_S = 2.0
_LOCK_POLL_S = 0.05


def _default_state() -> dict:
    # Fresh dict per call: a shared module-level default would alias its
    # inner "transcriptions" dict across callers, so mutations would leak
    # between loads.
    return {"transcriptions": {}}


def _lock_path() -> Path:
    # Derived per call (not cached) so tests that monkeypatch STATE_PATH
    # get a matching lockfile alongside it.
    return STATE_PATH.parent / (STATE_PATH.name + ".lock")


@contextlib.contextmanager
def _state_lock(timeout: float | None = None):
    """Best-effort exclusive lock on state.json via flock on a sidecar file.

    Yields True if the lock was acquired within `timeout`, False otherwise —
    callers decide whether to proceed unlocked (save_state) or skip
    (update_state). Non-blocking with a short retry loop so a status poll
    can never hang behind a wedged writer; flock is released automatically
    by the OS if the holder dies.
    """
    if timeout is None:
        timeout = LOCK_TIMEOUT_S  # read at call time so tests can tune it
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(_lock_path(), "w")
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(_LOCK_POLL_S)
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            except OSError:
                pass
        lock_file.close()


def load_state() -> dict:
    """Load transcription state from disk, returning default if missing/corrupt."""
    if not STATE_PATH.exists():
        return _default_state()
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return _default_state()


def _write_state(state: dict) -> None:
    """Atomically write state to disk (write-to-tmp then rename). No locking."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=STATE_PATH.parent, suffix=".tmp", prefix="state-"
    )
    try:
        with open(tmp_fd, "w") as f:
            json.dump(state, f, indent=2)
        Path(tmp_path).replace(STATE_PATH)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def save_state(state: dict) -> None:
    """Atomically write state to disk, serialised against other writers.

    Takes the state lock so concurrent save_state / update_state calls from
    other processes (e.g. a status poll pruning stale entries while a
    transcription completes) don't interleave. If the lock can't be acquired
    in time we still write — losing a completion record to a stuck lock
    would be worse than the (pre-existing) unlocked race.
    """
    with _state_lock() as locked:
        if not locked:
            print(
                "WARN: state.json lock busy; writing without lock",
                file=sys.stderr,
            )
        _write_state(state)


def update_state(mutator: Callable[[dict], None], timeout: float | None = None) -> bool:
    """Locked read-modify-write of state.json.

    Acquires the state lock, loads the freshest state, applies `mutator`
    (which mutates the dict in place), and writes it back — all under the
    lock, so the update can't clobber or be clobbered by a concurrent
    writer. Returns True if applied; False if the lock couldn't be acquired
    within `timeout` (nothing is written — callers should skip or retry).
    """
    with _state_lock(timeout=timeout) as locked:
        if not locked:
            return False
        state = load_state()
        mutator(state)
        _write_state(state)
        return True
