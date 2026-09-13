"""The observable-evidence bar and the protected-attribute bar, pinned.

WHY THIS FILE EXISTS AT ALL
-----------------------------
`services/hiring/observable.py` is the ONE detector two live callers share:
`swot_quality` holds a hiring manager's SWOT requirement to it, and `scorecard`
holds the model that names a competency to the same bar. A shared detector with
no test of its own is a rule that quietly loosens the next time somebody adds a
word to a list, and both callers would keep passing their own tests while
grading against a bar that had moved.

BOTH DIRECTIONS ARE ASSERTED, AND THE SECOND ONE MATTERS MORE
---------------------------------------------------------------
A detector that refused everything would pass every rejection case perfectly
and make the product unusable: a hiring manager would learn that no requirement
is ever good enough rather than what a usable one looks like. So every rejected
shape has an accepted twin here.

The same is true of `prohibited_in` in the other direction. Its first version
matched substrings and refused "Must hold a valid CA licence", because "hold"
contains "old". A false positive tells a client their lawful professional
requirement is discriminatory, which costs their trust in every refusal that
follows, including the correct ones.
"""
from __future__ import annotations

import pytest

from app.services.hiring import observable

# ── The shape that is refused ────────────────────────────────────────────────
#
# A description of a PERSON rather than an account of an EVENT. Not a list of
# bad words: several of these are perfectly good English in other sentences,
# and the accepted list below contains some of the same words.
REJECTED = (
    "Ownership mindset",
    "Team player",
    "Strong communicator",
    "Detail-oriented",
    "Culture fit",
    "A self-starter with a proactive attitude and real hunger",
    "very proactive",
    "great communicator",
)

# ── The shape that is accepted ───────────────────────────────────────────────
#
# "Has [done X] and can [describe or demonstrate Y]". Note the second entry:
# it carries the trait word "ownership" and is accepted anyway, which is the
# asymmetry the detector is built around.
ACCEPTED = (
    "Has taken a project from an unclear brief to a shipped outcome and can "
    "describe the decisions they made when nobody told them what to do",
    "She took ownership of the migration and shipped it in six weeks",
    "Has moved a blocking function to a decision without holding authority "
    "over it and can describe how they secured the commitment",
    "Has taken an unpredictable team to a predictable cadence and can describe "
    "the mechanism they introduced",
)


@pytest.mark.parametrize("phrase", REJECTED, ids=lambda s: s[:30])
def test_a_description_of_a_person_is_refused(phrase: str) -> None:
    assert not observable.is_observable(phrase)


@pytest.mark.parametrize("phrase", ACCEPTED, ids=lambda s: s[:30])
def test_an_account_of_an_event_is_accepted(phrase: str) -> None:
    assert observable.is_observable(phrase)


def test_an_event_verb_wins_over_a_trait_word() -> None:
    """The asymmetry, stated on its own rather than only inside the table.

    Without this, somebody tightening the trait list to catch one more
    rejection could break every real answer that happens to mention ownership,
    and the parametrised cases above would not say which rule had moved.
    """
    assert not observable.is_observable("They have a real ownership mindset")
    assert observable.is_observable(
        "They took ownership of the outage and rewrote the alerting that week"
    )


def test_an_answer_too_short_to_be_an_account_is_refused() -> None:
    """The other common form of "ownership mindset": three words, no event."""
    assert not observable.is_observable("")
    assert not observable.is_observable("   ")
    assert not observable.is_observable("shipped it")


def test_a_refusal_shows_the_difference_rather_than_only_refusing() -> None:
    """A refusal that teaches nothing gets the same adjective back, rephrased."""
    message = observable.rejection_message("we want someone with an ownership mindset")
    assert "ownership" in message, "the refusal does not name the phrase it caught"
    assert "has taken a project" in message.lower(), (
        "the refusal shows no accepted example, so there is nothing to convert "
        f"towards: {message!r}"
    )
    # A character class that MATCHES a dash is data, not prose: built from
    # chr(8212) so a repo-wide sweep cannot rewrite the check itself.
    assert chr(8212) not in message, "em dash in a user-facing string"


def test_a_refusal_with_no_named_trait_still_shows_an_example() -> None:
    """The other branch. "Driven" is caught by the word list; "does good work"
    is caught by length alone, and that refusal must still teach."""
    message = observable.rejection_message("does good work")
    assert "has taken a project" in message.lower()
    assert chr(8212) not in message


# ── The protected-attribute detector ─────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "No candidates over 45",
        "Nobody over 50",
        "Prefer male candidates",
        "Must be unmarried",
        "Looking for a young team fit",
        "Native speaker only",
    ],
    ids=lambda s: s[:30],
)
def test_a_disqualifier_resting_on_a_protected_attribute_is_caught(text: str) -> None:
    assert observable.prohibited_in(text), f"{text!r} was not caught"


@pytest.mark.parametrize(
    "text",
    [
        "Must hold a valid CA licence",
        "Must have shipped a payments integration end to end",
        "Holds an active security clearance",
        "Must be willing to travel to the Bengaluru office twice a month",
    ],
    ids=lambda s: s[:30],
)
def test_a_lawful_professional_requirement_is_not_caught(text: str) -> None:
    """The direction that costs trust. "hold" contains "old"."""
    assert observable.prohibited_in(text) == [], f"{text!r} was refused wrongly"


def test_an_age_bar_written_as_a_number_is_caught_without_the_word_age() -> None:
    """The blunt phrasing the word list alone missed by a distance.

    A client stating an age bar rarely writes "age"; they write the number.
    """
    caught = observable.prohibited_in("no candidates over 45")
    assert "an age limit" in caught
    assert "age" not in " ".join(caught).split("an age limit")[0]


def test_the_empty_string_is_neither_observable_nor_prohibited() -> None:
    assert observable.prohibited_in("") == []
    assert observable.prohibited_in("   ") == []
