"""Read-only index + query over typed meeting summaries in ~/HiDock/Summaries/.

Summaries are markdown files written by ``shared.typed_summarize`` with a
one-line-per-key frontmatter block (type / area / title / recorded /
classified / transcript) followed by the markdown body. This module gives
``list`` / ``search`` / ``get`` / ``stats`` over them, used by both the MCP
server (agent access) and the ``notes`` CLI subcommand.

Everything is local file access — no network, no API keys.
"""
from __future__ import annotations

import re
from pathlib import Path

SUMMARIES_DIR = Path.home() / "HiDock" / "Summaries"


def _parse(path: Path) -> dict:
    """Parse one summary .md into {file, filename, type, area, title,
    recorded, source, body}. Frontmatter wins; for older summaries written
    before frontmatter existed, type/area/title/recorded are recovered from
    the filename pattern "<stem> - <Type> - <Area> - <Desc>.md"."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict[str, str] = {}
    body = text
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            key, sep, val = lines[i].partition(":")
            if sep:
                meta[key.strip()] = val.strip()
            i += 1
        body = "\n".join(lines[i + 1:]).strip()

    # Filename fallback (older, frontmatter-less summaries).
    parts = [p.strip() for p in path.stem.split(" - ")]
    fn_type = parts[1] if len(parts) >= 2 else ""
    fn_area = parts[2] if len(parts) >= 3 else ""
    fn_title = " - ".join(parts[3:]) if len(parts) >= 4 else (parts[-1] if parts else path.stem)
    fn_recorded = parts[0] if parts and re.match(r"\d{4}", parts[0]) else ""

    return {
        "file": str(path),
        "filename": path.name,
        "type": meta.get("type") or fn_type,
        "area": meta.get("area") or fn_area,
        "title": meta.get("title") or fn_title or path.stem,
        "recorded": meta.get("recorded") or fn_recorded,
        "source": meta.get("transcript", ""),
        "body": body,
    }


def all_summaries() -> list[dict]:
    """All summaries, newest-first (by `recorded`, falling back to filename)."""
    if not SUMMARIES_DIR.exists():
        return []
    out = [_parse(p) for p in SUMMARIES_DIR.glob("*.md")]
    out.sort(key=lambda s: (s["recorded"] or s["filename"]), reverse=True)
    return out


def list_summaries(type: str | None = None, area: str | None = None,
                   since: str | None = None, limit: int = 50) -> list[dict]:
    """Filter by classification type (exact, case-insensitive), area
    (substring), and/or `since` (ISO date/datetime string compare)."""
    res = all_summaries()
    if type:
        res = [s for s in res if s["type"].lower() == type.lower()]
    if area:
        res = [s for s in res if area.lower() in s["area"].lower()]
    if since:
        # `recorded` uses a space separator ("YYYY-MM-DD HH:MM:SS"); callers
        # may pass T-separated ISO. Normalize both sides before comparing.
        since_norm = since.replace("T", " ")
        res = [s for s in res
               if s["recorded"] and s["recorded"].replace("T", " ") >= since_norm]
    return res[:limit]


def search_summaries(query: str, limit: int = 20) -> list[dict]:
    """Full-text search over title/type/area/body; returns matches with a
    short snippet around the first hit."""
    q = query.lower().strip()
    res: list[dict] = []
    for s in all_summaries():
        hay = f"{s['title']} {s['type']} {s['area']} {s['body']}".lower()
        if q and q in hay:
            idx = s["body"].lower().find(q)
            if idx >= 0:
                snippet = s["body"][max(0, idx - 60): idx + 120]
            else:
                snippet = s["body"][:160]
            res.append({**s, "snippet": " ".join(snippet.split())})
            if len(res) >= limit:
                break
    return res


def get_summary(identifier: str) -> dict | None:
    """Fetch one summary by (case-insensitive substring of) filename, title,
    or source recording stem."""
    ident = identifier.lower().strip()
    for s in all_summaries():
        stem = Path(s["source"]).stem.lower() if s["source"] else ""
        if ident in s["filename"].lower() or ident in s["title"].lower() or (stem and ident in stem):
            return s
    return None


def summary_stats() -> dict:
    """Counts by type and area, plus the latest title."""
    res = all_summaries()
    by_type: dict[str, int] = {}
    by_area: dict[str, int] = {}
    for s in res:
        by_type[s["type"] or "(none)"] = by_type.get(s["type"] or "(none)", 0) + 1
        by_area[s["area"] or "(none)"] = by_area.get(s["area"] or "(none)", 0) + 1
    return {
        "total": len(res),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "by_area": dict(sorted(by_area.items(), key=lambda kv: -kv[1])),
        "latest": res[0]["title"] if res else None,
    }
