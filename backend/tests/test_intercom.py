"""Intercom sends customer data and never candidate data, structurally.

The rule this file defends is one sentence: a candidate is not a contact. The
reason it needs a file is that the rule is easy to state, easy to agree with,
and broken by the ordinary act of adding a column to a table three months from
now. Every assertion here is aimed at that future change rather than at the
code as written today.
"""
from __future__ import annotations

import types

import pytest

from app.services import intercom


def _tenant(**overrides):
    """A `tenants`-shaped stand-in carrying MORE than the allowlist admits.

    The extra attributes are the point. A projection that iterated the object
    would forward them, and every one of them is something a support tool has
    no business holding.
    """
    base = {
        "id": "8f14e45f-0000-4000-8000-00000000c0de",
        "name": "Northwind Talent",
        "website_domain": "northwind.example",
        "industry": "Logistics",
        "status": "active",
        # None of the following is allowlisted, and all of it is realistic.
        "credit_deficit": 1200,
        "razorpay_subscription_id": "sub_XXXXXXXXXXXX",
        "notes": "internal: renewal at risk, escalate to founder",
        "candidate_count": 4218,
        "average_assessment_score": 71.4,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _user(role="recruiter", **overrides):
    base = {
        "email": "priya@northwind.example",
        "full_name": "Priya R",
        "role": role,
        "permissions_json": {"decide_profile": True},
        "firebase_uid": "uid-abc",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ── the allowlist, and what it must never grow into ──────────────────────────


def test_the_company_projection_sends_only_allowlisted_names() -> None:
    """Iterating the ALLOWLIST, not the row. This is the whole guarantee.

    The stand-in carries a credit balance, a Razorpay subscription id, internal
    renewal notes, a candidate count and an average score. A projection built
    the other way round -- iterate the row, drop what you remember to drop --
    would forward every one of them, and would keep forwarding whatever was
    added next.
    """
    payload = intercom.project_company(_tenant())
    assert set(payload) <= set(intercom.COMPANY_FIELDS), payload
    for leaked in (
        "credit_deficit",
        "razorpay_subscription_id",
        "notes",
        "candidate_count",
        "average_assessment_score",
    ):
        assert leaked not in payload
    # And no allowlisted VALUE smuggled one in.
    assert "1200" not in str(payload)
    assert "renewal at risk" not in str(payload)


def test_no_allowlisted_name_is_candidate_shaped() -> None:
    """The assertion that fires when somebody WIDENS the allowlist.

    `_assert_no_forbidden_name` runs at projection time and would catch this in
    production, but only for a tenant that actually carried the field. This
    checks the DECLARATION, so a bad entry fails the build rather than waiting
    for the right row to come along.
    """
    for name in list(intercom.COMPANY_FIELDS) + list(intercom.CONTACT_FIELDS):
        for forbidden in intercom.FORBIDDEN_SUBSTRINGS:
            assert forbidden not in name.lower(), (
                f"the Intercom allowlist gained {name!r}, which contains "
                f"{forbidden!r}. Candidate data does not go to a support tool."
            )


def test_a_forbidden_field_name_raises_rather_than_being_filtered() -> None:
    """Refusing beats filtering, because filtering is silent.

    A filter would send the rest of the payload and leave nobody aware that a
    field had been added which should never have been considered.
    """
    with pytest.raises(ValueError, match="candidate"):
        intercom._assert_no_forbidden_name({"name": "ok", "candidate_count": 3})


# ── a candidate is not a contact ─────────────────────────────────────────────


def test_a_candidate_is_never_projected_as_a_contact() -> None:
    assert intercom.project_contact(_user(role="candidate")) is None


def test_an_unknown_role_is_refused_rather_than_admitted() -> None:
    """`SYNCABLE_ROLES` is an allowlist, and this is why.

    A check written as `role != "candidate"` would admit every role invented
    later, including one that turns out to be candidate-shaped. A role this
    product has never heard of is refused.
    """
    assert intercom.project_contact(_user(role="applicant")) is None
    assert intercom.project_contact(_user(role="")) is None
    assert "candidate" not in intercom.SYNCABLE_ROLES


def test_a_staff_contact_carries_only_the_three_allowlisted_fields() -> None:
    payload = intercom.project_contact(_user())
    assert payload is not None
    assert set(payload) <= set(intercom.CONTACT_FIELDS)
    assert "permissions_json" not in payload
    assert "firebase_uid" not in payload


def test_a_contact_without_an_email_is_not_a_contact() -> None:
    """Intercom keys a contact on email; a row without one would create a
    contact nobody can ever match to a conversation."""
    assert intercom.project_contact(_user(email="")) is None


# ── unconfigured is a choice, not a failure ──────────────────────────────────


def test_an_unconfigured_deployment_sends_nothing_and_says_so(monkeypatch) -> None:
    """The same shape `TAVILY_API_KEY` already has.

    Reporting an absent optional credential as a failure would make the signal
    that flags a real outage fire constantly and stop being read, which is the
    argument `rag/reranker` makes for not calling a chosen lexical pass a
    degradation.
    """
    monkeypatch.setattr(intercom, "_token", lambda: "")

    def explode(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("an unconfigured deployment made a network call")

    monkeypatch.setattr(intercom, "_post", explode)

    outcome = intercom.sync_company(_tenant())
    assert outcome.status == intercom.STATUS_UNCONFIGURED
    assert outcome.reason and "not set" in outcome.reason
    assert outcome.fields_sent == ()


def test_a_vendor_failure_records_the_fault_class_and_never_the_body(
    monkeypatch,
) -> None:
    """A support payload carries a customer's contact details.

    The record says `http_422`, not what Intercom echoed back, because this
    string reaches a log sink far more widely readable than the database.
    """
    monkeypatch.setattr(intercom, "_token", lambda: "token-shaped-string")

    def fail(*_args, **_kwargs):
        raise intercom.IntercomUnavailable("http_422")

    monkeypatch.setattr(intercom, "_post", fail)

    outcome = intercom.sync_company(_tenant())
    assert outcome.status == intercom.STATUS_UNAVAILABLE
    assert outcome.reason == "http_422"


def test_the_company_is_keyed_on_our_own_tenant_id(monkeypatch) -> None:
    """A customer who renames themselves stays ONE company in Intercom.

    Keying on the name would make a rename create a second company whose
    history starts that day, which is the failure `bd_leads.promoted_tenant_id`
    already records for a re-signed lead.
    """
    monkeypatch.setattr(intercom, "_token", lambda: "token-shaped-string")
    sent: dict = {}

    def capture(path, body):
        sent["path"] = path
        sent["body"] = body
        return {}

    monkeypatch.setattr(intercom, "_post", capture)

    tenant = _tenant()
    assert intercom.sync_company(tenant).status == intercom.STATUS_OK
    assert sent["path"] == "/companies"
    assert sent["body"]["company_id"] == str(tenant.id)

    # And a rename does not change the key.
    sent.clear()
    intercom.sync_company(_tenant(name="Northwind Talent Group"))
    assert sent["body"]["company_id"] == str(tenant.id)


def test_the_whole_request_body_is_free_of_candidate_shaped_names(
    monkeypatch,
) -> None:
    """Checked on the BODY, after assembly, not only on the projection.

    `sync_company` nests part of the payload under `custom_attributes`, so a
    leak could be introduced by the ASSEMBLY rather than by the allowlist. This
    is the assertion that survives somebody restructuring that body.
    """
    monkeypatch.setattr(intercom, "_token", lambda: "token-shaped-string")
    sent: dict = {}

    def capture(_path, body):
        sent["body"] = body
        return {}

    monkeypatch.setattr(intercom, "_post", capture)
    intercom.sync_company(_tenant())

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                for forbidden in intercom.FORBIDDEN_SUBSTRINGS:
                    assert forbidden not in key.lower(), f"{path}.{key}"
                walk(value, f"{path}.{key}")

    walk(sent["body"])


def test_the_sweep_is_scheduled_and_registered() -> None:
    """A task nobody schedules is a task that never runs, which this codebase
    has paid for once already with a beat entry that outlived its module."""
    from app.workers import schedule

    assert "pickready.sync_intercom_companies" in {e.task for e in schedule.SCHEDULE}
