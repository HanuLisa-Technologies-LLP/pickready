"""One pagination vocabulary, added without breaking any existing response.

WHAT WAS DRIFTING
-----------------
Four shapes had appeared across the API:

    {page, page_size, total}                      bd leads, bd customers,
                                                  candidate applications,
                                                  provider customers
    {page, page_size, results, total,
     total_pages, has_next, has_previous, ...}    the candidate table, matching
    {limit, offset, total}                        the assessment transcript
    {total}                                       outreach delivery status

Every client then computes "is there a next page?" itself, slightly
differently, and the answer at the last page is where they disagree.

WHAT THIS DOES
--------------
`PageMeta` supplies `total_pages`, `has_next` and `has_previous` as COMPUTED
fields, derived from `total`, `page` and `page_size` that the response already
carries. Mixing it in ADDS fields and removes none, so no existing client
breaks -- Section 1's evolution rule is extend, never replace.

The names are deliberately the ones `RankedCandidatesOut` already uses. The
richest shape in the product was already right; the others were missing pieces
of it. Converging on the existing vocabulary rather than inventing a fifth is
the whole point.

WHAT IT DELIBERATELY LEAVES ALONE
---------------------------------
The transcript's `{limit, offset}`. It pages a message STREAM, where a caller
wants "the next fifty from here" rather than "page 4", and its own module
documents that choice. Forcing it into pages would be consistency for its own
sake against a real reason.
"""
from __future__ import annotations

from pydantic import BaseModel, computed_field

__all__ = ["PageMeta"]


class PageMeta(BaseModel):
    """Derived page navigation. Mix in beside `total`, `page`, `page_size`.

        class LeadListOut(PageMeta):
            leads: list[LeadOut]
            total: int
            page: int
            page_size: int

    Computed rather than stored so it can never disagree with the counts beside
    it: a handler that updates `total` and forgets `total_pages` is the exact
    inconsistency this replaces.
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pages(self) -> int:
        """Ceiling division, and 0 when there is nothing.

        NOT 1: an empty result set has no pages, and reporting one makes every
        "showing page 1 of 1" render over an empty table.
        """
        total = int(getattr(self, "total", 0) or 0)
        size = int(getattr(self, "page_size", 0) or 0)
        if total <= 0 or size <= 0:
            return 0
        return -(-total // size)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_next(self) -> bool:
        page = int(getattr(self, "page", 1) or 1)
        return page < self.total_pages

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_previous(self) -> bool:
        return int(getattr(self, "page", 1) or 1) > 1
