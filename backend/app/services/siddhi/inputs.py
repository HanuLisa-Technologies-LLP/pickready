"""Siddhi's inputs, built from what the pipeline already read. Pure: no session, no model.

`evidence_by_item` moved here from `functional_assessment._evidence_by_item`
(PLAN-p5 section 1.2) and changed in one way that is the reason it moved: each
exchange now carries the ADDRESSES of the rows it was assembled from, the
`candidate_questions` id and the `assessment_messages` ids, so the citation
trail Siddhi writes can say which message a statement rests on rather than
"the first exchange filed under this item". The text it carries is unchanged.

The other pure Siddhi-input helpers in `functional_assessment` (confidence,
validation points, portable nodes, claim records) move with the grading
phase's orchestrator rewrite, so the orchestrator is split once rather than
twice.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

__all__ = ["ANSWER_CHARS", "evidence_by_item"]

#: How much of one question's answers reaches a prompt or the support check.
#: Unchanged from the value the orchestrator used.
ANSWER_CHARS = 1500


def evidence_by_item(
    *,
    questions: Iterable[Any],
    competencies: Iterable[Any],
    answers: Mapping[str, Sequence[str]],
    answer_records: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """What each skill was actually asked, what was answered, and where it lives.

    `{skill name: [{"question", "answer", "question_id", "message_ids"}]}`,
    keyed by NAME because that is what survives onto the immutable report row.
    A question with no answer contributes nothing: the item still gets its
    `searched` node from the rated line-up, which is the honest citation for
    a gap.

    `answer_records` is `assessment_pipeline.evidence.answer_records(...)`:
    `{question_key: [AnswerRecord]}`. When it is absent the exchanges carry no
    message ids and the trail's answer nodes carry no locator, which is a
    weaker trail and never a wrong one.
    """
    by_id = {str(competency.id): competency.name for competency in competencies}
    located = answer_records or {}
    evidence: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        name = by_id.get(str(question.competency_id))
        if not name:
            continue
        key = str(question.id)
        answer = " ".join(answers.get(key, [])).strip()
        if not answer:
            continue
        evidence.setdefault(name, []).append(
            {
                "question": question.prompt,
                "answer": answer[:ANSWER_CHARS],
                "question_id": key,
                "message_ids": [
                    str(record.message_id) for record in located.get(key, ())
                ],
            }
        )
    return evidence
