"""The observable-evidence detector, and the protected-attribute detector.

WHAT THESE TWO ANSWER
-----------------------
`is_observable` answers "is this a statement of something somebody could have
watched happen", which is Runbook 18.5 rule 3's bar for a SWOT requirement and
the same bar Sutra holds a generated competency statement to. `prohibited_in`
answers "does this rest on a protected attribute", which is Runbook 12.3.

THERE IS ONE OF EACH, DELIBERATELY
------------------------------------
Two copies of a fairness detector drift invisibly: the copy nobody edits keeps
passing its own tests while the rule it encodes has moved. Sutra holds the
model that writes a skill's hidden evidence line to this bar
(`hiring/sutra.build_context`), and Drishti holds a functional head's profile to
it (`hiring/drishti`), so no second copy of the rule exists to drift. Both
import from here, and `tests/test_observable_detector.py` is the one place the
behaviour is pinned.

THE ASYMMETRY IN `is_observable` IS THE POINT
----------------------------------------------
An event verb wins even when a trait word is present, because "she took
ownership of the migration and shipped it in six weeks" is a real account with
a trait word in it. The shape being refused is a statement with no event verb
at all. `prohibited_in` matches on WORD BOUNDARIES for the complementary
reason: a false positive tells somebody their lawful professional requirement
is discriminatory, which destroys their trust in every refusal that follows.
"""
from __future__ import annotations

import re

__all__ = [
    "is_observable",
    "rejection_message",
    "PROHIBITED_DISQUALIFIERS",
    "prohibited_in",
]


#: Words that describe a PERSON rather than an EVENT. Not a blocklist of bad
#: words -- several are perfectly good English -- but a detector for the shape
#: of answer this detector exists to refuse.
_TRAIT_WORDS: frozenset[str] = frozenset(
    {
        "mindset", "attitude", "mentality", "personality", "character",
        "ownership", "proactive", "proactivity", "driven", "hungry", "hunger",
        "passionate", "passion", "motivated", "self-starter", "selfstarter",
        "go-getter", "team player", "culture fit", "culturally", "dynamic",
        "energetic", "enthusiastic", "committed", "dedication", "dedicated",
        "hardworking", "hard-working", "smart", "intelligent", "bright",
        "detail-oriented", "detail oriented", "meticulous", "reliable",
        "dependable", "flexible", "adaptable", "resilient", "resilience",
        "leadership qualities", "strong communicator", "excellent communicator",
        "good communicator", "problem solver", "problem-solver", "rockstar",
        "ninja", "guru", "10x",
    }
)

#: Verbs that describe something that HAPPENED. An answer containing one is
#: describing an event, which is what the detector is looking for.
_EVENT_VERBS: frozenset[str] = frozenset(
    {
        "shipped", "delivered", "built", "launched", "took", "led", "ran",
        "rewrote", "migrated", "closed", "negotiated", "hired", "fired",
        "reduced", "increased", "moved", "changed", "fixed", "resolved",
        "escalated", "presented", "wrote", "designed", "rebuilt", "recovered",
        "turned", "grew", "cut", "automated", "replaced", "introduced",
        "removed", "convinced", "persuaded", "handed", "finished", "completed",
        "solved", "diagnosed", "found", "caught", "missed", "left", "joined",
        "stopped", "started", "raised", "landed", "saved", "onboarded",
    }
)

_MIN_EVIDENCE_WORDS = 8


def is_observable(answer: str) -> bool:
    """True when an answer describes something somebody could have watched.

    The test is deliberately ASYMMETRIC: an answer containing an event verb
    passes even if it also contains a trait word, because "she took ownership of
    the migration and shipped it in six weeks" is a real answer with a trait
    word in it. An answer with no event verb at all and a trait word in it is
    the shape being refused.

    It also refuses answers that are simply too short to be an account of
    anything, which is the other common form of "ownership mindset" -- three
    words, no verb, nothing to probe.
    """
    text = (answer or "").strip().lower()
    if not text:
        return False
    words = re.findall(r"[a-z][a-z'\-]*", text)
    if len(words) < _MIN_EVIDENCE_WORDS:
        return False
    if any(word in _EVENT_VERBS for word in words):
        return True
    # No event verb. A trait word now decides it, and its absence is not enough
    # on its own -- an answer with neither is prose about nothing.
    return not any(trait in text for trait in _TRAIT_WORDS)


def rejection_message(answer: str) -> str:
    """What an agent says when it refuses a statement and asks again.

    Names the specific phrase it caught and gives the accepted example
    the Runbook quotes verbatim. A rejection that does not show the difference
    teaches nothing, and the client simply rephrases the same adjective.
    """
    text = (answer or "").strip().lower()
    caught = next((trait for trait in sorted(_TRAIT_WORDS) if trait in text), None)
    if caught:
        return (
            f"That is a description of a person rather than something I could "
            f"have watched happen -- \"{caught}\" is the part I mean. Could you "
            f"give me the specific thing they did? For instance, instead of "
            f"\"ownership mindset\": \"has taken a project from an unclear brief "
            f"to a shipped outcome\"."
        )
    return (
        "I need a bit more than that -- something specific enough that I could "
        "picture it happening. For instance, instead of \"ownership mindset\": "
        "\"has taken a project from an unclear brief to a shipped outcome\"."
    )


# ── Disqualifiers ────────────────────────────────────────────────────────────

#: Attributes a disqualifier may never rest on. Refused at capture, not at
#: compilation, so the client is told at the moment they typed it.
#:
#: This is the same list `agents/gates.FORBIDDEN_INFERENCE_FIELDS` holds and it
#: is checked here too rather than only there, for the reason enforcement is
#: always checked at the boundary where the information exists: by the time a
#: gate sees a compiled artifact, the sentence the client typed is gone.
PROHIBITED_DISQUALIFIERS: tuple[str, ...] = (
    "age", "aged", "date of birth", "born in", "years old", "young", "old",
    "gender", "male", "female", "sex", "man", "men", "woman", "women",
    "religion", "religious", "caste", "creed",
    "marital", "married", "single", "unmarried", "pregnant", "pregnancy",
    "children", "kids", "childcare", "maternity", "paternity",
    "nationality", "race", "racial", "ethnic", "ethnicity", "colour", "color",
    "disability", "disabled", "handicap", "handicapped",
    "sexual orientation", "gay", "lesbian", "transgender",
    "mother tongue", "native speaker",
)

#: Age bars written as a NUMBER rather than as a word.
#:
#: These exist because the word list alone missed the most common phrasing by a
#: distance: "nobody over 50" and "no candidates over 45" contain no term from
#: the list above, and both are exactly the unlawful requirement it is there to
#: catch. A client stating an age bar rarely uses the word "age" -- they state
#: the number, which is what makes a purely lexical list a detector that catches
#: the careful phrasings and misses the blunt ones.
_AGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(over|under|above|below|more than|less than|at least|max(?:imum)?|min(?:imum)?)\s+\d{2}\b"),
    re.compile(r"\b\d{2}\s*(\+|plus|and above|and over|or younger|or older|yrs? old|years? old)\b"),
    re.compile(r"\b(no|not|nobody|nobody's|none)\b[^.]{0,30}\b\d{2}\b"),
)


def prohibited_in(text: str) -> list[str]:
    """Every protected-attribute term a disqualifier rests on.

    MATCHED ON WORD BOUNDARIES, not as substrings, and the reason is a defect
    this had on its first version: "Must hold a valid CA licence" was refused
    because "hold" contains "old". A false positive here is not harmless -- it
    tells a client their perfectly lawful professional requirement is
    discriminatory, which destroys their trust in every refusal that follows,
    including the ones that are right.

    Multi-word terms are matched as phrases, single words with `\b` anchors.
    """
    lowered = " ".join((text or "").lower().split())
    if not lowered:
        return []
    found: list[str] = []
    for term in PROHIBITED_DISQUALIFIERS:
        if " " in term:
            if term in lowered:
                found.append(term)
        elif re.search(rf"\b{re.escape(term)}\b", lowered):
            found.append(term)
    for pattern in _AGE_PATTERNS:
        if pattern.search(lowered):
            found.append("an age limit")
            break
    return found
