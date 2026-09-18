"""The candidate's own account deletion: the warning, the phrase, the copy.

`VIVEKIUM_SPRINT_FEATURES.md` feature 7, and India's Digital Personal Data
Protection Act, 2023, which gives a data principal the right to have their
personal data erased.

THE MACHINERY FOR THIS ALREADY EXISTED AND HAD NO DOOR. `services/erasure`
reaches rows, vectors and caches, and `pickready.cascade_erasure` has been a
registered task since the AI runtime upgrade. Nothing anywhere called it: there
was no route, on any portal, that a person could use to have themselves erased.
This module is the missing half, and it deliberately contains no deletion logic
at all, because a second implementation of erasure is exactly the thing that
would drift from the one that knows about vectors.

What lives here is the part a screen must not be allowed to author for itself:

  * THE WARNING IS SERVED BY THE SERVER, VERBATIM. Same rule the BGV
    submission warning follows (`models/employment.SUBMISSION_WARNING`): the
    sentence describing an irreversible rule and the rule itself must not be
    able to drift, and they drift the moment one of them lives in a component.
    A client that rendered its own list would keep promising whatever it
    promised on the day it was written.
  * THE CONFIRMATION PHRASE IS CHECKED ON THE SERVER. A typed confirmation
    that only the browser checks is a speed bump, not a guard: the route is
    reachable by anything holding the candidate's session. This is the same
    shape `DELETE /admin/tenants/{id}` already uses, where the operator retypes
    the company name.
"""
from __future__ import annotations

#: What the candidate must type, exactly, before the route will act. ASCII and
#: uppercase so there is nothing to get wrong about locale, accent or case
#: folding: the comparison is `==` against this string and nothing else.
CONFIRMATION_PHRASE = "DELETE"

#: The smart warning screen, in the order the brief states it. Ordering is
#: load bearing: the irreversible consequence people most underestimate is a
#: completed background verification, which costs a third party's time to
#: re-obtain and cannot be recreated by the candidate alone, so it appears
#: before the summary line rather than buried in it.
#:
#: No em dash (rule 7), and no number: "employer clients registered on the
#: platform" is the brief's own required wording, and the terms "direct
#: employer" and "manpower agency" are forbidden anywhere in system copy.
DELETION_WARNINGS: tuple[str, ...] = (
    "Your complete profile, including all background verification records and "
    "assessment data, will be permanently deleted.",
    "You will be immediately removed from all active job matching on the "
    "platform.",
    "Employer clients registered on the platform who were considering your "
    "profile will no longer see it.",
    "Any ongoing assessment or shortlisting process will be cancelled "
    "immediately.",
    "If you completed a background verification, that verified record will be "
    "permanently lost and must be obtained again from the beginning.",
    "If you return to the platform in future, you will need to complete the "
    "full profile, questionnaire, assessment and verification process again "
    "from the beginning.",
    "This action cannot be reversed under any circumstances.",
)

#: The heading and the instruction. Held here for the same reason as the list.
DELETION_HEADING = "Delete my profile"
DELETION_INSTRUCTION = (
    f"Type {CONFIRMATION_PHRASE} to confirm. This cannot be undone."
)

#: What the route refuses with when the phrase does not match. Names the phrase
#: so the message is actionable, and says nothing about what would have been
#: deleted, because a refusal is not the place to restate the warning.
WRONG_PHRASE_MESSAGE = (
    f"Type {CONFIRMATION_PHRASE} exactly to confirm that you want your profile "
    "permanently deleted."
)

#: The delivery template key for the confirmation letter. The letter states
#: THAT the deletion happened and carries nothing about the person it happened
#: to beyond their own address, because by the time it is sent there is no
#: record left to describe and describing one would mean holding a copy.
CONFIRMATION_TEMPLATE = "account_deleted"


def deletion_notice() -> dict[str, object]:
    """The whole warning screen, as data, for the one route that serves it."""
    return {
        "heading": DELETION_HEADING,
        "warnings": list(DELETION_WARNINGS),
        "confirmation_phrase": CONFIRMATION_PHRASE,
        "instruction": DELETION_INSTRUCTION,
    }


def phrase_matches(typed: str | None) -> bool:
    """Whether what the candidate typed authorises the deletion.

    Surrounding whitespace is forgiven because a mobile keyboard adds a
    trailing space on its own; CASE IS NOT, because lowering the case would
    accept "delete" from an autocorrect that had no intent behind it.
    """
    return typed is not None and typed.strip() == CONFIRMATION_PHRASE
