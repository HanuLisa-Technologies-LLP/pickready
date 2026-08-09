"""The assessment link in a lifecycle email must be THE link (2026-08-09).

Reported symptom: the "Complete Assessment" email pointed at
`link.to.assessment`, which is not a host and resolves to
DNS_PROBE_FINISHED_NXDOMAIN. Every candidate who received one was told to
complete an assessment they had no way to open, and nothing in the product could
tell that had happened: the URL is built correctly from `settings.frontend_url`,
handed to the prompt, and then the MODEL wrote something else into the body.

So the defect is not in URL construction and no test of URL construction would
have caught it. What was missing was a check that the link SURVIVED the draft.
It is deterministic for the usual reason (`agent_loop`'s contract): the moment
a guard matters most is the moment the provider is behaving badly, and a judge
would add a second flaky dependency to the check on the first one.

The hard part here is the DISTINCTION, not the detection. A guard that rejects
a good email because it mentions "Node.js" fails invisibly, one wasted round of
latency at a time, so both directions are asserted below.
"""
from __future__ import annotations

import pytest

from app.models.email_log import (
    EMAIL_TYPE_ASSESSMENT_INVITATION,
    EMAIL_TYPE_ASSESSMENT_REMINDER,
    EMAIL_TYPE_REJECTED,
)
from app.services import lifecycle_email

LINK = "https://app.pickready.example/portal/assessments/9f1c2e40-0000-4000-8000-00000000abcd"
CTX = {
    "candidate_name": "Asha",
    "job_title": "Backend Engineer",
    "company_name": "Northwind",
    "assessment_link": LINK,
}


def _body(link: str = LINK) -> str:
    return (
        "Hi Asha,\n\nThe team would like you to complete a short assessment.\n\n"
        f"{link}\n\nYour answers save as you go.\n\n, The Northwind team"
    )


# ── The reported defect ──────────────────────────────────────────────────────

def test_an_invented_placeholder_link_is_rejected() -> None:
    reasons = lifecycle_email.link_defects(
        EMAIL_TYPE_ASSESSMENT_INVITATION, CTX, _body("link.to.assessment")
    )
    assert reasons
    # The rejection is fed back to the model verbatim, so it has to name both
    # the thing that is wrong and the thing to write instead.
    joined = " ".join(reasons)
    assert "link.to.assessment" in joined
    assert LINK in joined


@pytest.mark.parametrize(
    "invented",
    [
        "link.to.assessment",
        "www.pickready.example/assessment",
        "https://example.com/your-assessment",
        "your.assessment.link",
    ],
)
def test_every_shape_of_invented_link_is_caught(invented: str) -> None:
    assert lifecycle_email.link_defects(
        EMAIL_TYPE_ASSESSMENT_INVITATION, CTX, _body(invented)
    )


def test_a_body_that_drops_the_link_entirely_is_rejected() -> None:
    reasons = lifecycle_email.link_defects(
        EMAIL_TYPE_ASSESSMENT_INVITATION,
        CTX,
        "Hi Asha,\n\nPlease complete your assessment.\n\n, The Northwind team",
    )
    assert reasons and LINK in reasons[0]


# ── The direction that matters just as much: no false positives ──────────────

def test_the_real_link_passes() -> None:
    assert (
        lifecycle_email.link_defects(EMAIL_TYPE_ASSESSMENT_INVITATION, CTX, _body())
        == []
    )


def test_the_link_still_passes_with_sentence_punctuation_after_it() -> None:
    body = _body() .replace(LINK, f"{LINK}.")
    assert lifecycle_email.link_defects(EMAIL_TYPE_ASSESSMENT_INVITATION, CTX, body) == []


@pytest.mark.parametrize(
    "prose",
    [
        "You will be asked about Node.js and about how you work.",
        "The team read your application at readypick.ai and liked it.",
        "It takes about 30 to 40 minutes, i.e. one sitting.",
    ],
)
def test_ordinary_prose_is_not_mistaken_for_an_invented_link(prose: str) -> None:
    """A guard that mangles a real email fails invisibly. Two dotted segments
    is prose; three is a hostname."""
    body = f"{_body()}\n\n{prose}"
    assert lifecycle_email.link_defects(EMAIL_TYPE_ASSESSMENT_INVITATION, CTX, body) == []


def test_an_email_type_that_carries_no_link_is_not_link_checked() -> None:
    """The rejection email deliberately carries no link at all."""
    assert (
        lifecycle_email.link_defects(
            EMAIL_TYPE_REJECTED, CTX, "Hi Asha,\n\nNot this time.\n"
        )
        == []
    )


def test_a_link_invented_when_none_was_given_is_still_rejected() -> None:
    reasons = lifecycle_email.link_defects(
        EMAIL_TYPE_ASSESSMENT_REMINDER,
        {**CTX, "assessment_link": ""},
        _body("link.to.assessment"),
    )
    assert reasons and "no link" in reasons[0]


# ── The last line of defence ─────────────────────────────────────────────────

def test_repair_replaces_an_invented_link_with_the_real_one() -> None:
    fixed = lifecycle_email.repair_link(
        EMAIL_TYPE_ASSESSMENT_INVITATION, CTX, _body("link.to.assessment")
    )
    assert LINK in fixed
    assert "link.to.assessment" not in fixed


def test_repair_appends_the_link_when_the_body_has_none() -> None:
    fixed = lifecycle_email.repair_link(
        EMAIL_TYPE_ASSESSMENT_INVITATION,
        CTX,
        "Hi Asha,\n\nPlease complete your assessment.\n",
    )
    assert LINK in fixed


def test_repair_leaves_a_correct_body_alone() -> None:
    body = _body()
    assert lifecycle_email.repair_link(
        EMAIL_TYPE_ASSESSMENT_INVITATION, CTX, body
    ) == body.strip()


# ── The deterministic template already carried the link, and still must ──────

def test_the_deterministic_fallback_carries_the_real_link() -> None:
    """`run_loop` degrading to the template is the designed path when the model
    keeps writing a bad link, so the template is the thing the candidate
    actually receives on a bad day."""
    for email_type in (EMAIL_TYPE_ASSESSMENT_INVITATION, EMAIL_TYPE_ASSESSMENT_REMINDER):
        _subject, body = lifecycle_email.fallback_draft(email_type, CTX)
        assert LINK in body
        assert lifecycle_email.link_defects(email_type, CTX, body) == []


def test_the_prompts_forbid_inventing_a_web_address() -> None:
    """The prompt instruction is a request, not a guarantee, which is why the
    check above exists. It is still worth asking."""
    from app import prompts
    from app.models.email_log import EMAIL_TYPE_PROMPTS

    for email_type in (EMAIL_TYPE_ASSESSMENT_INVITATION, EMAIL_TYPE_ASSESSMENT_REMINDER):
        text = prompts.load(EMAIL_TYPE_PROMPTS[email_type])
        assert "NEVER invent" in text
        assert chr(8212) not in text
