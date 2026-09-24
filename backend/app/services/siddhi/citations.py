"""Citation enforcement, in CODE rather than in a prompt (spec-doc5 §A.3).

    "implement the hard constraint from Runbook §57.6 in code, not in a prompt
     -- the explanation generator must be architecturally prevented from
     emitting a statement that doesn't carry a citation to an evidence node.
     'Architecturally prevented' means a structural check the generator cannot
     bypass, not an instruction asking it nicely."

WHAT "ARCHITECTURALLY PREVENTED" MEANS HERE
---------------------------------------------
There is no path from this module to delivered text that renders an uncited
statement AS CITED. `Statement.problem` is the one rule, and exactly two
methods read it:

  * `Section.render` / `Report.render` RAISE on the first violation
    (`UncitedStatement`, or `UnknownEvidence` for a fabricated ref). Tests and
    the standalone `check` use this mode.
  * `Report.render_collect` WITHHOLDS every violating statement: it is not
    rendered at all, and it comes back as a `Withheld` record (section, kind,
    item, problem code; never the prose). This is the delivered path since the
    Vivekium release, and the reason is below.

There is no `force`, no `strict=False` and no `allow_uncited` flag on either. A
caller holding a statement it cannot cite has exactly two outcomes: it is cited,
or it does not reach a reader.

WHY THE LIVE PATH WITHHOLDS RATHER THAN RAISES (Vivekium release, P5-D8)
--------------------------------------------------------------------------
Until this release the only mode was the raising one, and the live path used
it. So one uncited statement anywhere in a report failed the whole scoring task,
the task retried twice and then nothing was written: a candidate who had
finished the assessment got no report at all, and nobody was told why except a
traceback. That is a silent failure with a loud log line. The rule the raise
protected is "nothing uncited is ever DELIVERED", and withholding keeps it
exactly: the statement is absent from the report and from the citation trail,
the report is written flagged for human review with an `uncited_statement`
finding, and `siddhi.report` logs `prism.statement_withheld_for_review` at
ERROR, which a CloudWatch metric filter and alarm watch.

WHY A PROMPT INSTRUCTION IS NOT ENOUGH
----------------------------------------
Because it fails silently and it fails most under load. Ask a model to cite and
it will, most of the time. The times it does not are the times the prompt was
long, the evidence was thin, or the provider was degraded -- which is to say,
exactly the reports where an uncited claim is most likely to be wrong. An
instruction produces a report that is usually cited; a structural check produces
one whose delivered statements are always cited.

WHAT IS AND IS NOT A STATEMENT
--------------------------------
Only CLAIMS ABOUT THE CANDIDATE need citations. A section heading, a piece of
connective prose ("Across the four areas assessed:"), a restatement of the job's
own requirements, and the Validation section -- which is the candidate's own
unrated submission reproduced exactly as submitted -- are not claims about the
candidate derived from evidence, and requiring citations on them would either
produce fake citations or produce a report that is unreadable.

`STATEMENT_KINDS` is the closed list, and `Statement` refuses an unknown kind.
A new kind of thing appearing in a report is a decision about what the report
asserts, and it should cost a reviewed line rather than defaulting into the
exempt bucket.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = [
    "UncitedStatement",
    "UnknownEvidence",
    "KIND_FINDING",
    "KIND_GRADE",
    "KIND_GAP",
    "KIND_PROBE",
    "KIND_HEADING",
    "KIND_CONNECTIVE",
    "KIND_VERBATIM",
    "STATEMENT_KINDS",
    "REQUIRES_CITATION",
    "PROBLEM_NO_CITATION",
    "PROBLEM_UNKNOWN_EVIDENCE",
    "Statement",
    "Section",
    "Report",
    "Withheld",
    "check",
]


class UncitedStatement(ValueError):
    """A statement about the candidate that cites no evidence node.

    Raised by the RAISING render mode. A logged violation is a violation that
    ships, which is why the collecting mode does not log and render: it
    withholds.
    """


class UnknownEvidence(ValueError):
    """A statement citing a ref that is not in the evaluation's evidence set.

    A distinct error from `UncitedStatement` on purpose, because it means
    something different and worse: an empty citation list is a generator that
    forgot, while an unknown ref is a generator that INVENTED one -- and a
    fabricated citation is more dangerous than no citation, because it reads as
    provenance.
    """


# ── Statement kinds ──────────────────────────────────────────────────────────

KIND_FINDING = "finding"        # "They have run a migration end to end."
KIND_GRADE = "grade"            # "Must-have: Matching"
KIND_GAP = "gap"                # "No evidence of on-call ownership."
KIND_PROBE = "probe"            # "Ask how they decided to roll back."
KIND_HEADING = "heading"        # "Gap Analysis & Action Plan"
KIND_CONNECTIVE = "connective"  # "Across the four areas assessed:"
KIND_VERBATIM = "verbatim"      # the Validation section, exactly as submitted

STATEMENT_KINDS: frozenset[str] = frozenset(
    {
        KIND_FINDING,
        KIND_GRADE,
        KIND_GAP,
        KIND_PROBE,
        KIND_HEADING,
        KIND_CONNECTIVE,
        KIND_VERBATIM,
    }
)

#: Which kinds are claims about the candidate and therefore need a citation.
#:
#: KIND_GAP IS IN THIS SET, and it is the entry worth arguing for. "There is no
#: evidence of X" feels like it cannot be cited -- there is nothing to point at.
#: It can and must be: the citation is the evidence that was SEARCHED, which is
#: what distinguishes "we looked at their answers on this competency and none of
#: them addressed it" from "we never asked". The first is a finding; the second
#: is a gap in the assessment being reported as a gap in the candidate, which is
#: the specific injustice this rule prevents.
#:
#: KIND_PROBE is in it too, because spec-doc5 requires every gap probe to be
#: "grounded in the candidate's actual answer" rather than generic advice, and a
#: probe with no citation is generic advice by definition.
REQUIRES_CITATION: frozenset[str] = frozenset(
    {KIND_FINDING, KIND_GRADE, KIND_GAP, KIND_PROBE}
)

#: The two reasons a statement is refused, as stable codes. A finding stores
#: the code and never the sentence: `review_findings_json` is read from far
#: more places than the report is.
PROBLEM_NO_CITATION = "no_citation"
PROBLEM_UNKNOWN_EVIDENCE = "unknown_evidence"


@dataclass(frozen=True)
class Statement:
    """One assertion in a report, with the evidence it rests on.

    Validated at CONSTRUCTION for its kind and at RENDER for its citations. The
    split matters: an unknown kind is a programming error and should fail where
    it is written, while a missing citation is a generation outcome and is
    decided where the report is assembled -- which is the last point at which
    the whole evidence set is known.
    """

    kind: str
    text: str
    evidence_refs: tuple[str, ...] = ()
    #: What the statement is ABOUT: a rated item's name, an aspect key, or ""
    #: for a statement about the assessment as a whole. A LABEL, never prose:
    #: it is what a withheld statement is reported under, so a reviewer can
    #: find the line that was held back without the finding quoting it.
    item: str = ""

    def __post_init__(self) -> None:
        if self.kind not in STATEMENT_KINDS:
            raise ValueError(
                f"Unknown statement kind {self.kind!r}. A new kind of thing in a "
                f"report is a decision about what the report asserts; it must be "
                f"added to STATEMENT_KINDS deliberately rather than defaulting "
                f"into the exempt bucket."
            )

    @property
    def needs_citation(self) -> bool:
        return self.kind in REQUIRES_CITATION

    def problem(self, known: Iterable[str]) -> str | None:
        """Why this statement may not be rendered, or None when it may.

        THE ONE RULE, stated once. The raising mode and the collecting mode
        both read it, so they cannot disagree about what counts as cited.
        """
        if not self.needs_citation:
            return None
        if not self.evidence_refs:
            return PROBLEM_NO_CITATION
        accepted = known if isinstance(known, (set, frozenset)) else set(known)
        if any(ref not in accepted for ref in self.evidence_refs):
            return PROBLEM_UNKNOWN_EVIDENCE
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "evidence_refs": list(self.evidence_refs),
            "item": self.item,
        }


@dataclass(frozen=True)
class Withheld:
    """A statement `render_collect` refused to render, described WITHOUT its prose.

    Section, kind, item and the problem code. Never the text: the whole point
    of withholding is that the sentence reaches nobody, and a finding that
    quoted it would deliver it by another door.
    """

    section: str
    kind: str
    item: str
    problem: str

    def as_dict(self) -> dict[str, str]:
        return {
            "section": self.section,
            "kind": self.kind,
            "item": self.item,
            "problem": self.problem,
        }


@dataclass
class Section:
    """One report section. Renders only statements that are citable."""

    key: str
    title: str
    statements: list[Statement] = field(default_factory=list)

    def add(self, statement: Statement) -> "Section":
        self.statements.append(statement)
        return self

    def render(self, known_refs: Iterable[str]) -> list[dict[str, Any]]:
        """THE RAISING CHOKEPOINT. Raises on the first statement that is not citable.

        No `force`, no `strict=False`, no `allow_uncited`. A caller holding a
        statement it cannot cite must cite it or drop it.
        """
        known = frozenset(known_refs)
        rendered: list[dict[str, Any]] = []
        for statement in self.statements:
            problem = statement.problem(known)
            if problem == PROBLEM_NO_CITATION:
                raise UncitedStatement(
                    f"{self.key}: a {statement.kind} statement carries no "
                    f"evidence citation. Every delivered statement about a "
                    f"candidate must trace to an evidence node. "
                    f"Statement: {statement.text[:80]!r}"
                )
            if problem == PROBLEM_UNKNOWN_EVIDENCE:
                unknown = [ref for ref in statement.evidence_refs if ref not in known]
                raise UnknownEvidence(
                    f"{self.key}: a {statement.kind} statement cites "
                    f"{unknown} which is not in this evaluation's evidence "
                    f"set. A fabricated citation is worse than none, because "
                    f"it reads as provenance."
                )
            rendered.append(statement.as_dict())
        return rendered

    def _collect(
        self, known: frozenset[str]
    ) -> tuple[list[dict[str, Any]], list[Withheld]]:
        rendered: list[dict[str, Any]] = []
        withheld: list[Withheld] = []
        for statement in self.statements:
            problem = statement.problem(known)
            if problem is None:
                rendered.append(statement.as_dict())
                continue
            withheld.append(
                Withheld(
                    section=self.key,
                    kind=statement.kind,
                    item=statement.item,
                    problem=problem,
                )
            )
        return rendered, withheld


@dataclass
class Report:
    """A whole report, assembled section by section.

    `known_refs` is the evaluation's complete evidence set. It is passed in at
    CONSTRUCTION rather than at render, so a caller cannot widen the accepted
    set per section to get one statement through.
    """

    known_refs: frozenset[str]
    sections: list[Section] = field(default_factory=list)

    def section(self, key: str, title: str) -> Section:
        section = Section(key=key, title=title)
        self.sections.append(section)
        return section

    def render(self) -> list[dict[str, Any]]:
        """Render every section, or raise on the first violation.

        FAILS FAST rather than collecting, for the callers that want the
        strict answer (the tests, `check`, the worked example). The delivered
        path uses `render_collect`, which withholds instead.
        """
        return [
            {
                "key": section.key,
                "title": section.title,
                "statements": section.render(self.known_refs),
            }
            for section in self.sections
        ]

    def render_collect(self) -> tuple[list[dict[str, Any]], list[Withheld]]:
        """Render every CITED statement and WITHHOLD every other one.

        A withheld statement is not rendered in any form: it is absent from the
        returned sections and therefore from the citation trail built from
        them. What comes back instead is a `Withheld` record carrying no prose,
        and the caller is the one that routes the report to human review. There
        is no way through this method to render an uncited statement as cited,
        which is the property the raising mode existed to protect.

        A section whose every statement was withheld still appears, empty, so
        the section order a renderer walks is unchanged and the absence is
        visible rather than a missing key.
        """
        sections: list[dict[str, Any]] = []
        withheld: list[Withheld] = []
        for section in self.sections:
            rendered, held = section._collect(self.known_refs)
            sections.append(
                {"key": section.key, "title": section.title, "statements": rendered}
            )
            withheld.extend(held)
        return sections, withheld

    def violations(self) -> list[dict[str, Any]]:
        """Every violation, without raising. For the loop's reflect stage.

        `agent_loop` feeds a rejection back to the generator VERBATIM as an
        instruction, and "you returned three statements with no citation, here
        they are" is a defect a model fixes when told. `render` is the gate;
        this is the feedback, and having both is what makes the gate productive
        rather than merely obstructive.
        """
        found: list[dict[str, Any]] = []
        for section in self.sections:
            for statement in section.statements:
                problem = statement.problem(self.known_refs)
                if problem == PROBLEM_NO_CITATION:
                    found.append(
                        {
                            "section": section.key,
                            "kind": statement.kind,
                            "problem": problem,
                            "text": statement.text[:120],
                        }
                    )
                elif problem == PROBLEM_UNKNOWN_EVIDENCE:
                    found.append(
                        {
                            "section": section.key,
                            "kind": statement.kind,
                            "problem": problem,
                            "refs": [
                                ref
                                for ref in statement.evidence_refs
                                if ref not in self.known_refs
                            ],
                            "text": statement.text[:120],
                        }
                    )
        return found


def check(
    statements: Sequence[Statement], known_refs: Iterable[str]
) -> None:
    """Standalone check, for a caller not assembling a whole `Report`.

    Raises exactly as `Section.render` does, so there is one rule and one error
    class rather than two implementations that must agree.
    """
    Section(key="check", title="check", statements=list(statements)).render(known_refs)
