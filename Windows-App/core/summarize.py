"""Summarisation wrapper for the Windows app.

Mirrors the macOS app's summarisation feature. The heavy lifting lives in the
cross-platform ``shared`` package (the same code the dev ``transcribe.py
summarize`` subcommand and the Mac app drive):

  * ``shared.typed_summarize.summarise_typed`` — classify + template-summarise a
    transcript via the configured AI CLI, writing ``~/HiDock/Summaries/``.
  * ``shared.typed_summarize.available_templates`` — the user's template library.
  * ``shared.llm_cli`` — detect installed LLM CLIs (claude > codex > gemini > ollama).
  * ``shared.config_store`` — persisted ``[summarization]`` engine / auto-summarise.

The Windows UI calls these directly (in a background thread), exactly the way
``core/transcription.py`` already imports ``shared`` modules.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root (which contains ``shared/``) is importable, matching
# core/transcription.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Engine choices surfaced in the Summarisation Provider menu, in the same
# priority order the auto-detector uses.
ENGINE_CHOICES = ["auto", "claude", "codex", "gemini", "ollama", "none"]


def templates_dir() -> Path:
    from shared.typed_summarize import TEMPLATES_DIR
    return TEMPLATES_DIR


def summaries_dir() -> Path:
    from shared.typed_summarize import SUMMARIES_DIR
    return SUMMARIES_DIR


def list_templates() -> dict[str, Path]:
    """Clean template name -> file path, from ~/HiDock/Summary Templates/."""
    from shared.typed_summarize import available_templates
    return available_templates()


def configured_engine() -> str:
    """The persisted [summarization].engine choice (default 'auto')."""
    try:
        from shared.config_store import get_config
        return get_config().get("summarization", "engine", "auto") or "auto"
    except Exception:
        return "auto"


def set_configured_engine(engine: str) -> None:
    try:
        from shared.config_store import get_config
        get_config().set("summarization", "engine", engine)
    except Exception:
        pass


def auto_summarize_enabled() -> bool:
    try:
        from shared.config_store import get_config
        return bool(get_config().get("summarization", "auto_summarize", False))
    except Exception:
        return False


def set_auto_summarize(enabled: bool) -> None:
    try:
        from shared.config_store import get_config
        get_config().set("summarization", "auto_summarize", bool(enabled))
    except Exception:
        pass


def resolved_engine() -> str | None:
    """Which engine the current setting resolves to (None if unavailable).

    Mirrors the Mac app's ``resolvedAutoEngine`` so the UI can show e.g.
    "AI: Claude" and disable the Summarise actions when no CLI is installed.
    """
    try:
        from shared.llm_cli import get_engine
        eng = get_engine(configured_engine())
        return eng.name if eng else None
    except Exception:
        return None


def available_engines() -> list[str]:
    """Names of installed LLM CLIs, in priority order."""
    try:
        from shared.llm_cli import detect_engines
        return [e.name for e in detect_engines()]
    except Exception:
        return []


def summarize_transcript(
    transcript_path: str | Path,
    engine: str | None = None,
    force_template: str | None = None,
) -> dict:
    """Classify + template-summarise an existing transcript.

    Returns ``shared.typed_summarize.summarise_typed``'s dict:
    ``{"summarized": True, "summary_path", "type", "area", "title", "classified"}``
    or ``{"summarized": False, "error": ...}``. Never raises for the common
    'no LLM' / 'no text' cases.
    """
    from shared.typed_summarize import summarise_typed
    return summarise_typed(
        Path(transcript_path).expanduser(),
        engine_name=engine or configured_engine(),
        # stream=False: the Windows UI shows progress via the status bar /
        # (later) CLI pane, not by tailing stderr.
        stream=False,
        force_template=force_template,
    )


def read_summary(summary_path: str | Path) -> tuple[dict[str, str], str]:
    """Split a summary file into (frontmatter fields, markdown body).

    The summary's one-line-per-key YAML block carries type / area / title /
    recorded / classified / transcript — the same header the Mac viewer parses.
    """
    p = Path(summary_path)
    fields: dict[str, str] = {}
    body = ""
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return fields, body
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            block = raw[3:end].strip("\n")
            for line in block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fields[k.strip()] = v.strip()
            body = raw[end + 4:].lstrip("\n")
        else:
            body = raw
    else:
        body = raw
    return fields, body


def summary_type_of(summary_path: str | Path) -> str | None:
    """The classification 'type' of a summary file (for the type filter)."""
    fields, _ = read_summary(summary_path)
    t = fields.get("type")
    return t or None
