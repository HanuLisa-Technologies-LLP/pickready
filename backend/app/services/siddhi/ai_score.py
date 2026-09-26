"""The PRISM Report's AI Score section: Yukti's pre-assessment snapshot, frozen.

WHAT REPLACED WHAT (PLAN-p5 section 3.3, decision D3)
-------------------------------------------------------
The AI Score section used to be four matching categories, each with a 25 to 30
word remark a model wrote at scoring time from `match_breakdown_json`. Phase 2
retires the matching categories; what survives is Yukti's pre-assessment
snapshot of the application: a grade WORD, the evidence tags it rests on (each
with a polarity), and a one-line header. Siddhi does not compute any of it and
calls no model for it. It FREEZES the snapshot onto the immutable report
(`functional_skills_reports.ai_score_json`), because the report is a permanent
record of what the recruiter was told before the assessment, and Yukti's live
value moves every time the application is re-ranked.

WHAT THIS MODULE GUARANTEES ABOUT THE FROZEN VALUE
----------------------------------------------------
  * NO NUMBER. Yukti's internal score never enters the snapshot (the source
    shape has no field for it), and every string is scanned with the delivered
    document's own rule (`siddhi.numbers.scan_text`), the one the PDF renderer
    raises on. A tag or header that fails it is WITHHELD, recorded as a reason
    code on the snapshot, and flags the report for review
    (`ai_score_text_withheld`): a snapshot that would make the PDF refuse after
    the report was written is caught here instead.
  * NO EM DASH, by the same route.
  * A CLOSED VOCABULARY. The status is one of `STATUSES`, the grade one of
    the four grade words or absent, a polarity positive or negative. Anything
    else RAISES: it is a defect in the source, identical on every retry, and
    rendering a value outside the vocabulary would put a word nobody defined in
    front of a client.
  * `scored` HAS A GRADE AND NOTHING ELSE DOES. A snapshot saying Yukti could
    not assess the resume carries no grade word, so the report can never state
    one Yukti did not decide.

THE SEAM
--------
Yukti lands from Phase 2. `snapshot_for_report(session, link_id, source=...)`
takes the reader as an argument (`SnapshotSource`), so this package imports
nothing from Yukti and the orchestrator wires
`source=yukti.pre_assessment_snapshot`, whose result carries `status`,
`grade`, `tags` (each `text` and `polarity`) and `header`, read by attribute.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from app.services.rating import GRADES
from app.services.siddhi import numbers

logger = logging.getLogger(__name__)

__all__ = [
    "STATUS_SCORED",
    "STATUS_NOT_ASSESSED",
    "STATUS_PENDING",
    "STATUSES",
    "POLARITY_POSITIVE",
    "POLARITY_NEGATIVE",
    "POLARITIES",
    "WITHHELD_NUMBER",
    "WITHHELD_EM_DASH",
    "SNAPSHOT_SHAPE",
    "AiScoreTag",
    "AiScoreSnapshot",
    "SnapshotSource",
    "snapshot_from",
    "snapshot_for_report",
    "read_snapshot",
]

#: Yukti's statuses for an application's pre-assessment read (CONTRACT v2, P2).
#: `legacy` is Yukti's word for a row scored before it existed; a report written
#: now never freezes that, so it is not a status a snapshot can carry.
STATUS_SCORED = "scored"
STATUS_NOT_ASSESSED = "not_assessed"
STATUS_PENDING = "pending"
STATUSES: tuple[str, ...] = (STATUS_SCORED, STATUS_NOT_ASSESSED, STATUS_PENDING)

POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"
POLARITIES: tuple[str, ...] = (POLARITY_POSITIVE, POLARITY_NEGATIVE)

#: Why a piece of the snapshot was withheld. Codes, never the withheld text.
WITHHELD_NUMBER = "number"
WITHHELD_EM_DASH = "em_dash"

#: The stored shape's name, so a later change to it can read this one. A
#: STRING, not a version number: the stored value is read by the report
#: serializer, and the number ban's structural rule refuses any numeric field
#: in a delivered payload, whatever it counts.
SNAPSHOT_SHAPE = "ai_score_snapshot.v1"

_EM_DASH = chr(8212)


@dataclass(frozen=True)
class AiScoreTag:
    """One evidence tag: a short phrase and whether it counts for or against."""

    text: str
    polarity: str

    def __post_init__(self) -> None:
        if self.polarity not in POLARITIES:
            raise ValueError(f"Unknown evidence tag polarity {self.polarity!r}")
        if not self.text:
            raise ValueError("an evidence tag has no text")


@dataclass(frozen=True)
class AiScoreSnapshot:
    """The frozen AI Score section. Words only; see the module docstring."""

    status: str
    grade: str | None
    tags: tuple[AiScoreTag, ...] = ()
    header: str = ""
    #: Reason codes for every piece withheld from the source, in order. Never
    #: the text, which is exactly what must not be shown.
    withheld: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"Unknown AI Score status {self.status!r}")
        if self.grade is not None and self.grade not in GRADES:
            raise ValueError(f"{self.grade!r} is not a grade word")
        if (self.status == STATUS_SCORED) != (self.grade is not None):
            raise ValueError(
                "a scored snapshot carries a grade word and no other status does"
            )

    @property
    def needs_human_review(self) -> bool:
        return bool(self.withheld)

    def review_findings(self) -> list[dict[str, str]]:
        """The report row's findings for this section. No prose, ever."""
        if not self.withheld:
            return []
        return [
            {
                "severity": "medium",
                "issue": "ai_score_text_withheld",
                "location": "report.ai_score",
                "recommendation": (
                    "Part of the pre-assessment summary could not be shown "
                    "because it did not meet the report's wording rules. Read "
                    "the candidate's resume before relying on this section."
                ),
            }
        ]

    def as_json(self) -> dict[str, Any]:
        """`functional_skills_reports.ai_score_json`."""
        return {
            "shape": SNAPSHOT_SHAPE,
            "status": self.status,
            "grade": self.grade,
            "header": self.header,
            "tags": [{"text": tag.text, "polarity": tag.polarity} for tag in self.tags],
            "withheld": list(self.withheld),
        }


#: `source(session, link_id) -> Yukti's snapshot or None`. The production value
#: is `yukti.pre_assessment_snapshot`; a test passes a stub.
SnapshotSource = Callable[[Any, uuid.UUID], Awaitable[Any]]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _refusal(text: str, *, path: str) -> str | None:
    """Why `text` may not be shown in a delivered report, or None."""
    if _EM_DASH in text:
        return WITHHELD_EM_DASH
    if numbers.scan_text(text, path=path):
        return WITHHELD_NUMBER
    return None


def snapshot_from(source: Any) -> AiScoreSnapshot:
    """Yukti's snapshot, checked and frozen. Pure.

    Reads `status`, `grade`, `tags` (each `text`, `polarity`) and `header` by
    attribute, and nothing else: in particular no score, which is why a number
    cannot arrive by a field somebody adds to Yukti's value later.
    """
    withheld: list[str] = []
    header = _clean(getattr(source, "header"))
    refused = _refusal(header, path="ai_score.header")
    if refused:
        withheld.append(refused)
        header = ""
    tags: list[AiScoreTag] = []
    for position, raw in enumerate(getattr(source, "tags") or ()):
        text = _clean(getattr(raw, "text"))
        if not text:
            continue
        refused = _refusal(text, path=f"ai_score.tags[{position}]")
        if refused:
            withheld.append(refused)
            continue
        tags.append(AiScoreTag(text=text, polarity=str(getattr(raw, "polarity"))))
    grade = getattr(source, "grade")
    snapshot = AiScoreSnapshot(
        status=str(getattr(source, "status")),
        grade=str(grade) if grade is not None else None,
        tags=tuple(tags),
        header=header,
        withheld=tuple(withheld),
    )
    if snapshot.withheld:
        logger.warning(
            "siddhi.ai_score.text_withheld reasons=%s", ",".join(snapshot.withheld)
        )
    return snapshot


async def snapshot_for_report(
    session: Any, link_id: uuid.UUID, *, source: SnapshotSource
) -> AiScoreSnapshot | None:
    """The snapshot to freeze onto this application's report, or None.

    None means Yukti holds nothing for the application: the report stores no
    snapshot and the section renders its empty state. It is never a snapshot
    invented to fill the section.
    """
    found = await source(session, link_id)
    if found is None:
        return None
    return snapshot_from(found)


def read_snapshot(ai_score_json: Mapping[str, Any] | None) -> AiScoreSnapshot | None:
    """The stored snapshot, typed. None for a report written without one.

    A stored snapshot that is present and malformed RAISES: it is this
    system's own immutable record, so a bad shape is a defect to fix, not a
    state to render. The withheld codes are read back so a serializer can
    say a part was withheld; the text never existed in storage to read.
    """
    if not ai_score_json:
        return None
    return AiScoreSnapshot(
        status=str(ai_score_json["status"]),
        grade=ai_score_json.get("grade"),
        tags=tuple(
            AiScoreTag(text=str(tag["text"]), polarity=str(tag["polarity"]))
            for tag in ai_score_json.get("tags") or ()
        ),
        header=str(ai_score_json.get("header") or ""),
        withheld=tuple(str(code) for code in ai_score_json.get("withheld") or ()),
    )
