"""The delivered PRISM Report payload, and the number ban attached to it.

WHY THIS FILE EXISTS SEPARATELY FROM `schemas/assessments.py`
--------------------------------------------------------------
Because the rule it carries is about DELIVERY, not about the assessment API. The
same guarantee has to hold for the JSON response, the PDF, an email body and an
attachment (spec-doc6 D8), and three of those four are not response models at
all. Putting the rule in a mixin here lets one implementation cover a pydantic
model, a plain dict and a bare string, which is what "serialiser-level" has to
mean if it is to survive the next export format somebody adds.

WHAT `NumberFreeDelivery` DOES, AND WHEN
------------------------------------------
It runs AFTER validation, on the assembled model, and it walks every field --
not a list of known ones. A ban that enumerated the fields it checked would pass
on the day somebody added a field, which is the only day it matters. The two
exemptions (a radar axis's band index, and the candidate's own verbatim
submission) live in `siddhi.numbers` and are argued there.

It RAISES rather than redacting. A response that silently dropped a field would
leave the reader unable to tell a redaction from an omission, and a Ready Pick
Score that reached a response model is a defect in the model, not in the row.
The failure is loud, names the exact path, and is the actionable form of the
D8 ruling: it must be technically impossible for the dashboard's triage number
to enter a delivered report.

Pydantic wraps a validator's `ValueError`, so what a route sees is a
`ValidationError` carrying the ban's message rather than
`NumberInDeliveredReport` itself. That is the right shape here: the model
refuses to CONSTRUCT, which is earlier and louder than refusing to serialise,
and the message that survives names the field somebody added. A caller that
needs to catch the ban by type checks a payload with `assert_deliverable`
instead.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator

from app.services.siddhi import numbers

__all__ = [
    "NumberFreeDelivery",
    "ReadyPickNoteOut",
    "assert_deliverable",
]


class NumberFreeDelivery(BaseModel):
    """Mixin: a response model that refuses to serialise a number.

    Inherited by every model that is a DELIVERED PRISM payload. It is a mixin
    rather than a decorator so that the guarantee is visible in the class
    declaration of anything that carries it: a reader of the response model can
    see the rule without going to look for a registration list.
    """

    @model_validator(mode="after")
    def _no_numbers_reach_a_client(self) -> "NumberFreeDelivery":
        numbers.assert_clean(self, where=f"prism.{type(self).__name__}")
        return self


class ReadyPickNoteOut(BaseModel):
    """The dashboard's one-line note. The sentence, and nothing else.

    `siddhi.synthesis.ReadyPickNote` carries the evidence refs the sentence
    rests on; this is the shape that crosses the API boundary, and it
    deliberately drops them. The dashboard renders one line, a ref is an
    internal audit locator that identifies a row and authorises nothing, and a
    locator shipped to a browser is a locator somebody will eventually read
    back as permission.

    The refs are not lost: they are persisted with the immutable report, which
    is the only place a claim's provenance is any use.
    """

    sentence: str


def assert_deliverable(payload: Any, *, where: str) -> None:
    """The ban, for a payload that is not a model. Same rule, one implementation."""
    numbers.assert_clean(payload, where=where)
