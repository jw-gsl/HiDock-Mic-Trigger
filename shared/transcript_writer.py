"""Transcript writer — produces Markdown files with YAML frontmatter.

All transcription backends (PyTorch, whisper.cpp, Windows) should use this
module to write transcripts, ensuring a consistent format across platforms.

Output format:
    ---
    title: "Auto-generated title"
    type: meeting
    date: 2026-04-05T14:00:00+00:00
    duration: 234.5
    speakers: [Speaker 1, Speaker 2]
    source_device: HiDock H1
    source_file: recording.mp3
    model: large-v3-turbo
    action_items: []
    decisions: []
    key_points: []
    tags: []
    ---

    ## Transcript

    **Speaker 1:** Hello...
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _yaml_escape(value: str) -> str:
    """Escape a string for YAML output (quote if it contains special chars)."""
    if not value:
        return '""'
    needs_quoting = any(c in value for c in ':{}[]&*?|>!%@`#,\'') or value.startswith(('-', ' '))
    if needs_quoting or '\n' in value:
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(items: list[str], indent: int = 0) -> str:
    """Format a YAML list. Uses inline format for short lists, block for long."""
    prefix = " " * indent
    if not items:
        return "[]"
    if len(items) <= 4 and all(len(s) < 40 for s in items):
        inner = ", ".join(_yaml_escape(s) for s in items)
        return f"[{inner}]"
    lines = []
    for item in items:
        lines.append(f"{prefix}- {_yaml_escape(item)}")
    return "\n" + "\n".join(lines)


def _format_action_items(items: list[dict]) -> str:
    """Format action items as YAML block."""
    if not items:
        return "[]"
    lines = []
    for item in items:
        lines.append(f'  - task: {_yaml_escape(item.get("task", ""))}')
        if item.get("assignee"):
            lines.append(f'    assignee: {_yaml_escape(item["assignee"])}')
        if item.get("due"):
            lines.append(f'    due: {item["due"]}')
        lines.append(f'    status: {item.get("status", "open")}')
    return "\n" + "\n".join(lines)


def _format_decisions(items: list[dict]) -> str:
    """Format decisions as YAML block."""
    if not items:
        return "[]"
    lines = []
    for item in items:
        lines.append(f'  - text: {_yaml_escape(item.get("text", ""))}')
        if item.get("topic"):
            lines.append(f'    topic: {_yaml_escape(item["topic"])}')
    return "\n" + "\n".join(lines)


def build_frontmatter(
    *,
    title: str = "",
    doc_type: str = "meeting",
    date: str | None = None,
    duration: float | None = None,
    speakers: list[str] | None = None,
    source_device: str = "",
    source_file: str = "",
    model: str = "",
    action_items: list[dict] | None = None,
    decisions: list[dict] | None = None,
    key_points: list[str] | None = None,
    tags: list[str] | None = None,
) -> str:
    """Build YAML frontmatter string.

    Returns:
        String including opening and closing ``---`` delimiters.
    """
    if date is None:
        date = datetime.now(timezone.utc).isoformat()

    lines = ["---"]
    lines.append(f"title: {_yaml_escape(title)}")
    lines.append(f"type: {doc_type}")
    lines.append(f"date: {date}")
    if duration is not None:
        lines.append(f"duration: {duration}")
    lines.append(f"speakers: {_yaml_list(speakers or [])}")
    if source_device:
        lines.append(f"source_device: {_yaml_escape(source_device)}")
    if source_file:
        lines.append(f"source_file: {_yaml_escape(source_file)}")
    if model:
        lines.append(f"model: {_yaml_escape(model)}")
    lines.append(f"action_items: {_format_action_items(action_items or [])}")
    lines.append(f"decisions: {_format_decisions(decisions or [])}")
    lines.append(f"key_points: {_yaml_list(key_points or [])}")
    lines.append(f"tags: {_yaml_list(tags or [])}")
    lines.append("---")
    return "\n".join(lines)


def auto_title(text: str, max_words: int = 10) -> str:
    """Generate a short title from the first sentence of the transcript.

    Takes the first sentence (up to ``max_words`` words) and cleans it up.
    Falls back to "Untitled recording" if the text is empty.
    """
    if not text or not text.strip():
        return "Untitled recording"

    # Take first sentence or first N words
    first_line = text.strip().split("\n")[0]
    # Remove speaker labels like **Speaker 1:**
    first_line = re.sub(r"\*\*[^*]+?\*\*\s*", "", first_line)
    # Remove timestamps like [00:00-00:45]
    first_line = re.sub(r"\[\d+:\d+[^\]]*\]\s*", "", first_line)

    words = first_line.split()
    if not words:
        return "Untitled recording"

    title_words = words[:max_words]
    title = " ".join(title_words)
    if len(words) > max_words:
        title += "..."
    return title


def extract_speakers_from_diarized(diarized_result: dict) -> list[str]:
    """Extract display-name speaker list from a diarization result dict."""
    if not diarized_result:
        return []
    names = diarized_result.get("speaker_names", {})
    # Return display names, ordered by first appearance
    seen = []
    for seg in diarized_result.get("segments", []):
        spk = seg.get("speaker", "")
        display = names.get(spk, spk)
        if display and display not in seen:
            seen.append(display)
    return seen


def format_diarized_transcript(diarized_result: dict) -> str:
    """Format a diarized result dict into readable markdown transcript body.

    Args:
        diarized_result: Dict with ``segments`` and ``speaker_names`` keys.

    Returns:
        Markdown-formatted transcript text.
    """
    if not diarized_result or not diarized_result.get("segments"):
        return ""

    names = diarized_result.get("speaker_names", {})
    lines = []
    current_speaker = None

    for seg in diarized_result["segments"]:
        display_name = names.get(seg["speaker"], seg["speaker"])
        text = seg.get("text", "").strip()
        if not text:
            continue

        if display_name != current_speaker:
            if lines:
                lines.append("")
            # Include timestamp if available
            start = seg.get("start")
            end = seg.get("end")
            if start is not None and end is not None:
                ts = f"[{_format_timestamp(start)}-{_format_timestamp(end)}] "
            else:
                ts = ""
            lines.append(f"{ts}**{display_name}:** {text}")
            current_speaker = display_name
        else:
            lines.append(text)

    return "\n".join(lines)


def _format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def write_transcript(
    output_path: Path,
    transcript_text: str,
    *,
    source_path: Path | None = None,
    model: str = "",
    duration_s: float | None = None,
    diarized_result: dict | None = None,
    source_device: str = "",
    summary: dict | None = None,
) -> Path:
    """Write a complete transcript file with YAML frontmatter.

    This is the main entry point. All transcription backends should call this.

    Args:
        output_path: Where to write the .md file.
        transcript_text: Plain transcript text (used if no diarized_result).
        source_path: Original audio file path.
        model: Whisper model name.
        duration_s: Transcription wall-clock time.
        diarized_result: Optional diarization output dict.
        source_device: Device name (e.g. "HiDock H1").
        summary: Optional LLM summary dict with keys:
            title, action_items, decisions, key_points, tags.

    Returns:
        The output_path written to.
    """
    # Determine transcript body
    if diarized_result and diarized_result.get("segments"):
        body = format_diarized_transcript(diarized_result)
        speakers = extract_speakers_from_diarized(diarized_result)
    else:
        body = transcript_text
        speakers = []

    # Build metadata from summary or defaults
    summary = summary or {}
    title = summary.get("title") or auto_title(body)
    action_items = summary.get("action_items", [])
    decisions = summary.get("decisions", [])
    key_points = summary.get("key_points", [])
    tags = summary.get("tags", [])

    frontmatter = build_frontmatter(
        title=title,
        date=datetime.now(timezone.utc).isoformat(),
        duration=duration_s,
        speakers=speakers,
        source_device=source_device,
        source_file=source_path.name if source_path else "",
        model=model,
        action_items=action_items,
        decisions=decisions,
        key_points=key_points,
        tags=tags,
    )

    # Compose full document
    content = f"{frontmatter}\n\n## Transcript\n\n{body}\n"

    # Append summary section if we have LLM output
    if summary.get("summary_text"):
        content += f"\n## Summary\n\n{summary['summary_text']}\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a transcript markdown file.

    Handles our specific YAML structure including nested block lists
    (action_items, decisions) with sub-keys.

    Args:
        text: Full file content.

    Returns:
        Tuple of (frontmatter_dict, body_text). Returns ({}, text) if
        no frontmatter found.
    """
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
    end_idx = text.find("\n---", 3)
    if end_idx == -1:
        return {}, text

    yaml_block = text[4:end_idx]  # skip opening ---\n
    body = text[end_idx + 4:].lstrip("\n")  # skip closing ---\n

    meta: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list | None = None
    current_dict: dict | None = None  # for block list items with sub-keys

    for line in yaml_block.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Detect indentation level
        indent = len(line) - len(line.lstrip())

        # Sub-key of a list item (e.g. "    assignee: Alice")
        if indent >= 4 and current_dict is not None and ":" in stripped and not stripped.startswith("-"):
            colon_idx = stripped.index(":")
            k = stripped[:colon_idx].strip()
            v = stripped[colon_idx + 1:].strip().strip('"')
            current_dict[k] = v
            continue

        # Block list item starting with "- " under a key
        if stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
                meta[current_key] = current_list

            item_text = stripped[2:].strip()

            # Check if it's a dict item (e.g. "- task: Do something")
            if ":" in item_text:
                colon_idx = item_text.index(":")
                k = item_text[:colon_idx].strip()
                v = item_text[colon_idx + 1:].strip().strip('"')
                current_dict = {k: v}
                current_list.append(current_dict)
            else:
                current_dict = None
                current_list.append(item_text.strip('"'))
            continue

        # Top-level key: value pair
        if ":" in stripped and not stripped.startswith("-"):
            colon_idx = stripped.index(":")
            key = stripped[:colon_idx].strip()
            value = stripped[colon_idx + 1:].strip()

            current_key = key
            current_list = None
            current_dict = None

            # Inline list: [a, b, c]
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                if inner.strip():
                    items = [v.strip().strip('"') for v in inner.split(",")]
                    meta[key] = items
                else:
                    meta[key] = []
            elif value == "":
                # Value will come on next lines (block list)
                meta[key] = ""
            elif value.strip('"'):
                clean = value.strip('"')
                # Only parse as number if the original value was NOT quoted
                if not (value.startswith('"') and value.endswith('"')):
                    try:
                        meta[key] = float(clean) if "." in clean else int(clean)
                    except ValueError:
                        meta[key] = clean
                else:
                    meta[key] = clean
            else:
                meta[key] = ""

    return meta, body
