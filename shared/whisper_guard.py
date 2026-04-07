"""Whisper-Guard — anti-hallucination filtering for Whisper transcripts.

Whisper sometimes hallucinates text during silence, repeats phrases in
loops, or inserts foreign-script artifacts. This module provides a
multi-layer filtering pipeline that cleans transcripts while preserving
legitimate content.

Based on the 7-layer approach from silverstein/minutes, adapted for our
pipeline where we work with text output (not raw Whisper segments).

Filters (applied sequentially):
1. Consecutive deduplication — collapses A→A→A repeated lines
2. Interleaved deduplication — removes A→B→A→B loop patterns
3. Foreign-script filtering — strips language-mismatched characters
4. Noise marker collapse — merges bracketed noise markers like [music]
5. Trailing noise trimming — removes end-of-transcript artifacts
6. Minimum word count validation — flags near-empty transcripts
7. Repetition density check — detects high repetition rate as hallucination signal

Usage:
    from shared.whisper_guard import clean_transcript, FilterStats

    cleaned, stats = clean_transcript(text, language="en")
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass
class FilterStats:
    """Tracks what each filter removed for diagnostics."""

    original_lines: int = 0
    after_consecutive_dedup: int = 0
    after_interleaved_dedup: int = 0
    after_foreign_script: int = 0
    after_noise_collapse: int = 0
    after_trailing_trim: int = 0
    final_word_count: int = 0
    filters_triggered: list[str] = field(default_factory=list)
    is_likely_hallucination: bool = False


# Common Whisper hallucination phrases (language-agnostic noise)
_HALLUCINATION_PHRASES = {
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "see you next time",
    "see you in the next video",
    "bye bye",
    "you",
    "...",
    "the end",
    "subtitles by",
    "amara.org",
}

# Noise markers Whisper commonly inserts
_NOISE_MARKER_RE = re.compile(
    r"^\s*\[(?:music|applause|laughter|silence|inaudible|noise|blank_audio|"
    r"risas|musica|música|risa|aplausos|silencio)\]\s*$",
    re.IGNORECASE,
)

# Script detection ranges for common languages
_LATIN_SCRIPTS = {"LATIN", "COMMON", "INHERITED"}
_CJK_SCRIPTS = {"CJK", "HAN", "HIRAGANA", "KATAKANA", "HANGUL", "BOPOMOFO"}
_CYRILLIC_SCRIPTS = {"CYRILLIC", "COMMON", "INHERITED"}
_ARABIC_SCRIPTS = {"ARABIC", "COMMON", "INHERITED"}

_LANGUAGE_SCRIPTS = {
    "en": _LATIN_SCRIPTS,
    "es": _LATIN_SCRIPTS,
    "fr": _LATIN_SCRIPTS,
    "de": _LATIN_SCRIPTS,
    "it": _LATIN_SCRIPTS,
    "pt": _LATIN_SCRIPTS,
    "nl": _LATIN_SCRIPTS,
    "pl": _LATIN_SCRIPTS,
    "sv": _LATIN_SCRIPTS,
    "da": _LATIN_SCRIPTS,
    "no": _LATIN_SCRIPTS,
    "fi": _LATIN_SCRIPTS,
    "ja": _CJK_SCRIPTS | _LATIN_SCRIPTS,
    "zh": _CJK_SCRIPTS | _LATIN_SCRIPTS,
    "ko": _CJK_SCRIPTS | _LATIN_SCRIPTS,
    "ru": _CYRILLIC_SCRIPTS,
    "uk": _CYRILLIC_SCRIPTS,
    "ar": _ARABIC_SCRIPTS,
}


def _get_script(char: str) -> str:
    """Get the Unicode script name for a character."""
    try:
        name = unicodedata.name(char, "")
        # Extract script from Unicode name (e.g. "LATIN SMALL LETTER A" → "LATIN")
        if name:
            return name.split()[0]
    except ValueError:
        pass
    return "UNKNOWN"


def _normalize_line(line: str) -> str:
    """Normalize a line for comparison (strip whitespace, lowercase, remove punctuation)."""
    return re.sub(r"[^\w\s]", "", line.strip().lower())


# ── Filter 1: Consecutive Deduplication ────────────────────────────────────


def _filter_consecutive_dedup(lines: list[str]) -> list[str]:
    """Collapse consecutive identical lines (A→A→A becomes A)."""
    if not lines:
        return lines
    result = [lines[0]]
    for line in lines[1:]:
        if _normalize_line(line) != _normalize_line(result[-1]):
            result.append(line)
    return result


# ── Filter 2: Interleaved Deduplication ────────────────────────────────────


def _filter_interleaved_dedup(lines: list[str], window: int = 6) -> list[str]:
    """Remove A→B→A→B loop patterns within a sliding window.

    Detects when a short sequence of lines repeats in a cycle,
    which is a common Whisper hallucination pattern.
    """
    if len(lines) < 4:
        return lines

    result = []
    i = 0
    while i < len(lines):
        # Try to detect a repeating pattern of length 1-3
        found_loop = False
        for pattern_len in range(1, 4):
            if i + pattern_len * 2 > len(lines):
                continue
            pattern = [_normalize_line(lines[i + j]) for j in range(pattern_len)]
            # Check if pattern repeats at least once
            repeat_count = 1
            pos = i + pattern_len
            while pos + pattern_len <= len(lines):
                next_chunk = [_normalize_line(lines[pos + j]) for j in range(pattern_len)]
                if next_chunk == pattern:
                    repeat_count += 1
                    pos += pattern_len
                else:
                    break
            if repeat_count >= 3:
                # Keep one copy of the pattern, skip the rest
                result.extend(lines[i : i + pattern_len])
                i = pos
                found_loop = True
                break
        if not found_loop:
            result.append(lines[i])
            i += 1
    return result


# ── Filter 3: Foreign-Script Filtering ─────────────────────────────────────


def _filter_foreign_script(lines: list[str], language: str) -> list[str]:
    """Remove lines that are predominantly in the wrong script for the language."""
    allowed_scripts = _LANGUAGE_SCRIPTS.get(language)
    if not allowed_scripts:
        return lines  # Unknown language, skip this filter

    result = []
    for line in lines:
        text = line.strip()
        if not text:
            result.append(line)
            continue
        # Count characters by script
        total = 0
        foreign = 0
        for char in text:
            if char.isalpha():
                total += 1
                script = _get_script(char)
                if script not in allowed_scripts:
                    foreign += 1
        # Keep line if <50% foreign characters (or very short)
        if total < 3 or foreign / total < 0.5:
            result.append(line)
    return result


# ── Filter 4: Noise Marker Collapse ────────────────────────────────────────


def _filter_noise_markers(lines: list[str]) -> list[str]:
    """Collapse consecutive noise markers like [music] [music] into one."""
    if not lines:
        return lines
    result = []
    prev_was_noise = False
    for line in lines:
        if _NOISE_MARKER_RE.match(line):
            if not prev_was_noise:
                result.append(line)
            prev_was_noise = True
        else:
            prev_was_noise = False
            result.append(line)
    return result


# ── Filter 5: Trailing Noise Trimming ──────────────────────────────────────


def _filter_trailing_noise(lines: list[str]) -> list[str]:
    """Remove trailing lines that are just noise/hallucination artifacts."""
    while lines:
        last = lines[-1].strip().lower()
        # Remove trailing empty lines
        if not last:
            lines = lines[:-1]
            continue
        # Remove known hallucination phrases at the end
        if last in _HALLUCINATION_PHRASES:
            lines = lines[:-1]
            continue
        # Remove trailing noise markers
        if _NOISE_MARKER_RE.match(lines[-1]):
            lines = lines[:-1]
            continue
        break
    return lines


# ── Filter 6: Minimum Word Count ──────────────────────────────────────────


def _count_words(text: str) -> int:
    """Count real words (not noise markers or punctuation)."""
    # Remove noise markers
    cleaned = re.sub(r"\[[^\]]+\]", "", text)
    # Remove speaker labels
    cleaned = re.sub(r"\*\*[^*]+?\*\*\s*", "", cleaned)
    # Remove timestamps
    cleaned = re.sub(r"\[\d+:\d+[^\]]*\]", "", cleaned)
    words = cleaned.split()
    return len(words)


# ── Filter 7: Repetition Density Check ─────────────────────────────────────


def _check_repetition_density(lines: list[str]) -> float:
    """Calculate what fraction of lines are duplicates of another line.

    Returns a value 0.0-1.0. High values (>0.5) suggest hallucination.
    """
    if len(lines) < 3:
        return 0.0
    normalized = [_normalize_line(l) for l in lines if l.strip()]
    if not normalized:
        return 0.0
    unique = set(normalized)
    return 1.0 - (len(unique) / len(normalized))


# ── Main Pipeline ──────────────────────────────────────────────────────────


def clean_transcript(
    text: str,
    language: str = "en",
    min_words: int = 3,
) -> tuple[str, FilterStats]:
    """Run the full anti-hallucination pipeline on a transcript.

    Args:
        text: Raw transcript text (may include speaker labels, timestamps).
        language: ISO 639-1 language code (e.g. "en", "es", "ja").
        min_words: Minimum word count; below this the transcript is flagged.

    Returns:
        Tuple of (cleaned_text, filter_stats).
        If the transcript is likely hallucinated, cleaned_text may be empty
        and stats.is_likely_hallucination will be True.
    """
    stats = FilterStats()

    lines = text.split("\n")
    stats.original_lines = len(lines)

    # Filter 1: Consecutive dedup
    lines = _filter_consecutive_dedup(lines)
    stats.after_consecutive_dedup = len(lines)
    if stats.after_consecutive_dedup < stats.original_lines:
        stats.filters_triggered.append("consecutive_dedup")

    # Filter 2: Interleaved dedup
    lines = _filter_interleaved_dedup(lines)
    stats.after_interleaved_dedup = len(lines)
    if stats.after_interleaved_dedup < stats.after_consecutive_dedup:
        stats.filters_triggered.append("interleaved_dedup")

    # Filter 3: Foreign script
    lines = _filter_foreign_script(lines, language)
    stats.after_foreign_script = len(lines)
    if stats.after_foreign_script < stats.after_interleaved_dedup:
        stats.filters_triggered.append("foreign_script")

    # Filter 4: Noise marker collapse
    lines = _filter_noise_markers(lines)
    stats.after_noise_collapse = len(lines)
    if stats.after_noise_collapse < stats.after_foreign_script:
        stats.filters_triggered.append("noise_markers")

    # Filter 5: Trailing noise trim
    lines = _filter_trailing_noise(lines)
    stats.after_trailing_trim = len(lines)
    if stats.after_trailing_trim < stats.after_noise_collapse:
        stats.filters_triggered.append("trailing_noise")

    # Reassemble text
    cleaned = "\n".join(lines).strip()

    # Filter 6: Minimum word count
    stats.final_word_count = _count_words(cleaned)
    if stats.final_word_count < min_words:
        stats.filters_triggered.append("min_words")
        stats.is_likely_hallucination = True

    # Filter 7: Repetition density — check on original lines to detect
    # cases where dedup already collapsed massive repetition
    original_content_lines = [l for l in text.split("\n") if l.strip()]
    density = _check_repetition_density(original_content_lines)
    if density > 0.5:
        stats.filters_triggered.append("high_repetition")
        stats.is_likely_hallucination = True

    return cleaned, stats
