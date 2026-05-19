"""Tests for shared.config_store module."""
from __future__ import annotations



from shared.config_store import ConfigStore, _parse_toml, _serialize_toml


class TestParseToml:
    def test_empty(self):
        assert _parse_toml("") == {}

    def test_section_and_values(self):
        text = """
[general]
appearance = "dark"
count = 42
enabled = true
ratio = 3.14
"""
        result = _parse_toml(text)
        assert result["general"]["appearance"] == "dark"
        assert result["general"]["count"] == 42
        assert result["general"]["enabled"] is True
        assert result["general"]["ratio"] == 3.14

    def test_comments_ignored(self):
        text = """
# This is a comment
[section]
key = "value"  # inline comment
"""
        result = _parse_toml(text)
        assert result["section"]["key"] == "value"

    def test_multiple_sections(self):
        text = """
[a]
x = 1

[b]
y = 2
"""
        result = _parse_toml(text)
        assert result["a"]["x"] == 1
        assert result["b"]["y"] == 2

    def test_bare_string(self):
        text = """
[section]
key = auto
"""
        result = _parse_toml(text)
        assert result["section"]["key"] == "auto"

    def test_boolean_values(self):
        text = """
[flags]
a = true
b = false
"""
        result = _parse_toml(text)
        assert result["flags"]["a"] is True
        assert result["flags"]["b"] is False


class TestSerializeToml:
    def test_roundtrip(self):
        data = {
            "general": {
                "name": "test",
                "count": 5,
                "enabled": True,
                "ratio": 1.5,
            },
            "other": {
                "path": "/some/path",
            },
        }
        text = _serialize_toml(data)
        parsed = _parse_toml(text)
        assert parsed["general"]["name"] == "test"
        assert parsed["general"]["count"] == 5
        assert parsed["general"]["enabled"] is True
        assert parsed["other"]["path"] == "/some/path"

    def test_escapes_quotes(self):
        data = {"section": {"key": 'value with "quotes"'}}
        text = _serialize_toml(data)
        assert '\\"' in text
        parsed = _parse_toml(text)
        assert parsed["section"]["key"] == 'value with "quotes"'


class TestConfigStore:
    def test_defaults_loaded(self, tmp_path):
        config = ConfigStore(path=tmp_path / "config.toml")
        assert config.get("summarization", "engine") == "auto"
        assert config.get("transcription", "model") == "large-v3-turbo"
        assert config.get("obsidian", "enabled") is False

    def test_get_nonexistent(self, tmp_path):
        config = ConfigStore(path=tmp_path / "config.toml")
        assert config.get("nonexistent", "key") is None
        assert config.get("nonexistent", "key", "fallback") == "fallback"

    def test_set_and_get(self, tmp_path):
        config = ConfigStore(path=tmp_path / "config.toml")
        config.set("summarization", "engine", "claude")
        assert config.get("summarization", "engine") == "claude"

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "config.toml"

        # Save
        config1 = ConfigStore(path=path)
        config1.set("summarization", "engine", "ollama")
        config1.set("obsidian", "enabled", True)
        config1.set("obsidian", "vault_path", "/Users/me/vault")
        config1.save()

        assert path.exists()

        # Load in new instance
        config2 = ConfigStore(path=path)
        assert config2.get("summarization", "engine") == "ollama"
        assert config2.get("obsidian", "enabled") is True
        assert config2.get("obsidian", "vault_path") == "/Users/me/vault"

        # Defaults still present for unset values
        assert config2.get("transcription", "model") == "large-v3-turbo"

    def test_get_section(self, tmp_path):
        config = ConfigStore(path=tmp_path / "config.toml")
        section = config.get_section("summarization")
        assert "engine" in section
        assert "ollama_model" in section

    def test_as_dict(self, tmp_path):
        config = ConfigStore(path=tmp_path / "config.toml")
        data = config.as_dict()
        assert "general" in data
        assert "summarization" in data
        assert "obsidian" in data

    def test_missing_file_uses_defaults(self, tmp_path):
        config = ConfigStore(path=tmp_path / "nonexistent" / "config.toml")
        assert config.get("transcription", "language") == "en"

    def test_creates_parent_dirs_on_save(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "config.toml"
        config = ConfigStore(path=path)
        config.save()
        assert path.exists()
