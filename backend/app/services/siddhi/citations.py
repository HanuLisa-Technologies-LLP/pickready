"""Citation enforcement, in CODE rather than in a prompt (spec-doc5 §A.3).

    "implement the hard constraint from Runbook §57.6 in code, not in a prompt
     -- the explanation generator must be architecturally prevented from
     emitting a statement that doesn't carry a citation to an evidence node.
     'Architecturally prevented' means a structural check the generator cannot
     bypass, not an instruction asking it nicely."

WHAT "ARCHITECTURALLY PREVENTED" MEANS HERE
---------------------------------------------
There is no path from this module to a delivered report that does not go through
`Section.render`, and `Section.render` raises `UncitedStatement` on any statement
whose `evidence_refs` are empty or whose refs are not in the evaluation's known
evidence set. There is no `force` argument, no `strict=False`, and no
`allow_uncited` flag. A caller that has a statement it cannot cite has exactly
two options: cite it, or drop it.

That is a deliberate absence, and it is the same technique used everywhere else
this codebase enforces something structurally: `ContradictionReport.settle` has
no `force`, `EvaluatorInput` has no candidate field, and an agent's reach is the
absence of a tool rather than a refusal inside one. A bypass parameter is a
bypass that will be used, and the first use will be in a hotfix at the end of a
release.

WHY A PROMPT INSTRUCTION IS NOT ENOUGH
----------------------------------------
Because it fails silently and it fails most under load. Ask a model to cite and
it will, most of the time. The times it does not are the times the prompt was
long, the evidence was thin, or the provider was degraded -- which is to say,
exactly the reports where an uncited claim is most likely to be wrong. An
instruction produces a report that is usually cited; a structural check produces
one that is always cited or is not produced.

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
    "Statement",
    "Section",
    "Report",
    "check",
]


class UncitedStatement(ValueError):
    """A statement about the candidate that cites no evidence node.

    Raised at RENDER, not logged at write. A logged violation is a violation
    that ships.
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


@dataclass(frozen=True)
class Statement:
    """One assertion in a report, with the evidence it rests on.

    Validated at CONSTRUCTION for its kind and at RENDER for its citations. The
    split matters: an unknown kind is a programming error and should fail where
    it is written, while a missing citation is a generation outcome and should
    fail where the report is assembled -- which is the last point at which the
    whole evidence set is known.
    """

    kind: str
    text: str
    evidence_refs: tuple[str, ...] = ()

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass
class Section:
    """One report section. Renders only if every statement in it is citable."""

    key: str
    title: str
    statements: list[Statement] = field(default_factory=list)

    def add(self, statement: Statement) -> "Section":
        self.statements.append(statement)
        return self

    def render(self, known_refs: Iterable[str]) -> list[dict[str, Any]]:
        """THE CHOKEPOINT. There is no other way to get text out of a Section.

        No `force`, no `strict=False`, no `allow_uncited`. A caller holding a
        statement it cannot cite must cite it or drop it.
        """
        known = set(known_refs)
        rendered: list[dict[str, Any]] = []
        for statement in self.statements:
            if statement.needs_citation:
                if not statement.evidence_refs:
                    raise UncitedStatement(
                        f"{self.key}: a {statement.kind} statement carries no "
                        f"evidence citation. Every delivered statement about a "
                        f"candidate must trace to an evidence node. "
                        f"Statement: {statement.text[:80]!r}"
                    )
                unknown = [ref for ref in statement.evidence_refs if ref not in known]
                if unknown:
                    raise UnknownEvidence(
                        f"{self.key}: a {statement.kind} statement cites "
                        f"{unknown} which is not in this evaluation's evidence "
                        f"set. A fabricated citation is worse than none -- it "
                        f"reads as provenance."
                    )
            rendered.append(statement.as_dict())
        return rendered


@dataclass
class Report:
    """A whole report, assembled section by section.

    `known_refs` is the evaluation's complete evidence set --
    `Aggregate.evidence_refs`. It is passed in at CONSTRUCTION rather than at
    render, so a caller cannot widen the accepted set per section to get one
    statement through.
    """

    known_refs: frozenset[str]
    sections: list[Section] = field(default_factory=list)

    def section(self, key: str, title: str) -> Section:
        section = Section(key=key, title=title)
        self.sections.append(section)
        return section

    def render(self) -> list[dict[str, Any]]:
        """Render every section, or raise on the first violation.

        FAILS FAST rather than collecting violations, because a report that
        rendered its clean sections and dropped the rest would be a report with
        holes in it that reads as complete. The whole thing renders or none of
        it does, and the caller's own degradation path decides what to do.
        """
        return [
            {
                "key": section.key,
                "title": section.title,
                "statements": section.render(self.known_refs),
            }
            for section in self.sections
        ]

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
                if not statement.needs_citation:
                    continue
                if not statement.evidence_refs:
                    found.append(
                        {
                            "section": section.key,
                            "kind": statement.kind,
                            "problem": "no_citation",
                            "text": statement.text[:120],
                        }
                    )
                    continue
                unknown = [
                    ref for ref in statement.evidence_refs if ref not in self.known_refs
                ]
                if unknown:
                    found.append(
                        {
                            "section": section.key,
                            "kind": statement.kind,
                            "problem": "unknown_evidence",
                            "refs": unknown,
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
