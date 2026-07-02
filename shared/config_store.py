"""Unified TOML configuration — cross-platform settings store.

Replaces scattered UserDefaults (macOS) and Registry (Windows) settings
with a single TOML file that both platforms read/write.

Config file location:
    macOS:   ~/.config/hidock/config.toml
    Windows: %APPDATA%/HiDock/config.toml
    Linux:   ~/.config/hidock/config.toml

Precedence: compiled defaults -> TOML file -> runtime overrides
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# ── Defaults ────────────────────────────────────────────────────────────────

_DEFAULTS = {
    "general": {
        "recordings_folder": str(Path.home() / "HiDock" / "Recordings"),
        "transcripts_folder": str(Path.home() / "HiDock" / "Raw Transcripts"),
        "appearance": "auto",
    },
    "transcription": {
        "model": "large-v3-turbo",
        "language": "en",
        "diarization": False,
        "voice_library": True,
    },
    "summarization": {
        "engine": "auto",
        "ollama_model": "llama3.2",
        "custom_command": "",
        "auto_summarize": False,
    },
    "obsidian": {
        "enabled": False,
        "vault_path": "",
        "sync_strategy": "symlink",
        "subfolder": "Meetings",
        "wikilinks": True,
        "daily_notes": False,
    },
    "hooks": {
        "post_transcription": "",
    },
    "knowledge": {
        "losing_touch_days": 21,
        "stale_action_item_days": 14,
    },
}


def _config_dir() -> Path:
    """Platform-appropriate config directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "HiDock"
    return Path.home() / ".config" / "hidock"


def _config_path() -> Path:
    return _config_dir() / "config.toml"


# ── TOML Parser (minimal, no dependencies) ─────────────────────────────────
# We avoid requiring `tomli` or `tomllib` (Python 3.11+) so this works
# on Python 3.10 and in bundled builds without extra deps.


def _parse_toml(text: str) -> dict[str, Any]:
    """Parse a simple TOML file into a nested dict.

    Supports: [sections], key = "string", key = true/false, key = 123,
    key = 1.5. Does NOT support arrays-of-tables, inline tables, or
    multiline strings — those aren't needed for our config.
    """
    result: dict[str, Any] = {}
    current_section = result

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Section header
        if stripped.startswith("[") and stripped.endswith("]"):
            section_name = stripped[1:-1].strip()
            if section_name not in result:
                result[section_name] = {}
            current_section = result[section_name]
            continue

        # Key = value
        if "=" in stripped:
            eq_idx = stripped.index("=")
            key = stripped[:eq_idx].strip()
            value = stripped[eq_idx + 1:].strip()

            # Remove inline comments (only outside quotes)
            if value.startswith('"') and '"' in value[1:]:
                # Find closing quote, handling escaped quotes
                i = 1
                while i < len(value):
                    if value[i] == '\\' and i + 1 < len(value):
                        i += 2  # skip escaped char
                        continue
                    if value[i] == '"':
                        break
                    i += 1
                if i < len(value):
                    after = value[i + 1:]
                    if " #" in after:
                        value = value[:i + 1]
            elif " #" in value:
                value = value[:value.index(" #")].strip()

            current_section[key] = _parse_value(value)

    return result


def _parse_value(value: str) -> Any:
    """Parse a TOML value string."""
    # Boolean
    if value == "true":
        return True
    if value == "false":
        return False

    # Quoted string
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        inner = value[1:-1]
        return inner.replace('\\\\', '\\').replace('\\"', '"')

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    # Float
    try:
        return float(value)
    except ValueError:
        pass

    # Bare string (unquoted)
    return value


def _serialize_toml(data: dict[str, Any]) -> str:
    """Serialize a nested dict to TOML format."""
    lines = []
    # Top-level keys first (non-dict values)
    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_serialize_value(value)}")

    # Sections
    for section, values in data.items():
        if isinstance(values, dict):
            if lines:
                lines.append("")
            lines.append(f"[{section}]")
            for key, value in values.items():
                lines.append(f"{key} = {_serialize_value(value)}")

    lines.append("")  # trailing newline
    return "\n".join(lines)


def _serialize_value(value: Any) -> str:
    """Serialize a single value to TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return f'"{value}"'


# ── Config Store ────────────────────────────────────────────────────────────


class ConfigStore:
    """Cross-platform configuration store backed by TOML."""

    def __init__(self, path: Path | None = None):
        self._path = path or _config_path()
        self._data: dict[str, Any] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def load(self) -> None:
        """Load config from TOML file, merging with defaults."""
        self._data = _deep_copy_defaults()

        if self._path.exists():
            try:
                text = self._path.read_text(encoding="utf-8")
                file_data = _parse_toml(text)
                _deep_merge(self._data, file_data)
            except (OSError, UnicodeDecodeError):
                pass  # Use defaults on read error

        self._loaded = True

    def save(self) -> None:
        """Write current config to TOML file (atomically, so a crash
        mid-write can't leave a truncated config.toml)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = _serialize_toml(self._data)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=self._path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self._path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get(self, section: str, key: str, default: Any = None) -> Any:
        """Get a config value.

        Args:
            section: Config section (e.g. "summarization").
            key: Key within the section (e.g. "engine").
            default: Fallback if not found.

        Returns:
            The config value.
        """
        self._ensure_loaded()
        return self._data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        """Set a config value.

        Does NOT auto-save. Call ``save()`` to persist.

        Args:
            section: Config section.
            key: Key within the section.
            value: New value.
        """
        self._ensure_loaded()
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = value

    def get_section(self, section: str) -> dict[str, Any]:
        """Get all values in a section."""
        self._ensure_loaded()
        return dict(self._data.get(section, {}))

    def as_dict(self) -> dict[str, Any]:
        """Return the full config as a nested dict."""
        self._ensure_loaded()
        return dict(self._data)

    @property
    def path(self) -> Path:
        return self._path


def _deep_copy_defaults() -> dict[str, Any]:
    """Deep copy the defaults dict."""
    result = {}
    for key, value in _DEFAULTS.items():
        if isinstance(value, dict):
            result[key] = dict(value)
        else:
            result[key] = value
    return result


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base (mutates base)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── Singleton ───────────────────────────────────────────────────────────────

_instance: ConfigStore | None = None


def get_config() -> ConfigStore:
    """Get the global config store instance."""
    global _instance
    if _instance is None:
        _instance = ConfigStore()
    return _instance
