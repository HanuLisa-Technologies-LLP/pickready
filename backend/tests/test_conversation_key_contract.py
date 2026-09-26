"""The conversation must stamp the key the scorer reads.

WHY THIS NEEDED PINNING. Nothing asserted it, and the documentation had already
drifted: `models/assessment.CandidateQuestion` stated that the conversation
stamps `question_key = str(CandidateQuestion.competency_id)`, while the code on
both sides used the QUESTION's own id. The two sides happened to agree, so the
product worked and the sentence stayed wrong for as long as anyone cared to
read it.

The failure it would cause is silent and total. `answers_by_key` groups every
candidate answer by `question_key`; Miti's item stage fetches a skill's answers with
`answers.get(str(question.id))`. If one side ever moved, every lookup would miss,
every item would score as unanswered, and every candidate on the job would grade
Not Matching -- with no error anywhere, because "no answer" is a legitimate state
the scorer already handles.

Since 2026-09-24 the key is stamped by the conversation ENGINE
(`services/assessment_conversation/turns`). The coverage read the operator's
state line uses joins on the same id; it decides nothing any more (every item
is asked), but a join on the wrong id would still make that line lie.
"""
import inspect
import re

from app.services.assessment_conversation import turns
from app.services.miti import items as miti_items


def test_the_conversation_stamps_the_questions_own_id():
    """`conversation_prompts` builds the (aspect, key, text, row) tuples, and
    the key becomes `question_key` on the candidate's message."""
    source = inspect.getsource(turns.conversation_prompts)
    assert re.search(r"str\(question\.id\)", source), source[-400:]
    assert not re.search(r"str\(question\.competency_id\)", source)


def test_the_scorer_looks_answers_up_by_that_same_id():
    """Miti's item stage is the scorer since WP5-B; both of its methods key an
    answer by the QUESTION's own id."""
    for method in (miti_items._rubric_scored, miti_items._behavioural):
        source = inspect.getsource(method)
        assert re.search(r"key = str\(question\.id\)", source), method.__name__
        assert "answers.get(key" in source, method.__name__


def test_the_coverage_read_joins_on_the_same_id_and_groups_by_skill():
    """Several questions can probe one skill, and a follow-up is filed under its
    parent's key; grouping by question would report a third of the skills as
    the whole of them."""
    source = inspect.getsource(turns.coverage_rows)
    assert "m.question_key = CAST(q.id AS text)" in source
    assert "GROUP BY c.id" in source


def test_a_missing_label_counts_as_substantive():
    """Every degradation path in `answer_classification` returns "substantive"
    by design, so a NULL label means the classifier was unavailable."""
    source = inspect.getsource(turns.coverage_rows)
    assert "COALESCE(m.answer_label, 'substantive') = 'substantive'" in source
