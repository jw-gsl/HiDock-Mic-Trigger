"""Tests for state.py — atomic load/save of transcription state."""
import json

from state import load_state, save_state


class TestLoadState:
    def test_missing_file_returns_default(self):
        state = load_state()
        assert state == {"transcriptions": {}}

    def test_corrupt_json_returns_default(self, tmp_path, monkeypatch):
        import state as state_mod
        bad = tmp_path / "bad_state.json"
        bad.write_text("{not valid json!!!")
        monkeypatch.setattr(state_mod, "STATE_PATH", bad)
        assert load_state() == {"transcriptions": {}}

    def test_valid_json_round_trip(self):
        data = {
            "transcriptions": {
                "rec001.mp3": {
                    "status": "completed",
                    "source_path": "/tmp/rec001.mp3",
                    "duration_s": 12.3,
                }
            }
        }
        save_state(data)
        loaded = load_state()
        assert loaded == data

    def test_empty_transcriptions_round_trip(self):
        save_state({"transcriptions": {}})
        assert load_state() == {"transcriptions": {}}


class TestSaveState:
    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        import state as state_mod
        nested = tmp_path / "deep" / "nested" / "state.json"
        monkeypatch.setattr(state_mod, "STATE_PATH", nested)
        save_state({"transcriptions": {"a.mp3": {"status": "completed"}}})
        assert nested.exists()
        assert json.loads(nested.read_text())["transcriptions"]["a.mp3"]["status"] == "completed"

    def test_atomic_write_no_partial_on_error(self, tmp_path, monkeypatch):
        """If save_state fails mid-write, the old state should remain intact."""
        import state as state_mod
        path = tmp_path / "pipeline" / "state.json"
        monkeypatch.setattr(state_mod, "STATE_PATH", path)

        # Write initial state
        save_state({"transcriptions": {"old": {"status": "completed"}}})
        assert "old" in json.loads(path.read_text())["transcriptions"]

        # Attempt to save un-serializable data — should raise
        class BadObj:
            pass

        try:
            save_state({"transcriptions": {"bad": BadObj()}})
        except TypeError:
            pass

        # Original state should still be intact
        assert "old" in json.loads(path.read_text())["transcriptions"]

    def test_overwrite_existing(self):
        save_state({"transcriptions": {"a.mp3": {"status": "in_progress"}}})
        save_state({"transcriptions": {"a.mp3": {"status": "completed"}}})
        loaded = load_state()
        assert loaded["transcriptions"]["a.mp3"]["status"] == "completed"
