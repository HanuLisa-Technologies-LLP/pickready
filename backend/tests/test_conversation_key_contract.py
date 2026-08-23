"""The conversation must stamp the key the scorer reads.

WHY THIS NEEDED PINNING. Nothing asserted it, and the documentation had already
drifted: `models/assessment.CandidateQuestion` stated that the conversation
stamps `question_key = str(CandidateQuestion.competency_id)`, while the code on
both sides used the QUESTION's own id. The two sides happened to agree, so the
product worked and the sentence stayed wrong for as long as anyone cared to
read it.

The failure it would cause is silent and total. `answers_by_key` groups every
candidate answer by `question_key`; `_score_item` fetches an item's answers with
`answers.get(str(question.id))`. If one side ever moved, every lookup would miss,
every item would score as unanswered, and every candidate on the job would grade
Not Matching -- with no error anywhere, because "no answer" is a legitimate state
the scorer already handles.
"""
import inspect
import re

from app.api import assessments
from app.services import functional_assessment


def test_the_conversation_stamps_the_questions_own_id():
    """`_conversation_prompts` builds the (domain, key, prompt) triple, and the
    middle element becomes `question_key` on the candidate's message."""
    source = inspect.getsource(assessments._conversation_prompts)
    assert re.search(r"str\(question\.id\)", source), source[-400:]
    assert not re.search(r"str\(question\.competency_id\)", source)


def test_the_scorer_looks_answers_up_by_that_same_id():
    source = inspect.getsource(functional_assessment._score_item)
    assert re.search(r"answers\.get\(\s*str\(question\.id\)", source)


def test_dimension_coverage_joins_on_the_same_id():
    """The stopping rule counts covered matrix items by joining messages back to
    `candidate_questions`. Joining on the competency id instead would match
    nothing, `conversation_may_close` would always answer False, and the whole
    dynamic-length feature would be silently inert -- passing every test,
    because never closing early is also the safe direction."""
    source = inspect.getsource(assessments._dimension_coverage)
    assert "m.question_key = CAST(q.id AS text)" in source
    # And it must count DISTINCT competencies, not questions: several questions
    # can probe one matrix item, and counting questions would let a third of the
    # matrix look like full coverage.
    assert "COUNT(DISTINCT q.competency_id)" in source


def test_only_a_substantive_answer_counts_as_coverage():
    """The dangerous direction. If a non-answer counted, a candidate could end
    their own assessment early by not answering."""
    source = inspect.getsource(assessments._dimension_coverage)
    assert "answer_label" in source
    assert "'substantive'" in source


def test_a_missing_label_is_treated_as_substantive():
    """Every degradation path in `answer_classification` returns "substantive"
    by design, so a NULL label means the classifier was unavailable. Withholding
    coverage there would let a provider outage silently lengthen assessments."""
    source = inspect.getsource(assessments._dimension_coverage)
    assert "COALESCE(m.answer_label, 'substantive')" in source
