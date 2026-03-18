"""Tests for core/state.py — atomic load/save."""
import json

from core.state import load_state, save_state


class TestLoadState:
    def test_missing_file_returns_default(self):
        assert load_state() == {"transcriptions": {}}

    def test_corrupt_json_returns_default(self, tmp_path, monkeypatch):
        import core.state as state_mod
        bad = tmp_path / "bad.json"
        bad.write_text("{corrupt!!!")
        monkeypatch.setattr(state_mod, "STATE_PATH", bad)
        assert load_state() == {"transcriptions": {}}

    def test_round_trip(self):
        data = {"transcriptions": {"rec.mp3": {"status": "completed", "duration_s": 5.0}}}
        save_state(data)
        assert load_state() == data


class TestSaveState:
    def test_creates_parent_dirs(self, tmp_path, monkeypatch):
        import core.state as state_mod
        nested = tmp_path / "a" / "b" / "state.json"
        monkeypatch.setattr(state_mod, "STATE_PATH", nested)
        save_state({"transcriptions": {}})
        assert nested.exists()

    def test_atomic_write_preserves_old_on_error(self, tmp_path, monkeypatch):
        import core.state as state_mod
        path = tmp_path / "pipe" / "state.json"
        monkeypatch.setattr(state_mod, "STATE_PATH", path)

        save_state({"transcriptions": {"old": {"status": "ok"}}})

        class Bad:
            pass

        try:
            save_state({"transcriptions": {"bad": Bad()}})
        except TypeError:
            pass

        assert "old" in json.loads(path.read_text())["transcriptions"]
