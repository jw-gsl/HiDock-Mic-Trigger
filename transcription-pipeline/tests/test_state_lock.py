"""Tests for state.json locking (update_state / save_state) and the
transcribe_cpp fixes that build on it: the cmd_status stale-transcript
guard + locked prune, and the SIGTERM in-flight handler."""
import fcntl
import json
import signal

import pytest

import state as state_mod
from state import load_state, save_state, update_state


def _hold_lock():
    """Acquire the state lockfile from a separate file description, simulating
    another process holding it. Returns the open file (close to release)."""
    state_mod.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(state_mod._lock_path(), "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


class TestUpdateState:
    def test_applies_mutation_and_returns_true(self):
        save_state({"transcriptions": {"a.mp3": {"status": "in_progress"}}})

        def mut(s):
            s["transcriptions"]["a.mp3"]["status"] = "completed"

        assert update_state(mut) is True
        assert load_state()["transcriptions"]["a.mp3"]["status"] == "completed"

    def test_starts_from_default_when_no_state_file(self):
        def mut(s):
            s["transcriptions"]["new.mp3"] = {"status": "failed"}

        assert update_state(mut) is True
        assert load_state()["transcriptions"]["new.mp3"]["status"] == "failed"

    def test_returns_false_and_writes_nothing_when_lock_held(self):
        save_state({"transcriptions": {"a.mp3": {"status": "completed"}}})
        holder = _hold_lock()
        try:
            called = []

            def mut(s):
                called.append(True)
                s["transcriptions"].pop("a.mp3", None)

            assert update_state(mut, timeout=0.2) is False
            assert not called  # mutator never ran
        finally:
            holder.close()
        assert "a.mp3" in load_state()["transcriptions"]

    def test_sees_freshest_state_under_lock(self):
        """update_state must reload inside the lock, not trust a stale copy."""
        save_state({"transcriptions": {"a.mp3": {"status": "completed"}}})
        # Simulate a concurrent writer changing state before our RMW runs
        state_mod._write_state(
            {"transcriptions": {"a.mp3": {"status": "completed"},
                                "b.mp3": {"status": "completed"}}}
        )

        def mut(s):
            s["transcriptions"].pop("a.mp3", None)

        assert update_state(mut) is True
        # b.mp3 (written concurrently) survives the prune of a.mp3
        assert load_state()["transcriptions"] == {"b.mp3": {"status": "completed"}}


class TestSaveStateLocking:
    def test_save_state_still_writes_when_lock_busy(self, monkeypatch, capsys):
        """save_state is best-effort: a stuck lock must not lose a completion."""
        monkeypatch.setattr(state_mod, "LOCK_TIMEOUT_S", 0.2)
        holder = _hold_lock()
        try:
            save_state({"transcriptions": {"a.mp3": {"status": "completed"}}})
        finally:
            holder.close()
        assert load_state()["transcriptions"]["a.mp3"]["status"] == "completed"
        assert "lock busy" in capsys.readouterr().err

    def test_lockfile_lives_alongside_state_json(self):
        save_state({"transcriptions": {}})
        lock = state_mod._lock_path()
        assert lock.parent == state_mod.STATE_PATH.parent
        assert lock.exists()


class TestCmdStatusStaleGuard:
    def _run_status(self, capsys):
        import transcribe_cpp
        transcribe_cpp.cmd_status(None)
        return json.loads(capsys.readouterr().out)

    def test_missing_transcript_reported_not_transcribed_and_pruned(self, tmp_path, capsys):
        save_state({"transcriptions": {
            "gone.mp3": {
                "status": "completed",
                "transcript_path": str(tmp_path / "nope" / "gone.md"),
            },
        }})
        lookup = self._run_status(capsys)
        assert lookup["gone.mp3"]["transcribed"] is False
        # stale entry pruned from state.json
        assert "gone.mp3" not in load_state()["transcriptions"]

    def test_existing_transcript_still_reported_transcribed(self, tmp_path, capsys):
        transcript = tmp_path / "kept.md"
        transcript.write_text("hello")
        save_state({"transcriptions": {
            "kept.mp3": {"status": "completed", "transcript_path": str(transcript)},
        }})
        lookup = self._run_status(capsys)
        assert lookup["kept.mp3"]["transcribed"] is True
        assert "kept.mp3" in load_state()["transcriptions"]

    def test_non_completed_entries_untouched(self, tmp_path, capsys):
        save_state({"transcriptions": {
            "busy.mp3": {"status": "in_progress", "transcript_path": str(tmp_path / "busy.md")},
        }})
        lookup = self._run_status(capsys)
        assert lookup["busy.mp3"]["transcribed"] is False
        assert "busy.mp3" in load_state()["transcriptions"]

    def test_prune_skipped_when_lock_busy(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(state_mod, "LOCK_TIMEOUT_S", 0.2)
        save_state({"transcriptions": {
            "gone.mp3": {
                "status": "completed",
                "transcript_path": str(tmp_path / "nope" / "gone.md"),
            },
        }})
        holder = _hold_lock()
        try:
            lookup = self._run_status(capsys)
        finally:
            holder.close()
        # Status still answers truthfully...
        assert lookup["gone.mp3"]["transcribed"] is False
        # ...but the prune was skipped rather than racing/blocking
        assert "gone.mp3" in load_state()["transcriptions"]


class TestSigtermHandler:
    def test_in_flight_entry_flipped_to_failed(self, monkeypatch):
        import transcribe_cpp
        save_state({"transcriptions": {
            "rec.mp3": {"status": "in_progress", "source_path": "/x/rec.mp3"},
        }})
        monkeypatch.setattr(transcribe_cpp, "_IN_FLIGHT", {"key": "rec.mp3"})
        with pytest.raises(SystemExit) as exc:
            transcribe_cpp._sigterm_handler(signal.SIGTERM, None)
        assert exc.value.code == 128 + signal.SIGTERM
        entry = load_state()["transcriptions"]["rec.mp3"]
        assert entry["status"] == "failed"
        assert "signal" in entry["last_error"]
        assert entry["source_path"] == "/x/rec.mp3"  # existing fields preserved

    def test_no_in_flight_leaves_state_alone(self, monkeypatch):
        import transcribe_cpp
        save_state({"transcriptions": {
            "rec.mp3": {"status": "completed"},
        }})
        monkeypatch.setattr(transcribe_cpp, "_IN_FLIGHT", None)
        with pytest.raises(SystemExit):
            transcribe_cpp._sigterm_handler(signal.SIGTERM, None)
        assert load_state()["transcriptions"]["rec.mp3"]["status"] == "completed"

    def test_flip_works_even_when_lock_busy(self, tmp_path, monkeypatch):
        """On lock timeout the handler falls back to an unlocked write —
        a possibly-racy 'failed' beats a permanently stuck 'in_progress'."""
        import transcribe_cpp
        monkeypatch.setattr(state_mod, "LOCK_TIMEOUT_S", 0.2)
        save_state({"transcriptions": {"rec.mp3": {"status": "in_progress"}}})
        monkeypatch.setattr(transcribe_cpp, "_IN_FLIGHT", {"key": "rec.mp3"})
        holder = _hold_lock()
        try:
            with pytest.raises(SystemExit):
                transcribe_cpp._sigterm_handler(signal.SIGTERM, None)
        finally:
            holder.close()
        assert load_state()["transcriptions"]["rec.mp3"]["status"] == "failed"
