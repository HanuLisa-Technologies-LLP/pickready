"""The serialiser-level number ban (spec-doc6 D8).

    "The Ready Pick Score (0-100 plus band plus confidence) is a dashboard
     triage artifact. It renders in the candidate list and nowhere else. It
     must be technically impossible for it to enter a delivered report:
     enforce with a serialiser-level rule and a test that asserts no numeric
     score field appears in any PRISM payload."

WHY THE RULE LIVES AT THE SERIALISER AND NOT AT THE GENERATOR
--------------------------------------------------------------
Because the generator is not the only thing that writes a delivered report. A
PDF renderer reads the same payload, an email body quotes it, an attachment
carries it, and each of those is a separate piece of code that could be given a
field the generator never intended to publish. A rule enforced once, at the
point where the object becomes bytes somebody receives, holds for every one of
them; a rule enforced in the generator holds only for the paths that go through
the generator, which is exactly the set of paths a future change adds one to.

It is also the only formulation that survives the dashboard existing. D8 rules
that the Ready Pick Score renders in the candidate list; the report and the
dashboard therefore read from overlapping state, and "do not put the number in
the report" stops being a property of one function and becomes a property of the
boundary between two surfaces.

THE THREE RULES, AND WHY THERE ARE THREE RATHER THAN ONE
----------------------------------------------------------
1. NUMERIC FIELD. Any int, float or Decimal at any path in the payload. This is
   the structural half and it is the one that catches a Ready Pick Score: a
   score does not arrive as prose, it arrives as a field somebody added to a
   response model. It needs no pattern and cannot be evaded by wording.

2. SCORE-SHAPED KEY. A numeric value under a key that names a score, checked
   even inside the one subtree where rule 1 is relaxed. See VERBATIM below.

3. SCORE-SHAPED PROSE. `conversation_guardrails.contains_forbidden_number` is
   the product's one implementation of "does this sentence state a number about
   the assessment", and it is reused here rather than reimplemented. What is
   added is the report-specific case that detector deliberately does not carry:
   a GRADE WORD sitting next to a digit. In interviewer speech a grade word is
   banned outright, so `contains_forbidden_number` never had to consider it; on
   a report the grade words are the whole vocabulary, and "Matching (82)" is the
   most likely shape a leak actually takes.

THE TWO EXEMPTIONS, BOTH NARROW, BOTH ARGUED
----------------------------------------------
RENDERING COORDINATES. `requirement_index` and `candidate_index` on a radar
axis. A radar chart has no geometry without a radius, and the four grades ARE
the axis, so this is the coarsest value that can draw the required chart. The
licence is narrow and it is enforced narrowly: the exemption applies only to
those two field names and only on a path that runs through `radar_charts`, so
the same field name elsewhere in the payload is still a violation. It is never
displayed as a number, which `tests/test_prism_report.py` asserts against the
rendered PDF text rather than against the payload.

VERBATIM CANDIDATE SUBMISSION. The `validation` subtree is the candidate's own
unrated application data, reproduced exactly as submitted, and it legitimately
carries numbers: a current CTC is an amount, a notice period is a count of days.
This is the same exemption, for the same reason, that `citations.KIND_VERBATIM`
gets from the citation requirement. It is not a claim the product makes about
the candidate; it is the candidate's own words carried across untouched, and a
product that reworded or withheld it would have falsified an application field
in a document a client decides from.
Inside that subtree rule 1 is relaxed and rule 2 takes over, so a Ready Pick
Score smuggled in under a key called `score` is still refused.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

from app.services import conversation_guardrails

__all__ = [
    "NumberInDeliveredReport",
    "NumberViolation",
    "RENDERING_COORDINATE_FIELDS",
    "RENDERING_COORDINATE_CONTAINER",
    "VERBATIM_ROOT",
    "RULE_NUMERIC_FIELD",
    "RULE_SCORE_KEY",
    "RULE_SCORE_PROSE",
    "scan",
    "assert_clean",
    "scan_text",
]


class NumberInDeliveredReport(ValueError):
    """A number reached a delivered PRISM Report.

    Raised, never logged and never stripped. Stripping would produce a document
    that reads as complete while a field the generator believed it was
    publishing has silently vanished, and the next reader would have no way to
    tell a redaction from an omission. The correct response to a score in a
    report is to stop and find out how it got there.
    """


#: The rendering-coordinate exemption, kept to exactly two field names.
RENDERING_COORDINATE_FIELDS: frozenset[str] = frozenset(
    {"requirement_index", "candidate_index"}
)
#: ...and to exactly one subtree. The same field name outside a chart is a
#: violation, because outside a chart there is no geometry to justify it.
RENDERING_COORDINATE_CONTAINER = "radar_charts"

#: The candidate's own unrated submission. See the module docstring.
VERBATIM_ROOT = "validation"

RULE_NUMERIC_FIELD = "numeric_field"
RULE_SCORE_KEY = "score_shaped_key"
RULE_SCORE_PROSE = "score_shaped_prose"

#: A key that names a score. Used ONLY where rule 1 is relaxed, so it does not
#: have to be exhaustive: it has to catch the shapes a triage number actually
#: arrives under.
_SCORE_KEY = re.compile(
    r"(?:^|_)(?:score|scores|scoring|band|bands|percent|percentage|percentile|"
    r"rating|ratings|rank|ranking|points|index|indices|weight|weights|"
    r"threshold|thresholds|composite|rps)(?:$|_)",
    re.IGNORECASE,
)

#: The four grades of `services/rating.py`, followed by a digit. The one shape
#: `contains_forbidden_number` cannot be asked to carry, because it exists to
#: guard interviewer speech, where a grade word is already banned on its own.
_GRADE_THEN_NUMBER = re.compile(
    r"\b(?:highly\s+matching|moderately\s+matching|not\s+matching|matching)\b"
    r"[^.!?\n]{0,12}?\d",
    re.IGNORECASE,
)

#: A bare percentage. Legitimate in interviewer speech ("99.99% uptime") and
#: never legitimate in a rated line of a delivered report, which states words.
_BARE_PERCENT = re.compile(r"\d{1,3}(?:\.\d+)?\s*(?:%|per\s?cent\b|percent\b)")

#: "8 out of 10", in a delivered report, with ANY denominator and whatever
#: follows it.
#:
#: `contains_forbidden_number` carries an out-of-N pattern already, and it
#: deliberately does not catch this. It guards INTERVIEWER SPEECH, where
#: "we migrated 7 out of 10 services" is ordinary technical content, so it
#: restricts the denominator to 5, 10 and 100 and refuses to fire when a word
#: follows. Both restrictions are right there and wrong here: measured on
#: 2026-09-03, "rates 8 out of 10 on the rubric" and "8 out of 10 for depth"
#: passed the whole ban, and those are the shapes a model actually writes when
#: it states a score in prose. A trailing word is what a REPORT sentence looks
#: like, not what distinguishes a count from a score.
#:
#: This is the third report-specific rule, added for the same reason as the two
#: above: a delivered report states WORDS, so any out-of-N in one is a score.
_OUT_OF_N = re.compile(r"\b\d{1,3}(?:\.\d+)?\s+out\s+of\s+\d{1,3}\b", re.IGNORECASE)


@dataclass(frozen=True)
class NumberViolation:
    """One number, and exactly where it was found.

    The path is the whole value of this type. "A number reached the report" is
    unactionable; "`gap_analysis.groups[0].items[1].score`" names the field
    somebody added and the change that has to be reverted.
    """

    path: str
    rule: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - exercised through the raise
        return f"{self.path}: {self.rule}: {self.detail}"


def _is_number(value: Any) -> bool:
    # `bool` is a subclass of `int`, and a boolean flag is not a score. Checked
    # first, because `isinstance(True, int)` is True and would otherwise make
    # every `immutable: true` in the product a number ban violation.
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float, Decimal))


def _exempt_coordinate(path_parts: Sequence[str], key: str) -> bool:
    return key in RENDERING_COORDINATE_FIELDS and (
        RENDERING_COORDINATE_CONTAINER in path_parts
    )


def scan_text(text: str, *, path: str = "text") -> list[NumberViolation]:
    """Rule 3, on one string. Public because an email body is a bare string.

    The three checks are OR'd rather than blended: each catches a different
    shape and a payload only has to break one of them to be refused.
    """
    if not text:
        return []
    found: list[NumberViolation] = []
    if conversation_guardrails.contains_forbidden_number(text):
        found.append(
            NumberViolation(
                path,
                RULE_SCORE_PROSE,
                "states a number about the assessment (score, rating, out-of-N, "
                "percentile, or a percentage bound to a matching verdict)",
            )
        )
    match = _GRADE_THEN_NUMBER.search(text)
    if match:
        found.append(
            NumberViolation(
                path,
                RULE_SCORE_PROSE,
                f"a grade word is followed by a digit: {match.group(0)!r}",
            )
        )
    percent = _BARE_PERCENT.search(text)
    if percent:
        found.append(
            NumberViolation(
                path,
                RULE_SCORE_PROSE,
                f"a percentage appears in a rated document: {percent.group(0)!r}",
            )
        )
    out_of = _OUT_OF_N.search(text)
    if out_of:
        found.append(
            NumberViolation(
                path,
                RULE_SCORE_PROSE,
                f"a score is stated out of a total: {out_of.group(0)!r}",
            )
        )
    return found


def _dump(payload: Any) -> Any:
    """Turn a model, a dataclass or a namespace into plain containers.

    Duck-typed rather than depending on pydantic here: this module is walked
    over payloads that arrive as response models, as dicts, as dataclasses and
    as already-serialised JSON, and a hard dependency on the model layer would
    make the ban unusable on the shapes that are not models.

    THE NAMESPACE CASE IS NOT COSMETIC. Without it a `SimpleNamespace` fell
    through to the opaque-object branch, which scans `str(value)` -- so the
    walker read the object's REPR as prose, matched a number inside a field it
    had never descended into, and reported a violation whose path was the whole
    payload. A walker that cannot descend into a container does not fail open,
    but it does fail uselessly.
    """
    dumper = getattr(payload, "model_dump", None)
    if callable(dumper):
        return dumper()
    if dataclasses.is_dataclass(payload) and not isinstance(payload, type):
        return dataclasses.asdict(payload)
    if isinstance(payload, SimpleNamespace):
        return vars(payload)
    return payload


def scan(payload: Any, *, path: str = "payload") -> list[NumberViolation]:
    """Every number in a delivered payload, with the path that reached it.

    Walks EVERY field rather than a list of known ones. A ban that enumerated
    the fields it checked would pass the day somebody added a field, which is
    the only day it matters.
    """
    return _walk(_dump(payload), path=path, parts=())


def _walk(
    value: Any, *, path: str, parts: tuple[str, ...]
) -> list[NumberViolation]:
    verbatim = bool(parts) and parts[0] == VERBATIM_ROOT
    found: list[NumberViolation] = []

    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            found.extend(
                _walk(
                    _dump(child),
                    path=f"{path}.{name}",
                    parts=parts + (name,),
                )
            )
        return found

    if isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value):
            found.extend(
                _walk(
                    _dump(child),
                    path=f"{path}[{index}]",
                    parts=parts,
                )
            )
        return found

    key = parts[-1] if parts else ""

    if _is_number(value):
        if _exempt_coordinate(parts, key):
            return found
        if verbatim:
            # Rule 1 is relaxed here and rule 2 takes over. A candidate's own
            # notice period is a number they typed; a field called `score` is
            # not, wherever it was put.
            if _SCORE_KEY.search(key):
                found.append(
                    NumberViolation(
                        path,
                        RULE_SCORE_KEY,
                        f"a numeric value under a score-shaped key ({key!r}) "
                        "inside the candidate's own submission",
                    )
                )
            return found
        found.append(
            NumberViolation(
                path,
                RULE_NUMERIC_FIELD,
                f"a numeric field ({value!r}) in a delivered report; grades are "
                "words and the Ready Pick Score is a dashboard artifact",
            )
        )
        return found

    if isinstance(value, str):
        if verbatim:
            # Not scanned. This is the candidate's own answer, and refusing to
            # deliver a report because an applicant wrote "I scored 82 in my
            # entrance exam" would withhold the document over the one section
            # the product promises to reproduce untouched.
            return found
        found.extend(scan_text(value, path=path))
        return found

    if isinstance(value, (datetime, date)) or value is None:
        return found

    # Anything else (UUID, Enum, an opaque object) is stringified for the prose
    # rule only. A type this walker does not understand must not be a hole.
    if not verbatim:
        found.extend(scan_text(str(value), path=path))
    return found


def assert_clean(payload: Any, *, where: str) -> None:
    """Refuse to deliver a payload carrying a number. THE CHOKEPOINT.

    `where` names the export format, because the same report can be clean as
    JSON and dirty as an email body, and a failure that did not say which one
    sends the reader to the wrong file.
    """
    violations = scan(payload, path=where)
    if violations:
        listed = "; ".join(str(violation) for violation in violations[:8])
        raise NumberInDeliveredReport(
            f"{len(violations)} number(s) reached the delivered PRISM Report "
            f"via {where}: {listed}"
        )


def assert_text_clean(text: str, *, where: str) -> None:
    """Rule 3 alone, for an export format that is a bare string."""
    violations = scan_text(text, path=where)
    if violations:
        listed = "; ".join(str(violation) for violation in violations)
        raise NumberInDeliveredReport(
            f"a number reached the delivered PRISM Report via {where}: {listed}"
        )


def known_paths(payload: Any) -> list[str]:
    """Every leaf path in a payload, for a test that wants to prove coverage.

    A number ban is only as good as the set of fields it visited, and a test
    asserting "no violations" cannot tell a clean payload from a walker that
    stopped at the first level. This returns what was actually reached.
    """
    out: list[str] = []
    _collect(_dump(payload), "payload", out)
    return out


def _collect(value: Any, path: str, out: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _collect(_dump(child), f"{path}.{key}", out)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _collect(_dump(child), f"{path}[{index}]", out)
        return
    out.append(path)


def _at(payload: Any, path: str) -> Any:
    current = _dump(payload)
    for part in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", path[len("payload"):]):
        key, index = part
        if key:
            current = _dump(current[key] if isinstance(current, Mapping) else getattr(current, key))
        else:
            current = _dump(current[int(index)])
    return current
