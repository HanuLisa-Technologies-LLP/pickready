"""The PRISM Report's Leadership Alignment section (owner spec 22.9).

WHAT IT SAYS, AND WHERE EACH STATEMENT COMES FROM
-------------------------------------------------
Five groups, words only, composed DETERMINISTICALLY (no model) and every
statement passed through the citation chokepoint like every other section:

* Company-wide expectations, and Department expectations: the lines of the
  job's FROZEN leadership context (the one the contract names), each prefixed
  with whose expectation it is. A REQUIREMENT SOURCE, not a finding about the
  candidate: each cites its own `leadership` evidence node, whose locator is
  the frozen `job_leadership_contexts` row.
* Evidence demonstrated / Evidence not demonstrated: every skill the hiring
  team saved FROM a leadership line, with Miti's grade WORD for it, citing the
  candidate's own answers (the item's grounding) or, for a gap, the record
  that it was assessed. Miti decided the grade; this section never recomputes
  one, and a leadership expectation moves no grade (rule 37.10).
* Follow-up questions: the Gap Analysis's own grounded probes for those
  not-demonstrated skills, REUSED, so the report never carries two differently
  worded probes for one gap.

What a reader sees is what RENDERED: `stored_section` builds the stored JSON
from the rendered statements only, so a withheld statement is never stored,
anywhere. No context on the contract, no section: an older report and a job
with no leadership input render exactly as before.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.services.siddhi.evidence import KIND_LEADERSHIP, EvidenceNode

__all__ = [
    "GROUP_COMPANY",
    "GROUP_DEMONSTRATED",
    "GROUP_DEPARTMENT",
    "GROUP_FOLLOW_UP",
    "GROUP_NOT_DEMONSTRATED",
    "GROUP_TITLES",
    "NOTE",
    "NO_LEADERSHIP_SKILLS",
    "SECTION_KEY",
    "TITLE",
    "build_payload",
    "nodes",
    "stored_section",
]

SECTION_KEY = "leadership_alignment"
TITLE = "Leadership Alignment"

GROUP_COMPANY = "company"
GROUP_DEPARTMENT = "department"
GROUP_DEMONSTRATED = "demonstrated"
GROUP_NOT_DEMONSTRATED = "not_demonstrated"
GROUP_FOLLOW_UP = "follow_up"
GROUP_TITLES: dict[str, str] = {
    GROUP_COMPANY: "Company-wide expectations",
    GROUP_DEPARTMENT: "Department expectations",
    GROUP_DEMONSTRATED: "Evidence demonstrated",
    GROUP_NOT_DEMONSTRATED: "Evidence not demonstrated",
    GROUP_FOLLOW_UP: "Follow-up questions",
}

NOTE = (
    "What the company's leaders said they need from hires, set beside the "
    "evidence this candidate gave. The expectations are requirements, not "
    "findings about the candidate, and the grades are the assessment's own."
)
NO_LEADERSHIP_SKILLS = (
    "No skill on this job was drawn from a leadership expectation, so the "
    "skills above carry the whole comparison."
)

#: The four grade words that count as demonstrated evidence. Restated from
#: `gap_analysis.GAP_GRADES`' complement so this module imports no pipeline code.
DEMONSTRATED_GRADES = frozenset({"Highly Matching", "Matching"})


def node_ref(line_ref: str) -> str:
    return f"{KIND_LEADERSHIP}:{line_ref}"


def nodes(context_id: str, lines: Sequence[Mapping[str, Any]]) -> tuple[EvidenceNode, ...]:
    """One citable node per frozen leadership line. The locator is the frozen
    row plus the line's ref: an address, never the text."""
    return tuple(
        EvidenceNode(
            ref=node_ref(str(line["ref"])),
            kind=KIND_LEADERSHIP,
            item=f"leadership:{line['ref']}",
            locators=(f"job_leadership_contexts:{context_id}#{line['ref']}",),
        )
        for line in lines
    )


def build_payload(
    *,
    lines: Sequence[Mapping[str, Any]],
    source_labels: Mapping[str, str],
    leadership_skills: Sequence[Mapping[str, Any]],
    probes_by_item: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """The plain-data plan `synthesis` composes the section from. Pure.

    `lines`: the frozen context's lines (ref, source, scope, text).
    `leadership_skills`: {name, source, grade} for every contract skill whose
    saved source is a leadership source, grade being Miti's WORD.
    """
    expectations = [
        {
            "group": GROUP_DEPARTMENT if line.get("scope") == "department" else GROUP_COMPANY,
            "text": f"{source_labels.get(str(line['source']), 'Leadership')}: {line['text']}",
            "ref": node_ref(str(line["ref"])),
        }
        for line in lines
    ]
    skills = []
    for skill in leadership_skills:
        grade = str(skill.get("grade") or "")
        label = source_labels.get(str(skill.get("source")), "Leadership")
        skills.append(
            {
                "name": str(skill["name"]),
                "label": label,
                "grade": grade,
                "demonstrated": grade in DEMONSTRATED_GRADES,
                "probes": [
                    probe for probe in probes_by_item.get(str(skill["name"]), ()) if probe
                ],
            }
        )
    return {"expectations": expectations, "skills": skills}


def stored_section(
    sections: Iterable[Mapping[str, Any]],
    *,
    context_id: str,
    context_version: int,
    digest: str,
) -> dict[str, Any] | None:
    """The stored JSON, built from the RENDERED statements of this section only.

    A withheld statement never reached `sections`, so it can never be stored.
    None when the section was not composed.
    """
    rendered = next((section for section in sections if section.get("key") == SECTION_KEY), None)
    if rendered is None:
        return None
    titles = set(GROUP_TITLES.values())
    note = ""
    groups: list[dict[str, Any]] = []
    for statement in rendered.get("statements") or []:
        kind = statement.get("kind")
        text = str(statement.get("text") or "")
        if kind == "heading":
            if text in titles:
                groups.append({"title": text, "lines": []})
            continue
        if kind == "connective" and not groups:
            note = text
            continue
        if not groups:
            continue
        groups[-1]["lines"].append({"kind": kind, "text": text})
    return {
        "context_id": context_id,
        "context_version": context_version,
        "context_digest": digest,
        "note": note,
        "groups": [group for group in groups if group["lines"]],
    }
