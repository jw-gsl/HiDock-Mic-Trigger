"""Small helpers for carrying word-level timing through the pipeline."""
from __future__ import annotations


_NO_SPACE_BEFORE = frozenset(",.!?;:%)]}»")
_NO_SPACE_AFTER = frozenset("([{«")


def normalise_timed_word(raw: dict, *, default_start: float = 0.0, default_end: float = 0.0) -> dict | None:
    """Return the stable sidecar representation for one timed word."""
    if not isinstance(raw, dict):
        return None
    text = raw.get("word") or raw.get("text") or ""
    text = str(text).strip()
    if not text:
        return None
    try:
        raw_start = raw.get("start")
        raw_end = raw.get("end")
        start = float(default_start if raw_start is None else raw_start)
        end = float(default_end if raw_end is None else raw_end)
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    word = {"word": text, "start": start, "end": end}
    if raw.get("confidence") is not None:
        try:
            word["confidence"] = float(raw["confidence"])
        except (TypeError, ValueError):
            pass
    return word


def timed_words(segment: dict) -> list[dict]:
    """Read either ``word`` or legacy ``text`` word entries from a segment."""
    words = []
    for raw in segment.get("words") or []:
        word = normalise_timed_word(
            raw,
            default_start=float(segment.get("start", 0.0)),
            default_end=float(segment.get("end", 0.0)),
        )
        if word is not None:
            words.append(word)
    return words


def words_to_text(words: list[dict]) -> str:
    """Reconstruct readable text without adding spaces before punctuation."""
    result = ""
    for raw in words:
        text = str(raw.get("word") or raw.get("text") or "").strip()
        if not text:
            continue
        if not result:
            result = text
        elif text[0] in _NO_SPACE_BEFORE or result[-1] in _NO_SPACE_AFTER:
            result += text
        else:
            result += " " + text
    return result


def aligned_tokens_to_words(tokens, *, default_start: float = 0.0, default_end: float = 0.0) -> list[dict]:
    """Collapse Parakeet SentencePiece tokens into timed display words.

    ``parakeet-mlx`` returns aligned token text after decoding ``▁`` to a
    leading space. A token list such as ``["P", "er", "f", "ect", "."]``
    is one spoken word, while ``" James"`` starts the next one. Preserve that
    boundary before normalising whitespace, then use the first/last token
    timestamps for the whole word.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    for raw in tokens or []:
        if not isinstance(raw, dict):
            continue
        raw_text = str(raw.get("word") or raw.get("text") or "")
        starts_new_word = bool(current and (
            raw_text[:1].isspace() or raw_text.startswith("▁")
        ))
        if starts_new_word:
            groups.append(current)
            current = []
        word = normalise_timed_word({
            **raw,
            "word": raw_text.replace("▁", " ").strip(),
        }, default_start=default_start, default_end=default_end)
        if word is not None:
            current.append(word)
    if current:
        groups.append(current)

    result: list[dict] = []
    for group in groups:
        if not group:
            continue
        word = {
            "word": "".join(item["word"] for item in group),
            "start": group[0]["start"],
            "end": group[-1]["end"],
        }
        confidences = [item.get("confidence") for item in group if item.get("confidence") is not None]
        if confidences:
            word["confidence"] = min(confidences)
        if word["word"]:
            result.append(word)
    return result
