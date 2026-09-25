"""The job page's ranked table, asserted on the RESPONSE JSON of the real route.

WHY THE RESPONSE AND NOT THE SERIALIZER
---------------------------------------
The seven recruiter columns added on 2026-09-18 were computed by
`job_candidates._row_payload`, asserted there by a passing test, and dropped by
pydantic at the edge because the row schema did not declare them. Every CTC,
notice, education and BGV cell read its empty word in production for as long
as that test stayed green (PLAN-p2 NF-1). A serializer test cannot see that
failure; only the bytes a browser receives can. So every assertion here goes
through `GET /jobs/{job_id}/candidates` over a real database, entered with the
production RLS scope.

What is pinned:
* every recruiter column arrives as its WORD, from the response;
* the header is the owner's sentence until somebody is assessed, then says in
  words how the assessment moves the order;
* the order is ONE derived key with the Must-have cap after the blend, and a
  total order on ties;
* a tag's text is the skill's CURRENT name, so a rename moves the label and
  never the order, and marks the row as out of date;
* a databank row says it is not an applicant;
* no number reaches the payload: no numeric leaf outside the pager's counts,
  and no key shaped like a score, a percentage, a rank or a weight;
* an undeclared key FAILS rather than vanishing (`extra="forbid"`).
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.ranking import RankedCandidateOut, RankedCandidatesOut
from app.services import application_validation
from tests.ranking_world import (  # noqa: F401  -- fixtures
    Tenant,
    add_link,
    client_for,
    current_digest,
    execute,
    run,
    tenant_a,
)

HEADER_RESUME_ONLY = "Resume check only. Real skills are tested in the assessment."

#: The only integer fields a page may carry: the pager's own counts.
_COUNT_KEYS = frozenset(
    {"total", "page", "page_size", "total_pages", "range_start", "range_end",
     "new_candidate_count"}
)
_SCORE_SHAPED = re.compile(r"score|percent|rank|weight|tier", re.IGNORECASE)


@pytest.fixture
def client(tenant_a: Tenant) -> Iterator[TestClient]:
    yield from client_for(tenant_a)


def _page(client: TestClient, tenant: Tenant, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/v1/jobs/{tenant.job}/candidates", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _row(page: dict[str, Any], link: uuid.UUID) -> dict[str, Any]:
    for row in page["results"]:
        if row["link_id"] == str(link):
            return row
    raise AssertionError(f"link {link} is not on the page")


def _provenance(tenant: Tenant, **extra: Any) -> dict[str, Any]:
    record = {
        "contract_digest": current_digest(tenant),
        "contract_version": 0,
        "components_present": ["must_have_evidenced", "experience_level", "role_fit"],
        "components_excluded": {},
        "validation_parts": {},
        "degraded": False,
    }
    record.update(extra)
    return record


def _skill_tag(tenant: Tenant, name: str, polarity: str = "positive") -> dict[str, Any]:
    tag: dict[str, Any] = {
        "kind": "skill",
        "skill_id": str(tenant.skills[name]),
        "polarity": polarity,
    }
    if polarity == "positive":
        tag.update({"strength": "strong", "quote": "Five years of Python services."})
    return tag


# ── Every recruiter column reaches the browser ───────────────────────────────


def test_every_recruiter_column_reaches_the_response_as_its_word(client, tenant_a) -> None:
    link = add_link(
        tenant_a,
        name="Asha Rao",
        yukti_status="scored",
        pre=82.0,
        tags=[_skill_tag(tenant_a, "Python")],
        provenance=_provenance(
            tenant_a, validation_parts={"ctc": "Within range", "notice": "Within 30 days"}
        ),
        validation={
            "expected_ctc": "20,00,000",
            "notice_period": "30 days",
            "document_readiness": application_validation.DOCUMENT_READINESS_OPTIONS[0],
        },
        profile_form={"education": {"graduation": {"degree": "B.Tech"}}},
    )
    row = _row(_page(client, tenant_a), link)
    assert row["ctc_match_label"] == "Within range"
    assert row["notice_period_label"] == "Within 30 days"
    assert row["education_match_label"] == "Match"
    assert row["bgv_status"] == "not_required"
    assert row["bgv_status_label"] == "Not Required"
    assert row["ai_match_status"] == "scored"
    assert row["ai_match_label"] == "Matching"
    assert row["ai_match_status_word"] is None
    assert row["applicant_label"] is None
    assert row["ai_match_stale"] is False
    assert row["evidence_tags"] == [
        {"text": "Python", "polarity": "positive", "shown_in_row": True}
    ]
    assert "Resume check: skills, experience and the role, read from the resume." in row[
        "provenance"
    ]
    assert (
        "Application answers: expected pay within range and notice period within 30 days."
        in row["provenance"]
    )


# ── The header ───────────────────────────────────────────────────────────────


def test_the_header_is_the_owner_sentence_until_somebody_is_assessed(client, tenant_a) -> None:
    add_link(tenant_a, name="Unassessed", yukti_status="scored", pre=70.0,
             provenance=_provenance(tenant_a))
    assert _page(client, tenant_a)["ranking_header"] == HEADER_RESUME_ONLY

    add_link(tenant_a, name="Assessed", yukti_status="scored", pre=70.0,
             provenance=_provenance(tenant_a), overall=80)
    header = _page(client, tenant_a)["ranking_header"]
    assert header == (
        "Assessed candidates are ranked mostly on their Tatva Assessment. "
        "Everyone else is a resume check only."
    )
    assert not any(char.isdigit() for char in header)


def test_the_header_follows_the_tenant_ratio_as_a_word(client, tenant_a) -> None:
    add_link(tenant_a, name="Assessed", yukti_status="scored", pre=70.0,
             provenance=_provenance(tenant_a), overall=80)
    run(execute(
        "UPDATE tenants SET yukti_assessment_weight_pct = 100 WHERE id = :id",
        {"id": str(tenant_a.id)},
    ))
    assert _page(client, tenant_a)["ranking_header"].startswith(
        "Assessed candidates are ranked entirely on their Tatva Assessment."
    )


# ── The order: one key, the cap after the blend, a total order ───────────────


def test_one_key_orders_assessed_and_unassessed_together_and_the_cap_binds(
    client, tenant_a
) -> None:
    strong = add_link(tenant_a, name="Strong resume", yukti_status="scored", pre=95.0,
                      provenance=_provenance(tenant_a), created_days_ago=5)
    # 0.7 x 80 + 0.3 x 95 = 84.5 before the cap; a failed Must-have holds it
    # at the ceiling, which grades Moderately Matching.
    capped = add_link(tenant_a, name="Failed must-have", yukti_status="scored",
                      pre=95.0, provenance=_provenance(tenant_a), overall=80,
                      must_have_failed=True, created_days_ago=4)
    middling = add_link(tenant_a, name="Middling resume", yukti_status="scored",
                        pre=72.0, provenance=_provenance(tenant_a), created_days_ago=3)
    pending = add_link(tenant_a, name="Not read yet", created_days_ago=2)
    failed = add_link(tenant_a, name="Not assessed", yukti_status="not_assessed",
                      provenance=_provenance(tenant_a), created_days_ago=1)
    run(execute(
        "UPDATE job_candidate_links SET yukti_failure_reason = 'model_unavailable' "
        "WHERE id = :id",
        {"id": str(failed)},
    ))

    page = _page(client, tenant_a)
    order = [row["link_id"] for row in page["results"]]
    assert order == [str(strong), str(middling), str(capped), str(pending), str(failed)]

    assert _row(page, strong)["ai_match_label"] == "Highly Matching"
    capped_row = _row(page, capped)
    assert capped_row["ai_match_label"] == "Moderately Matching"
    assert (
        "Held at Moderately Matching or below: a Must-have skill was not "
        "demonstrated in the assessment." in capped_row["provenance"]
    )
    assert "Tatva Assessment: mostly decides this grade." in capped_row["provenance"]
    pending_row = _row(page, pending)
    assert pending_row["ai_match_label"] is None
    assert pending_row["ai_match_status_word"] == "Not checked yet"
    failed_row = _row(page, failed)
    assert failed_row["ai_match_label"] is None
    assert failed_row["ai_match_status_word"] == "Not assessed"
    assert (
        "The AI check could not be completed. It is retried on the next AI Matching run."
        in failed_row["provenance"]
    )


def test_equal_keys_order_by_arrival_then_id_across_pages(client, tenant_a) -> None:
    older = add_link(tenant_a, name="Older", yukti_status="scored", pre=80.0,
                     provenance=_provenance(tenant_a), created_days_ago=6)
    newer = add_link(tenant_a, name="Newer", yukti_status="scored", pre=80.0,
                     provenance=_provenance(tenant_a), created_days_ago=2)
    first = _page(client, tenant_a, page=1, page_size=1)
    second = _page(client, tenant_a, page=2, page_size=1)
    assert [r["link_id"] for r in first["results"]] == [str(older)]
    assert [r["link_id"] for r in second["results"]] == [str(newer)]


# ── Renames move the label, never the order ──────────────────────────────────


def test_a_renamed_skill_changes_the_tag_text_and_never_the_order(client, tenant_a) -> None:
    first = add_link(tenant_a, name="First", yukti_status="scored", pre=88.0,
                     tags=[_skill_tag(tenant_a, "Python"),
                           _skill_tag(tenant_a, "Kafka", "negative")],
                     provenance=_provenance(tenant_a))
    second = add_link(tenant_a, name="Second", yukti_status="scored", pre=76.0,
                      tags=[_skill_tag(tenant_a, "Kafka")],
                      provenance=_provenance(tenant_a))
    before = _page(client, tenant_a)
    assert _row(before, first)["evidence_tags"] == [
        {"text": "Python", "polarity": "positive", "shown_in_row": True},
        {"text": "Kafka", "polarity": "negative", "shown_in_row": True},
    ]

    run(execute(
        "UPDATE job_competencies SET name = 'Python services' WHERE id = :id",
        {"id": str(tenant_a.skills["Python"])},
    ))
    after = _page(client, tenant_a)
    assert [r["link_id"] for r in after["results"]] == [
        r["link_id"] for r in before["results"]
    ] == [str(first), str(second)]
    assert [r["ai_match_label"] for r in after["results"]] == [
        r["ai_match_label"] for r in before["results"]
    ]
    renamed = _row(after, first)
    assert renamed["evidence_tags"][0]["text"] == "Python services"
    # The digest the reading was made against no longer describes the skills.
    assert renamed["ai_match_stale"] is True
    assert (
        "The skills changed after this check. Run AI Matching to refresh."
        in renamed["provenance"]
    )


def test_a_reading_of_another_resume_is_out_of_date(client, tenant_a) -> None:
    link = add_link(tenant_a, name="Swapped resume", yukti_status="scored", pre=80.0,
                    provenance=_provenance(tenant_a), read_other_profile=True)
    row = _row(_page(client, tenant_a), link)
    assert row["ai_match_stale"] is True
    assert "The resume changed after this check. Run AI Matching to refresh." in row["provenance"]


def test_a_legacy_row_is_graded_and_asks_for_a_refresh(client, tenant_a) -> None:
    link = add_link(tenant_a, name="Legacy", yukti_status="legacy", pre=91.0)
    row = _row(_page(client, tenant_a), link)
    assert row["ai_match_label"] == "Highly Matching"
    assert row["ai_match_stale"] is True
    assert row["evidence_tags"] == []
    assert "Checked before evidence tags existed. Run AI Matching to refresh." in row["provenance"]


# ── Databank rows ────────────────────────────────────────────────────────────


def test_a_databank_row_says_it_is_not_an_applicant(client, tenant_a) -> None:
    link = add_link(
        tenant_a, name="Found in the databank", status="sourced", source_type="databank",
        yukti_status="scored", pre=78.0,
        provenance=_provenance(
            tenant_a, components_excluded={"validation_fit": "not_answered"}
        ),
    )
    row = _row(_page(client, tenant_a), link)
    assert row["applicant_label"] == "Databank, not an applicant"
    assert row["status"] == "sourced"
    assert "Application answers: not given, this candidate has not applied." in row["provenance"]


# ── No number, and nothing undeclared ────────────────────────────────────────


def _numeric_leaves(value: Any, path: str = "") -> list[str]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [path]
    if isinstance(value, dict):
        return [
            leaf
            for key, child in value.items()
            for leaf in _numeric_leaves(child, f"{path}.{key}" if path else key)
        ]
    if isinstance(value, list):
        return [
            leaf for index, child in enumerate(value)
            for leaf in _numeric_leaves(child, f"{path}[{index}]")
        ]
    return []


def _pairs(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return list(value.items()) + [p for child in value.values() for p in _pairs(child)]
    if isinstance(value, list):
        return [p for child in value for p in _pairs(child)]
    return []


def test_no_number_and_no_score_shaped_key_reaches_the_page(client, tenant_a) -> None:
    add_link(tenant_a, name="Scored", yukti_status="scored", pre=83.7,
             tags=[_skill_tag(tenant_a, "Python")], provenance=_provenance(tenant_a))
    add_link(tenant_a, name="Assessed", yukti_status="scored", pre=64.2,
             provenance=_provenance(tenant_a), overall=77, must_have_failed=True)
    page = _page(client, tenant_a)
    leaves = [leaf for leaf in _numeric_leaves(page) if leaf not in _COUNT_KEYS]
    assert leaves == [], leaves
    # A score-shaped KEY may carry only a sentence (`ranking_header` is the
    # one, and it is words); anything else under such a key is a number or a
    # structure that could hold one.
    shaped = sorted(
        key for key, value in _pairs(page)
        if _SCORE_SHAPED.search(key) and not isinstance(value, str)
    )
    assert shaped == [], shaped


def test_an_undeclared_row_key_fails_instead_of_vanishing() -> None:
    """The exact production bug, turned around: a key the serializer adds
    without declaring it must fail the request, not be dropped."""
    base = {
        "link_id": uuid.uuid4(), "candidate_id": uuid.uuid4(), "full_name": "A",
        "status": "applied", "stage_label": "Applied", "bgv_status": "not_required",
        "bgv_status_label": "Not Required", "ai_match_status": "pending",
    }
    RankedCandidateOut.model_validate(base)
    with pytest.raises(ValidationError):
        RankedCandidateOut.model_validate({**base, "match_percent": 87})
    with pytest.raises(ValidationError):
        RankedCandidatesOut.model_validate(
            {"job_id": uuid.uuid4(), "grade": "cxo", "ranking_header": "x",
             "results": [], "total": 0, "page": 1, "page_size": 25, "total_pages": 0,
             "has_next": False, "has_previous": False, "range_start": 0, "range_end": 0,
             "level": "CXO"}
        )


def test_the_schemas_declare_no_number_field() -> None:
    """Belt to the JSON walk's braces: no declared row field is numeric, and
    the page's integers are exactly the pager's counts."""
    for name, field in RankedCandidateOut.model_fields.items():
        annotation = str(field.annotation)
        assert "int" not in annotation and "float" not in annotation, name
    integers = {
        name for name, field in RankedCandidatesOut.model_fields.items()
        if field.annotation is int
    }
    assert integers == _COUNT_KEYS
