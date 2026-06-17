"""Type-aware, template-driven transcript summarisation via Claude Code.

Mirrors the (previously external) Cowork flow, but runs in-app through the local
`claude` CLI (shared.llm_cli) — no API keys. Steps:
  1. classify the transcript type → pick the matching template from
     ~/HiDock/Summary Templates/  (reuses the user's existing 14 templates),
  2. apply that template's extraction guidance to produce the summary,
  3. write a typed summary to ~/HiDock/Summaries/ named
     "<transcript-base> - <Type> - <Area> - <Desc>.md".

The "<transcript-base>" prefix lets the desktop app's findSummaryPath() locate
it by recording basename, while the rest stays human-friendly for Obsidian/
Cowork folder mapping.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from shared.llm_cli import get_engine, query_json, query_streaming


def _emit(msg: str) -> None:
    """Coarse progress marker → stderr (the desktop app streams stderr into
    the CLI pane, so the user sees 'how far it's progressed')."""
    print(msg, file=sys.stderr, flush=True)


def _stream_to_stderr(delta: str) -> None:
    sys.stderr.write(delta)
    sys.stderr.flush()


def _parse_headered(text: str) -> tuple[str, str, str]:
    """Parse the streamed response shaped as:
        AREA: <area>
        TITLE: <title>
        ---
        <markdown body>
    Falls back gracefully (Other / stem / whole text) if the model didn't
    follow the header convention."""
    area, title = "Other", ""
    lines = text.splitlines()
    sep_idx = None
    for i, ln in enumerate(lines[:8]):
        s = ln.strip()
        if s == "---":
            sep_idx = i
            break
        m = re.match(r"(?i)^AREA:\s*(.+)$", s)
        if m:
            area = m.group(1).strip()
            continue
        m = re.match(r"(?i)^TITLE:\s*(.+)$", s)
        if m:
            title = m.group(1).strip()
    body = "\n".join(lines[sep_idx + 1:]).strip() if sep_idx is not None else text.strip()
    # If the body came back fenced (```markdown … ```), unwrap it.
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n", "", body)
        body = re.sub(r"\n```$", "", body).strip()
    return area, title, body

HIDOCK = Path.home() / "HiDock"
RAW_DIR = HIDOCK / "Raw Transcripts"
TEMPLATES_DIR = HIDOCK / "Summary Templates"
SUMMARIES_DIR = HIDOCK / "Summaries"

# 12-type taxonomy hints — mirrors CoworkPromptView.swift's selection guidance.
TYPE_HINTS = {
    "1 on 1 Meeting": "two participants, informal catch-up or coaching",
    "Client or External Meeting": "mixed internal/external attendees",
    "Job Interview": "candidate + interviewer dynamic",
    "Project Sync": "technical/delivery focused, sprint or milestone review",
    "Stand Up Meeting": "short, status-update format",
    "Brainstorming": "ideation, open-ended exploration",
    "Podcast": "interview/conversation format for publication",
    "Retrospective Meeting": "what went well / what to improve",
    "Weekly Team Meeting": "recurring team sync with multiple topics",
    "Project kick-off": "new initiative, roles and milestones",
    "Training or Workshop": "learning/teaching session",
    "General Meeting": "fallback if no clear match",
}


def _clean_name(stem: str) -> str:
    """'👥 Job Interview' -> 'Job Interview' (drop a leading emoji/symbol)."""
    return re.sub(r"^[^A-Za-z0-9]+", "", stem).strip()


def _sanitize(value: str, maxlen: int = 60) -> str:
    cleaned = re.sub(r'[\\/:"*?<>|]+', "-", str(value)).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:maxlen].strip() or "Summary"


def available_templates() -> dict[str, Path]:
    """Map clean template name -> file path, from ~/HiDock/Summary Templates/."""
    out: dict[str, Path] = {}
    if TEMPLATES_DIR.exists():
        for p in sorted(TEMPLATES_DIR.glob("*.md")):
            out[_clean_name(p.stem)] = p
    return out


def _read_transcript_text(transcript_path: Path) -> str:
    base = transcript_path.stem
    wj = RAW_DIR / f"{base}_whisper.json"
    if wj.exists():
        try:
            import json
            data = json.loads(wj.read_text(encoding="utf-8"))
            segs = data.get("segments") or []
            text = data.get("text") or " ".join(s.get("text", "").strip() for s in segs)
            if text.strip():
                return text.strip()
        except Exception:
            pass
    if transcript_path.exists():
        md = transcript_path.read_text(encoding="utf-8")
        if md.startswith("---"):                 # strip a leading YAML frontmatter block
            end = md.find("\n---", 3)
            if end != -1:
                md = md[end + 4:]
        return md.strip()
    return ""


def classify(text: str, engine, names: list[str]) -> str:
    """Pick the best-matching template name via the LLM (falls back safely)."""
    menu = "\n".join(f"- {n}: {TYPE_HINTS.get(n, 'custom template')}" for n in names)
    prompt = (
        "Classify this transcript by choosing the single best-matching template "
        "name from the list (consider participants, topics, tone, structure).\n\n"
        f"Templates:\n{menu}\n\n"
        'Respond ONLY as JSON: {"template": "<exact name from the list>"}.\n\n'
        f"Transcript (first 6000 chars):\n{text[:6000]}"
    )
    res = query_json(prompt, engine=engine) or {}
    choice = str(res.get("template", "")).strip().lower()
    for n in names:
        if n.lower() == choice:
            return n
    return "General Meeting" if "General Meeting" in names else names[0]


def summarise_typed(
    transcript_path: Path,
    engine_name: str | None = None,
    stream: bool = True,
) -> dict:
    """Classify + template-summarise a transcript, write the typed summary file.

    When ``stream`` is True (default), the summary is generated with the
    streaming engine and Claude's output is forwarded to stderr live (plus
    coarse ``STAGE:`` markers), so the desktop app's CLI pane shows progress.
    The final JSON result still goes to stdout for the caller.

    Returns a JSON-able dict; never raises for the common 'no LLM' / 'no text'
    cases (returns {"summarized": False, "error": ...})."""
    transcript_path = Path(transcript_path)
    text = _read_transcript_text(transcript_path)
    if not text.strip():
        return {"summarized": False, "error": "No transcript text found"}

    engine = get_engine(engine_name or "auto")
    if engine is None:
        return {"summarized": False, "error": "No LLM engine available (is the `claude` CLI installed and signed in?)"}

    templates = available_templates()
    if not templates:
        return {"summarized": False, "error": f"No templates in {TEMPLATES_DIR}"}

    _emit("STAGE: Classifying transcript…")
    tname = classify(text, engine, list(templates.keys()))
    tcontent = templates[tname].read_text(encoding="utf-8")
    _emit(f"STAGE: Type: {tname} — summarising…")

    # Headered text (not JSON) so the streamed output is human-readable in the
    # CLI pane; we still recover the structured Area/Title from the header.
    prompt = (
        f"Summarise the transcript by completing this '{tname}' template, following ALL "
        "the 'Extraction guidance' notes inside it and keeping its section structure. "
        "Use only information present in the transcript.\n\n"
        "Begin your response with exactly these two header lines, then a line "
        "containing only '---', then the completed summary in markdown:\n"
        "AREA: <the Area you selected, or Other>\n"
        "TITLE: <a 3-6 word title>\n"
        "---\n"
        "<the completed summary in markdown>\n\n"
        f"=== TEMPLATE ===\n{tcontent}\n\n=== TRANSCRIPT ===\n{text}\n"
    )

    if stream:
        full = query_streaming(prompt, engine=engine, timeout=240, on_text=_stream_to_stderr)
        sys.stderr.write("\n")
        sys.stderr.flush()
    else:
        from shared.llm_cli import query
        full = query(prompt, engine=engine, timeout=240)

    if not full or not full.strip():
        return {"summarized": False, "error": "Summarisation produced no content"}

    area_raw, title_raw, md = _parse_headered(full)
    if not md.strip():
        return {"summarized": False, "error": "Summarisation produced no content"}

    _emit("STAGE: Writing summary…")
    area = _sanitize(area_raw or "Other", 40)
    desc = _sanitize(title_raw or transcript_path.stem, 50)
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    out = SUMMARIES_DIR / f"{transcript_path.stem} - {tname} - {area} - {desc}.md"
    out.write_text(md.rstrip() + "\n", encoding="utf-8")
    return {"summarized": True, "summary_path": str(out), "type": tname, "area": area, "title": desc}
