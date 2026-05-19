"""Detect groups of recordings that look like a single conversation
split across multiple files. The split could happen for several reasons:
the mic trigger releasing and re-acquiring (most common now), the app
having been rebuilt mid-recording (fixed since 2026-04-25 via the
PreToolUse hook, but historical files still carry the artefact), or
the device briefly dropping. The detector doesn't care about the
cause — it just looks for "two consecutive recordings on the same
device, small wall-clock gap, transcripts that read as one
conversation continuing."

Signal it uses:
  1. Same device (product_id) — splits don't cross devices.
  2. Small wall-clock gap between piece N's end and piece N+1's start
     (< 90s candidate, < 30s strong).
  3. Plausible single-meeting total duration (the user's typical meeting
     length is ~30 min, so 20-75 min totals are weighted highest).
  4. Transcript-boundary continuity: piece N ends mid-sentence AND
     piece N+1's opening shares content terms — strong "same
     conversation" signal. Heuristic-only today; the `llm_score()`
     hook is left unused but ready for a future plug-in to a local
     Claude Code terminal that can rate the boundary more accurately.

Two persistent state files cooperate:
  - state.json: device-side filename -> downloaded entry (mp3 path,
    product_id, length). Read-only here.
  - merge_candidates.json: sidecar this module owns. Stores
    `dismissed_pairs` (the user has explicitly said "these don't go
    together") and `scanned_files` (recordings already evaluated, so a
    rescan can skip work when no new files arrived).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ── Storage paths ──────────────────────────────────────────────────────────

HIDOCK_ROOT = Path.home() / "HiDock"
RAW_TRANSCRIPTS_DIR = HIDOCK_ROOT / "Raw Transcripts"
RECORDINGS_DIR = HIDOCK_ROOT / "Recordings"
MERGE_STATE_PATH = HIDOCK_ROOT / "merge_candidates.json"

# ── Filename parsing ───────────────────────────────────────────────────────

# HiDock filename: YYYYMmm-HHMMSS-Rec##.mp3 (e.g. 2026Apr22-203106-Rec51.mp3).
FN_RE = re.compile(r"^(\d{4})([A-Za-z]{3})(\d{2})-(\d{2})(\d{2})(\d{2})-Rec(\d+)\.mp3$")
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def parse_recording_timestamp(filename: str) -> datetime | None:
    m = FN_RE.match(filename)
    if not m:
        return None
    yr, mon, day, hh, mm, ss, _ = m.groups()
    try:
        return datetime(int(yr), MONTHS[mon], int(day), int(hh), int(mm), int(ss))
    except (KeyError, ValueError):
        return None


# ── Scoring & continuity ───────────────────────────────────────────────────

# Stop words to ignore when computing transcript-boundary overlap. Short
# common words bias the heuristic toward false positives ("the/a/in").
_STOP = set("the a an and or but of to in on at for is are was were be been being "
            "have has had do does did i you we they it this that yes no okay ok "
            "so um uh just like really and's it's i'm you're we're they're".split())

# Phrases that signal a call IS ending — sometimes without terminal
# punctuation. Without these, "see you monday then cheers" would look
# like a continuation just because the speaker didn't end with "."/"!"/"?".
_FAREWELL_PHRASES = (
    "bye", "cheers", "thanks", "thank you", "see you", "see ya",
    "talk later", "talk soon", "speak soon", "speak to you",
    "talk to you", "have a good", "have a great", "have a nice",
    "take care", "catch you later", "goodbye",
)

# Phrases that signal a NEW call is starting — typically the next
# transcript opens with a greeting. Mirror image of farewells.
_GREETING_PHRASES = (
    "hello", "hi,", "hi ", "hi.", "hey,", "hey ", "hey.",
    "good morning", "good afternoon", "good evening",
    "morning everyone", "afternoon everyone",
    "hi everyone", "hello everyone", "hi guys", "hi all",
    "can you hear me",
)


def _ends_with_farewell(text: str) -> bool:
    """True if the LAST few words of `text` contain a known farewell
    phrase. Used to override the naive ends-mid-sentence check, which
    fired on things like "see you monday then cheers" purely because
    the speaker didn't end with terminal punctuation."""
    tail = " ".join(_last_words(text, 12)).lower()
    return any(p in tail for p in _FAREWELL_PHRASES)


def _starts_with_greeting(text: str) -> bool:
    """True if the FIRST few words of `text` look like a fresh-call
    greeting — strong evidence the recording is a new conversation,
    not a continuation."""
    head = " ".join(_first_words(text, 10)).lower()
    return any(p in head for p in _GREETING_PHRASES)


def _read_transcript(mp3_name: str) -> str | None:
    """Load the .md transcript body for a recording, stripped of
    YAML frontmatter. Returns None if no transcript exists."""
    stem = mp3_name.removesuffix(".mp3")
    md = RAW_TRANSCRIPTS_DIR / f"{stem}.md"
    if not md.exists():
        return None
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:]
    return text.strip()


def _last_words(text: str, n: int = 30) -> list[str]:
    return text.split()[-n:]


def _first_words(text: str, n: int = 30) -> list[str]:
    return text.split()[:n]


def heuristic_continuity(end_text: str | None, start_text: str | None) -> tuple[bool, list[str]]:
    """Quick local heuristic for "is piece N's end the same conversation
    as piece N+1's start?". Returns (probably_same, reasons).

    Order of evaluation:
      1. If the next piece OPENS with a greeting → new call. Hard veto.
      2. If the previous piece ENDS with a farewell → call done. Hard veto.
      3. Mid-sentence end + content-term overlap → likely continuing.

    The vetos are why this isn't a pure overlap-counting heuristic.
    A user routinely says "see you Monday cheers" mid-sign-off without
    final punctuation, which the punctuation-only check used to read
    as "ends mid-sentence" → false-positive same-conversation. The
    farewell veto kills that. Greeting veto handles the mirror case
    where the second piece opens with "Hey mate" / "Good morning".
    """
    if not end_text or not start_text:
        return False, ["transcript missing"]

    reasons: list[str] = []
    if _starts_with_greeting(start_text):
        reasons.append("next piece opens with a greeting")
        return False, reasons
    if _ends_with_farewell(end_text):
        reasons.append("previous piece ends with a farewell")
        return False, reasons

    last_char = end_text.rstrip()[-1] if end_text.strip() else ""
    ends_open = last_char not in ".!?"
    end_set = {w.lower().strip(".,?!:;\"'") for w in _last_words(end_text)} - _STOP
    start_set = {w.lower().strip(".,?!:;\"'") for w in _first_words(start_text)} - _STOP
    overlap = {w for w in (end_set & start_set) if len(w) > 3}
    if ends_open:
        reasons.append("ends mid-sentence")
    if len(overlap) >= 3:
        reasons.append(f"shared content terms: {', '.join(sorted(overlap)[:5])}")
    elif len(overlap) >= 1:
        reasons.append(f"weak overlap: {', '.join(sorted(overlap))}")
    same = ends_open or len(overlap) >= 3
    return same, reasons


def llm_continuity(end_text: str | None, start_text: str | None) -> tuple[bool, list[str]]:
    """Future plug-in point: rate continuity using a local Claude Code
    terminal embedded in the app. Currently unwired — the desktop app
    sets `use_llm=False` so this never runs. When the in-app Claude
    Code terminal lands, replace the body of this function with a call
    that asks "are these the same conversation?" and parses yes/no.

    Until then, raises NotImplementedError so any caller passing
    use_llm=True fails loudly rather than silently degrading.
    """
    raise NotImplementedError("llm_continuity is not yet wired — pass use_llm=False")


def chain_score(durations_min: float, max_gap_s: float, n_pieces: int,
                continuity_same: bool) -> int:
    """Map gap + total + boundary continuity to a confidence score.
    Higher = more likely a single split conversation.

    Tuned against today's hand-classified set (9 real merges, 13
    back-to-back meetings, 25 chains total) AND on the realisation
    that short meetings can absolutely produce many tight pieces too —
    e.g. a 4-min call cut into four 1-min pieces by aggressive
    rebuilds. Scoring is therefore deliberately duration-agnostic
    until the total exceeds 3 hours (where multi-meeting becomes
    overwhelmingly more likely).

    Score >=8 = "high confidence" → surface by default.
    Score 5-7 = "candidate"        → behind a "show all" toggle.
    Score <5  = "weak"             → drop entirely.
    """
    s = 0
    # Primary signal: max gap between pieces. Mic-trigger
    # release/re-acquire and (historically) app-rebuild round-trips
    # both produce gaps in the 5–30s window. Anything tighter is
    # almost certainly one continuous conversation.
    if max_gap_s <= 30:
        s += 5
    elif max_gap_s <= 60:
        s += 2

    # Secondary signal: did the conversation actually continue across
    # the boundary? Mid-sentence end + shared content terms are strong
    # evidence; a clean farewell + greeting is strong evidence against.
    if continuity_same:
        s += 4

    # Many pieces with at least one loose gap is the back-to-back-
    # meetings signature ("you had 5 meetings in a row, app rebuilt
    # between some of them"). Penalty scales with piece count, but
    # only fires when there's also a non-tight gap.
    if n_pieces >= 4 and max_gap_s > 30:
        s -= (n_pieces - 3)

    # Hard cap: > 3 hours of cumulative audio is more often "an
    # afternoon of meetings" than "one mega-meeting interrupted
    # several times".
    if durations_min > 180:
        s -= 2

    return s


# ── Sidecar I/O ────────────────────────────────────────────────────────────


def _load_merge_state() -> dict:
    if not MERGE_STATE_PATH.exists():
        return {"dismissed_pairs": [], "scanned_files": []}
    try:
        return json.loads(MERGE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"dismissed_pairs": [], "scanned_files": []}


def _save_merge_state(state: dict) -> None:
    MERGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MERGE_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                                encoding="utf-8")


def _pair_key(name_a: str, name_b: str) -> str:
    """Stable order-independent key for a pair of device-side filenames.
    A user dismissing (Rec18, Rec19) shouldn't have to dismiss
    (Rec19, Rec18) too."""
    a, b = sorted([name_a, name_b])
    return f"{a}|||{b}"


def _chain_pairs(piece_names: list[str]) -> list[str]:
    """All adjacent pair-keys in a chain (n-1 of them)."""
    return [_pair_key(piece_names[i], piece_names[i + 1])
            for i in range(len(piece_names) - 1)]


def is_chain_dismissed(piece_names: list[str], state: dict | None = None) -> bool:
    """A chain is dismissed if any of its adjacent pairs was dismissed —
    a single "no, these aren't the same" signal kills the whole chain
    suggestion."""
    if state is None:
        state = _load_merge_state()
    dismissed = set(state.get("dismissed_pairs", []))
    return any(k in dismissed for k in _chain_pairs(piece_names))


def dismiss_chain(piece_names: list[str]) -> None:
    """Record a "don't suggest these together again" decision. Sticky
    across rescans; only an explicit unmark-dismissed call clears it."""
    state = _load_merge_state()
    dismissed = set(state.get("dismissed_pairs", []))
    for k in _chain_pairs(piece_names):
        dismissed.add(k)
    state["dismissed_pairs"] = sorted(dismissed)
    _save_merge_state(state)


# ── Detection ──────────────────────────────────────────────────────────────


@dataclass
class Piece:
    name: str               # device-side filename (e.g. "2026Apr22-203106-Rec52.hda")
    mp3_name: str           # output filename (e.g. "2026Apr22-203106-Rec52.mp3")
    mp3_path: str
    start: datetime
    duration_s: float
    end: datetime
    pid: int | None


@dataclass
class CandidateChain:
    pieces: list[Piece]
    score: int
    total_min: float
    max_gap_s: float
    continuity: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "high_confidence": self.score >= 8,
            "total_min": round(self.total_min, 1),
            "max_gap_s": round(self.max_gap_s, 1),
            "continuity_signals": self.continuity,
            "pair_key": _pair_key(self.pieces[0].name, self.pieces[-1].name),
            "pieces": [{
                "device_name": p.name,
                "mp3_name": p.mp3_name,
                "mp3_path": p.mp3_path,
                "start": p.start.isoformat(),
                "duration_s": round(p.duration_s, 1),
                "pid": p.pid,
            } for p in self.pieces],
        }


def _read_pieces(downloads: dict) -> list[Piece]:
    """Build Piece records for every locally-existing recording we have
    metadata for. Falls back to size/8000 if mutagen is unavailable."""
    pieces: list[Piece] = []
    try:
        from mutagen.mp3 import MP3
        have_mutagen = True
    except ImportError:
        have_mutagen = False

    for hda_name, rec in downloads.items():
        out = rec.get("output_path", "")
        if not out:
            continue
        path = Path(out)
        if not path.exists():
            continue
        ts = parse_recording_timestamp(path.name)
        if ts is None:
            continue
        if have_mutagen:
            try:
                duration = float(MP3(str(path)).info.length)
            except Exception:
                duration = max(path.stat().st_size / 8000.0, 0.0)
        else:
            duration = max(path.stat().st_size / 8000.0, 0.0)
        pieces.append(Piece(
            name=hda_name,
            mp3_name=path.name,
            mp3_path=str(path),
            start=ts,
            duration_s=duration,
            end=ts + timedelta(seconds=duration),
            pid=rec.get("product_id"),
        ))
    return pieces


def find_candidates(downloads: dict, *, max_gap_s: float = 90.0,
                    use_llm: bool = False) -> list[CandidateChain]:
    """Walk every paired-device's recordings, group adjacent ones with
    small inter-piece gaps into chains, score each chain, and return
    candidates whose score is at least the "weak" cutoff (5).

    Dismissed chains are filtered out — they were the user's explicit
    "no, these aren't related" verdict and shouldn't keep nagging.

    `use_llm=True` activates the llm_continuity hook (currently raises
    NotImplementedError; left as the future plug-in point for the
    in-app Claude Code terminal).
    """
    pieces = _read_pieces(downloads)
    by_pid: dict[int | None, list[Piece]] = {}
    for p in pieces:
        by_pid.setdefault(p.pid, []).append(p)
    for pid in by_pid:
        by_pid[pid].sort(key=lambda p: p.start)

    state = _load_merge_state()

    chains: list[CandidateChain] = []
    for pid, recs in by_pid.items():
        if len(recs) < 2:
            continue
        cur = [recs[0]]
        for prev, curr in zip(recs, recs[1:]):
            gap = (curr.start - prev.end).total_seconds()
            if 0 <= gap <= max_gap_s:
                cur.append(curr)
            else:
                if len(cur) >= 2:
                    _emit_chain(cur, chains, state, use_llm)
                cur = [curr]
        if len(cur) >= 2:
            _emit_chain(cur, chains, state, use_llm)

    # Stable sort: highest score first, then earliest start.
    chains.sort(key=lambda c: (-c.score, c.pieces[0].start))
    return chains


def _emit_chain(pieces: list[Piece], out: list[CandidateChain], state: dict, use_llm: bool) -> None:
    names = [p.name for p in pieces]
    if is_chain_dismissed(names, state):
        return

    # Hard requirement: every piece must have a transcript. Without
    # that, the continuity check has no signal and we can't reliably
    # distinguish "a single meeting that got split" from "two
    # back-to-back meetings". The user explicitly asked for this gate.
    transcripts = [_read_transcript(p.mp3_name) for p in pieces]
    if any(t is None for t in transcripts):
        return

    gaps = [(pieces[i].start - pieces[i - 1].end).total_seconds() for i in range(1, len(pieces))]
    total_min = sum(p.duration_s for p in pieces) / 60
    max_gap = max(gaps)

    # Continuity check on the first→second boundary (keeps it cheap;
    # a 3-piece chain that bridges cleanly across 1→2 is almost
    # certainly bridging 2→3 too).
    if use_llm:
        same, reasons = llm_continuity(transcripts[0], transcripts[1])
    else:
        same, reasons = heuristic_continuity(transcripts[0], transcripts[1])

    score = chain_score(total_min, max_gap, len(pieces), continuity_same=same)
    if score < 5:
        return  # below the "weak" cutoff — drop entirely

    out.append(CandidateChain(
        pieces=pieces,
        score=score,
        total_min=total_min,
        max_gap_s=max_gap,
        continuity=reasons,
    ))


def candidates_to_payload(chains: list[CandidateChain]) -> dict:
    """Public wire format for the desktop app subprocess call."""
    return {
        "chains": [c.to_dict() for c in chains],
        "high_confidence_count": sum(1 for c in chains if c.score >= 8),
        "total_count": len(chains),
    }
