"""One pagination vocabulary across the API, and the boundaries it gets right.

Four shapes had appeared, and every client was left to compute "is there a next
page?" itself. They disagree at exactly one place -- the last page -- which is
the place a pager is most visibly wrong.

`PageMeta` adds the derived fields to the shapes that lacked them, using the
names the richest existing response already used. Additive only: Section 1's
evolution rule is extend, never replace, so no field was removed or renamed and
no client breaks.
"""
from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from app.schemas import bd, candidates, jobs, matching, provider
from app.schemas.pagination import PageMeta

#: The responses that had NO derived navigation and now inherit it.
PAGED = [
    (bd.LeadListOut, "leads"),
    (bd.BDCustomerListOut, "customers"),
    (provider.CustomerListOut, "customers"),
]


def _build(model, collection: str, **counts):
    payload = {collection: [], **counts}
    # Some of these carry extra required scalars; fill anything still missing
    # with a zero/empty of the right kind rather than hand-writing four
    # fixtures that drift from the schemas.
    for name, field in model.model_fields.items():
        if name in payload or not field.is_required():
            continue
        annotation = str(field.annotation)
        payload[name] = (
            [] if "list" in annotation else 0 if "int" in annotation else ""
        )
    return model(**payload)


@pytest.mark.parametrize("model,collection", PAGED)
def test_every_paged_response_reports_the_same_navigation(model, collection) -> None:
    page = _build(model, collection, total=108, page=2, page_size=25)
    data = page.model_dump()
    assert data["total_pages"] == 5
    assert data["has_next"] is True
    assert data["has_previous"] is True


@pytest.mark.parametrize("model,collection", PAGED)
def test_an_empty_result_set_has_no_pages(model, collection) -> None:
    """0 pages, not 1.

    Reporting one page makes every empty table render "showing page 1 of 1",
    which reads as a loading failure rather than as "no results".
    """
    page = _build(model, collection, total=0, page=1, page_size=25)
    data = page.model_dump()
    assert data["total_pages"] == 0
    assert data["has_next"] is False
    assert data["has_previous"] is False


@pytest.mark.parametrize("model,collection", PAGED)
def test_the_last_page_says_there_is_no_next(model, collection) -> None:
    """The boundary every client was computing for itself, and the one they
    disagreed on."""
    page = _build(model, collection, total=50, page=2, page_size=25)
    data = page.model_dump()
    assert data["total_pages"] == 2
    assert data["has_next"] is False
    assert data["has_previous"] is True


@pytest.mark.parametrize("model,collection", PAGED)
def test_a_partial_last_page_still_counts_as_a_page(model, collection) -> None:
    """101 items at 25 a page is 5 pages, not 4."""
    page = _build(model, collection, total=101, page=5, page_size=25)
    data = page.model_dump()
    assert data["total_pages"] == 5
    assert data["has_next"] is False


def test_the_derived_fields_cannot_disagree_with_the_counts() -> None:
    """Computed, not stored. A handler that updates `total` and forgets
    `total_pages` is the inconsistency this replaces."""
    page = bd.LeadListOut(leads=[], total=10, page=1, page_size=25)
    assert page.total_pages == 1
    page.total = 100
    assert page.total_pages == 4, "the derived value did not follow the count"


def test_nothing_was_removed_from_any_existing_response() -> None:
    """The additive guarantee, asserted rather than reasoned about."""
    for model, _ in PAGED:
        fields = set(model.model_fields)
        assert {"total", "page", "page_size"} <= fields, model.__name__


def test_the_richest_existing_shape_was_not_disturbed() -> None:
    """`RankedCandidatesOut` already had this vocabulary; PageMeta adopted its
    names rather than inventing a fifth set. It must keep its own fields."""
    fields = set(jobs.RankedCandidatesOut.model_fields)
    for name in ("total", "page", "page_size", "total_pages", "has_next", "has_previous"):
        assert name in fields, name


def test_the_transcript_keeps_offset_pagination_on_purpose() -> None:
    """Not an inconsistency to fix. It pages a message STREAM, where a caller
    wants "the next fifty from here" rather than "page 4"."""
    from app.schemas.assessments import TranscriptOut

    fields = set(TranscriptOut.model_fields)
    assert {"limit", "offset", "total"} <= fields
    assert "page" not in fields


def test_the_applications_list_keeps_its_own_empty_set_convention() -> None:
    """One documented divergence, and the reason it is allowed to stand.

    `JobLinksOut` and `MatchResultsOut` already carried the derived fields and
    report a MINIMUM of one page where `PageMeta` reports zero. Both readings
    are defensible; these are already in a shipped client, and changing a
    number an existing UI renders is a replacement, not an extension. Both
    gained `has_previous`, so the vocabulary matches everywhere even where the
    empty-set convention does not.
    """
    for model, collection in (
        (candidates.JobLinksOut, "links"),
        (matching.MatchResultsOut, "results"),
    ):
        empty = model(job_id=uuid4(), **{collection: []})
        assert empty.total_pages == 1, model.__name__
        assert empty.has_next is False, model.__name__
        assert empty.has_previous is False, model.__name__
        assert not issubclass(model, PageMeta), model.__name__


def test_no_new_endpoint_invents_a_fifth_pagination_shape() -> None:
    """The guard that keeps this from drifting again.

    Anything carrying `page` and `page_size` must inherit the derived fields
    rather than hand-rolling them, so there is one answer to "is there a next
    page" for the whole API.
    """
    offenders: list[str] = []
    for module in (bd, candidates, provider, jobs, matching):
        for name, obj in vars(module).items():
            if not inspect.isclass(obj) or not hasattr(obj, "model_fields"):
                continue
            fields = set(obj.model_fields)
            if not {"page", "page_size", "total"} <= fields:
                continue
            derived = {"total_pages", "has_next", "has_previous"}
            has_own = derived <= fields
            inherits = issubclass(obj, PageMeta)
            if not (has_own or inherits):
                offenders.append(f"{module.__name__}.{name}")
    assert not offenders, (
        "these paginated responses carry neither the derived fields nor "
        f"PageMeta: {offenders}"
    )
