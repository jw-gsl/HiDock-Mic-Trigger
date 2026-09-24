"""Decide whether a ranked voice-library result is confident enough to name.

Both naming paths — the promoted candidate library in `diarize_sortformer.
_naming_backend` and the legacy `voice_library_lite.identify_speaker` — used the
same shape of rule: accept the top match when it clears a score threshold *and*
leads the runner-up by a flat margin. That rule threw away correct answers on
this user's library, and did it silently.

Measured on Rec01 (2026-07-31), against the promoted ReDimNet2 library:

    speaker 0:  James Whiting 0.847,  Nick Clark 0.765   -> margin 0.082
    speaker 1:  Adam 0.822,           Adam Gardner 0.807 -> margin 0.014

Both cleared the 0.5 threshold comfortably. Both were rejected by the flat
0.23 margin, so the transcript came back as "Speaker 1" / "Speaker 2" and read
as though the voice library had never run at all.

Two separate faults, fixed here:

1. **Alias entries competed with themselves.** "Adam" and "Adam Gardner" are one
   human enrolled twice; they scored within 0.014 of each other and so
   suppressed the match. Nine such pairs exist in the library today
   (Jackson/Jackson Ryan, John/John Henderson, Andy/Andy Wheeler,
   Andy/Andy Wilmott, Adam/Adam Prior, Adam/Adam Gardner, Kieran/Kieran
   Redpath, Ian/Ian Reay, Ian/Ian Glenn). A runner-up who is the *same person*
   as the winner is not evidence of ambiguity, so it must not consume the
   margin.

   The collapse is deliberately narrow. "Andy" against both "Andy Wheeler" and
   "Andy Wilmott" is two different people, and naming either one confidently
   would be a fabricated identity — so a first name with more than one distinct
   surname still in contention is reported ambiguous rather than resolved.

2. **A flat margin is the wrong test at high confidence.** The margin exists to
   stop a coin-flip between two plausible people. The closer the top score sits
   to 1.0, the less a given gap means, so the requirement relaxes with
   confidence instead of staying fixed.

Rejections are no longer silent: `MatchDecision.near_miss` carries the candidate
that was refused and why, so the UI can offer it as a one-click suggestion
rather than leaving the user to wonder whether matching ran.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Never relax the margin below this fraction of the configured value, however
# confident the top score looks. A gap has to mean *something*.
_MIN_MARGIN_FRACTION = 0.25

# How much better someone outside the meeting must score than anyone inside it
# before the calendar's attendee list is disregarded entirely. People do join
# meetings they were not invited to, and a voice that matches this much better
# is stronger evidence than the invite list.
_OUTSIDER_TRUST_MARGIN = 0.10


@dataclass
class MatchDecision:
    """What to do with a ranked library result.

    `name` is set only when the match should be applied automatically.
    `near_miss` is set when a candidate was plausible but refused; it is what the
    UI offers the user. Both are None when nothing came close.
    """

    name: str | None = None
    confidence: float = 0.0
    near_miss: dict | None = None
    reason: str = ""
    #: Every library entry judged to be the same person as the winner, best
    #: first. Useful for explaining a collapse and for de-duplication tooling.
    alias_group: list[str] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.name is not None


def _tokens(name: str) -> list[str]:
    return [t for t in str(name).strip().lower().split() if t]


def alias_compatible(a: str, b: str) -> bool:
    """True when one name is a bare first name and the other extends it.

    "Adam" ~ "Adam Gardner". "Adam Gardner" !~ "Adam Prior" — two full names that
    disagree on the surname are two people, never an alias pair.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or ta == tb:
        return ta == tb and bool(ta)
    if ta[0] != tb[0]:
        return False
    # Exactly one side must be the bare first name.
    return (len(ta) == 1) != (len(tb) == 1)


def required_margin(best: float, threshold: float, min_margin: float) -> float:
    """Margin the winner must lead a *different person* by.

    Full `min_margin` at the threshold, easing towards `_MIN_MARGIN_FRACTION` of
    it as the score approaches a perfect 1.0. A 0.847 top score against a 0.5
    threshold and a 0.23 configured margin needs 0.070, which is what lets a
    genuine 0.847-vs-0.765 match through.
    """
    span = 1.0 - threshold
    if span <= 0:
        return min_margin
    closeness = max(0.0, min(1.0, (best - threshold) / span))
    return min_margin * max(_MIN_MARGIN_FRACTION, 1.0 - closeness)


def _canonical(entries: list[dict]) -> str:
    """The name to report for one person enrolled under several spellings.

    Prefer a full name over a bare first name — "Adam Gardner" says more than
    "Adam" — and break ties on score, never on string length. Length would be an
    accident of spelling: it would pick between "Adam Gardner" and "Adam Prior"
    on letter count, which is exactly how a confident wrong name gets made.
    """
    return max(
        entries,
        key=lambda e: (len(_tokens(str(e["name"]))) > 1, float(e["score"])),
    )["name"]


def _in_expected(name: str, expected: set[str]) -> bool:
    """True when `name` is one of the expected people, allowing for aliases.

    The calendar gives display names ("Harry Veening") while the library may
    hold a bare first name ("Harry") or vice versa, so the same
    first-name-extends-full-name rule used for alias collapse applies here.
    """
    n = str(name)
    return any(e == n or alias_compatible(e, n) for e in expected)


def decide(
    ranked: list[dict],
    threshold: float,
    min_margin: float,
    expected_names: set[str] | list[str] | None = None,
) -> MatchDecision:
    """Resolve a ranked `[{"name", "score"}, ...]` list into an action.

    `ranked` must be sorted best-first, as both `_rank_library` and
    `library_scores` already return it.

    `expected_names` — when known, the people the calendar says were in this
    meeting. It is a *tie-breaker of last resort*, never a filter. See
    `_decide_with_expected`.
    """
    decision = _decide(ranked, threshold, min_margin)
    if decision.matched or not expected_names:
        return decision
    return _decide_with_expected(
        ranked, threshold, min_margin, set(map(str, expected_names)), decision
    )


def _decide_with_expected(
    ranked: list[dict],
    threshold: float,
    min_margin: float,
    expected: set[str],
    refusal: MatchDecision,
) -> MatchDecision:
    """Retry a refused match among only the people known to be in the meeting.

    Rec26 (2026-08-06) is the case this exists for. A 37-minute meeting with 7
    invitees came back with 4 speakers and 1 name, and correcting it took 38
    manual edits. Three of the eight refusals read:

        Martin 0.513 leads Jurriaan Piek by only 0.125 (needed 0.224)
        Martin 0.543 leads Joe Kraft by only 0.127 (needed 0.210)
        Patrick Stephens 0.513 leads Sean Denton by only 0.067 (needed 0.224)

    Not one of those five people was in the meeting. The library was searched
    against everyone ever enrolled, so near-ties between strangers suppressed
    matches for the people actually in the room. A runner-up who could not have
    been speaking is no more evidence of ambiguity than the alias case above.

    Two guards keep this honest, because the attendee list is *evidence, not
    truth* — Rec26 also had Christian Cockroft in the room and not on the
    invite:

    1. It only runs when the unrestricted decision already refused. A confident
       global match is never overridden, so a genuine non-invited attendee is
       still named on the strength of their own voice.
    2. It is abandoned when someone outside the meeting matches clearly better
       than anyone inside it (`_OUTSIDER_TRUST_MARGIN`). That is the signature
       of exactly such an uninvited attendee, and the voice is better evidence
       than the invite list.

    The absolute threshold is never relaxed — a narrower field cannot manufacture
    confidence that was not there.
    """
    narrowed = [r for r in ranked if _in_expected(str(r["name"]), expected)]
    if not narrowed:
        return refusal

    best_overall = float(ranked[0]["score"])
    best_expected = float(narrowed[0]["score"])
    if best_overall - best_expected >= _OUTSIDER_TRUST_MARGIN:
        return refusal

    retry = _decide(narrowed, threshold, min_margin)
    if not retry.matched:
        return refusal
    return MatchDecision(
        name=retry.name,
        confidence=retry.confidence,
        reason=f"{retry.reason} (among this meeting's attendees)",
        alias_group=retry.alias_group,
    )


def _decide(
    ranked: list[dict],
    threshold: float,
    min_margin: float,
) -> MatchDecision:
    if not ranked:
        return MatchDecision(reason="no library candidates")

    best = ranked[0]
    best_name, best_score = str(best["name"]), float(best["score"])
    needed = required_margin(best_score, threshold, min_margin)

    # Sharing a first name is not enough to be the same person: "Adam" scored
    # 0.822 while "Adam Prior" scored 0.528, and treating those as one human
    # would let a stranger's enrolment stand in for the match. Require the
    # scores to agree too — alias-compatible *and* within the margin means one
    # person enrolled twice; alias-compatible but far apart means two people who
    # happen to share a first name, and the other one is a genuine rival.
    group = [
        r
        for r in ranked
        if alias_compatible(str(r["name"]), best_name)
        and best_score - float(r["score"]) < needed
    ]
    group_names = [str(r["name"]) for r in group]
    rivals = [r for r in ranked if str(r["name"]) not in set(group_names)]

    if best_score < threshold:
        return MatchDecision(
            near_miss={
                "name": _canonical(group),
                "score": best_score,
                "runner_up": str(rivals[0]["name"]) if rivals else None,
                "runner_up_score": float(rivals[0]["score"]) if rivals else None,
                "why": "below threshold",
            },
            reason=f"best {best_score:.3f} < threshold {threshold:.3f}",
            alias_group=group_names,
        )

    # A bare first name still tied with two different surnames is genuinely
    # ambiguous: "Andy" could be Andy Wheeler or Andy Wilmott. Both survived the
    # score-agreement test above, so the embeddings cannot separate them and
    # picking one would invent a certainty that is not there.
    surnamed = [r for r in group if len(_tokens(str(r["name"]))) > 1]
    if len(surnamed) > 1:
        top, second = float(surnamed[0]["score"]), float(surnamed[1]["score"])
        return MatchDecision(
            near_miss={
                "name": str(surnamed[0]["name"]),
                "score": top,
                "runner_up": str(surnamed[1]["name"]),
                "runner_up_score": second,
                "why": "two people share this first name",
            },
            reason=(
                f"ambiguous first name: {surnamed[0]['name']} {top:.3f} vs "
                f"{surnamed[1]['name']} {second:.3f}"
            ),
            alias_group=group_names,
        )

    winner = _canonical(group)
    rival_score = float(rivals[0]["score"]) if rivals else -1.0
    gap = best_score - rival_score

    if gap >= needed:
        return MatchDecision(
            name=winner,
            confidence=best_score,
            reason=(
                f"{winner} {best_score:.3f} leads "
                f"{rivals[0]['name'] if rivals else 'nothing'} by {gap:.3f} "
                f"(needed {needed:.3f})"
            ),
            alias_group=group_names,
        )

    return MatchDecision(
        near_miss={
            "name": winner,
            "score": best_score,
            "runner_up": str(rivals[0]["name"]) if rivals else None,
            "runner_up_score": rival_score if rivals else None,
            "why": "too close to the runner-up",
        },
        reason=(
            f"{winner} {best_score:.3f} leads "
            f"{rivals[0]['name'] if rivals else 'nothing'} by only {gap:.3f} "
            f"(needed {needed:.3f})"
        ),
        alias_group=group_names,
    )
