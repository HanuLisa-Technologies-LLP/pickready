"""The activity copy. A FIXED catalogue in code, never a prompt.

PROVENANCE
----------
`ai-upgrade-spec-doc.md` "Case 3" sections 6, 8, 17 and 18, and the shape of
`services/candidate_updates.py`, which is this codebase's existing answer to
"user-facing copy that must never be generated".

WHY NO MODEL WRITES THESE
--------------------------
Case 3 section 6 forbids a separate model call per status update, and this
product already has the three reasons written down elsewhere:

  * a generated status can describe work that did not happen, which is worse
    than no status at all (`services/matching_progress`);
  * the prompts behind these workflows contain a real candidate's resume and a
    real client's job description, and a narration quotes its prompt;
  * the status must keep working when the provider is the thing that is broken,
    which is exactly when the status matters most.

So every sentence below is written here, once, and
`tests/test_ai_activity.py` sweeps the whole catalogue rather than checking a
call site: for digits, percentages, grade words, em dashes, product-name errors
and chain-of-thought vocabulary. A rule enforced at one call site is a rule the
next entry breaks.

THE KEY IS (TASK, EVENT), AND THAT IS THE WHOLE DESIGN
-------------------------------------------------------
Section 8 states the requirement as an example: `REQUIREMENTS_IDENTIFIED` means
"the key requirements of the role are known, now compare them with the
experience" during a matching run and "the criteria this candidate is graded
against are known" while a report is written. Same event, two sentences. Keying
on the event alone is how a system ends up with one sequence for every request.

TWO VARIANTS PER PHRASE, AND WHY THE DETAILED ONE IS CONDITIONAL
-----------------------------------------------------------------
`plain` needs nothing. `detailed` names facts in braces and is used ONLY when
the workflow supplied every one of them. That is section 18 (no false
precision) made structural rather than remembered: a count cannot reach a
sentence unless the pipeline computed it, because the sentence that mentions it
is unreachable otherwise.

VOICE
-----
Present participle, no first-person pronoun: "Reading the job description ...",
not "I'm reading ...". The spec's examples use "I'm"; this product's existing
recruiter-facing progress copy does not, and Vivekium is a platform rather than
an assistant persona. Task specificity is the requirement; the pronoun is not.
Sentences end in a full stop and carry no ellipsis, matching the copy already on
the job page.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.activity import events as ev


@dataclass(frozen=True)
class Phrase:
    """One catalogue entry.

    `detailed` is a `str.format` template naming facts. It is rendered only when
    every name it uses was supplied and validated; otherwise `plain` is used and
    nothing is invented to fill the gap.
    """

    plain: str
    detailed: str = ""


#: How an integer fact is spoken. A count reaches the reader as "12 candidates"
#: or "1 candidate", never as a bare number, and never as a percentage or an
#: estimate. These are operational counts of rows being processed, which the job
#: page already displays; none of them is a score, a rank or a band index, and
#: claude.md's no-numbers rule is about assessment output.
COUNT_NOUNS: dict[str, tuple[str, str]] = {
    "candidate_count": ("candidate", "candidates"),
    "criterion_count": ("saved criterion", "saved criteria"),
    "resume_count": ("resume", "resumes"),
    "answer_count": ("answer", "answers"),
    "gap_count": ("criterion", "criteria"),
}

#: Facts that are text rather than a count. Only values the user themselves
#: supplied or chose, so that the status can say "for the Product Manager role"
#: rather than "your document" (Case 3 section 9). Validated before use: a value
#: that is empty, over-long or carries a forbidden character drops the sentence
#: back to `plain` rather than being cleaned up quietly.
TEXT_FACTS: frozenset[str] = frozenset({"role_title", "candidate_reference"})

#: A text fact longer than this is a paste, not a title. Truncating one would
#: put half a sentence on screen, so an over-long value is refused instead.
MAX_TEXT_FACT_CHARS = 80


# ── The catalogue ────────────────────────────────────────────────────────────

_MATCHING = ev.TASK_JOB_CANDIDATE_MATCHING
_REPORT = ev.TASK_ASSESSMENT_REPORT

PHRASING: dict[tuple[str, str], Phrase] = {
    # ── AI Matching: Yukti reads every candidate on a job against that job's
    #    SAVED skills (`pickready.run_matching`). The plain variants are the
    #    sentences the job page shows for each pipeline stage, so this
    #    catalogue IS that copy rather than a second set of words for the
    #    same work.
    (_MATCHING, ev.TASK_STARTED): Phrase(
        plain="Reading the job description and the skills saved for this job.",
        detailed="Reading the job description for {role_title} and the skills saved for it.",
    ),
    (_MATCHING, ev.REQUIREMENTS_IDENTIFIED): Phrase(
        plain="Deciding which candidates to look at for the skills this job needs.",
    ),
    (_MATCHING, ev.INPUT_PREPARED): Phrase(
        plain="Turning the job description into the semantic form used for retrieval.",
    ),
    (_MATCHING, ev.DOCUMENT_PARSED): Phrase(
        plain="Making sure every linked resume is parsed and indexed before retrieval runs.",
        detailed="Making sure all {resume_count} linked to this job are parsed and indexed before retrieval runs.",
    ),
    (_MATCHING, ev.SEMANTIC_SEARCH_COMPLETED): Phrase(
        plain="Finding resumes that mean the same thing as the role, not just resumes that repeat its words.",
    ),
    (_MATCHING, ev.KEYWORD_SEARCH_COMPLETED): Phrase(
        plain="Matching the role's named skills and technologies against resume text.",
    ),
    (_MATCHING, ev.CANDIDATE_POOL_ASSEMBLED): Phrase(
        plain="Combining both searches, then adding every candidate linked to this job so retrieval never decides who gets checked.",
    ),
    (_MATCHING, ev.VALIDATION_CHECKED): Phrase(
        plain="Reading each candidate's application answers on pay, notice period and documents.",
    ),
    (_MATCHING, ev.SKILLS_COMPARED): Phrase(
        plain="Checking each resume against the skills saved for this job.",
        detailed="Checking {candidate_count} against the skills saved for this job.",
    ),
    (_MATCHING, ev.EVIDENCE_GROUNDED): Phrase(
        plain="Making sure every piece of evidence quoted really appears in the resume it came from.",
    ),
    (_MATCHING, ev.RESULTS_RECORDED): Phrase(
        plain="Recording each result against the candidate's application.",
    ),
    (_MATCHING, ev.STEP_UNAVAILABLE): Phrase(
        plain="One step of this run could not be completed, so the run continued without it.",
    ),
    (_MATCHING, ev.TASK_COMPLETED): Phrase(
        plain="Every candidate linked to this job has been checked against its saved skills.",
        detailed="All {candidate_count} linked to this job have been checked against its saved skills.",
    ),
    (_MATCHING, ev.TASK_FAILED): Phrase(
        plain="Vivekium could not finish checking the candidates for this job. Please try again.",
    ),
    (_MATCHING, ev.TASK_CANCELLED): Phrase(
        plain="This AI Matching run was stopped before it finished.",
    ),
    # ── Scoring one candidate's assessment and writing their PRISM Report
    #    (`pickready.run_functional_assessment`). Every shared event kind below
    #    reads differently from its matching twin, which is the property Case 3
    #    section 8 asks for.
    (_REPORT, ev.TASK_STARTED): Phrase(
        plain="Opening this candidate's completed assessment so it can be scored.",
        detailed="Opening the completed assessment for {role_title} so it can be scored.",
    ),
    (_REPORT, ev.DOCUMENT_PARSED): Phrase(
        plain="Reading the answers this candidate gave during the assessment.",
        detailed="Reading the {answer_count} this candidate gave during the assessment.",
    ),
    (_REPORT, ev.REQUIREMENTS_IDENTIFIED): Phrase(
        plain="Listing what the saved criteria for this job ask a candidate to show.",
        detailed="Listing what each of the {criterion_count} for this job asks a candidate to show.",
    ),
    (_REPORT, ev.SKILLS_COMPARED): Phrase(
        plain="Comparing each answer with the criterion it was asked against.",
    ),
    (_REPORT, ev.GAPS_IDENTIFIED): Phrase(
        plain="Marking the criteria the answers did not clearly evidence.",
        detailed="Marking the {gap_count} the answers did not clearly evidence.",
    ),
    (_REPORT, ev.RECOMMENDATIONS_GENERATED): Phrase(
        plain="Turning those gaps into the action plan that goes into the report.",
    ),
    (_REPORT, ev.RESULTS_RECORDED): Phrase(
        plain="Filing the PRISM Report against this application.",
    ),
    (_REPORT, ev.STEP_UNAVAILABLE): Phrase(
        plain="One step of this report could not be completed, so nothing from it is presented as finished.",
    ),
    (_REPORT, ev.TASK_COMPLETED): Phrase(
        plain="The PRISM Report for this candidate is ready.",
    ),
    (_REPORT, ev.TASK_FAILED): Phrase(
        plain="Vivekium could not finish this candidate's PRISM Report. Please try again.",
    ),
    (_REPORT, ev.TASK_CANCELLED): Phrase(
        plain="Report writing was stopped before it finished.",
    ),
}


#: Priority 3 of Case 3 section 19: a registered workflow reached a milestone
#: the catalogue has no sentence for. Still task-specific, so the reader is told
#: which piece of work is running even in the case nobody wrote copy for. The
#: build fails before this can be reached for a DECLARED stage
#: (`test_ai_activity.py`), so it covers only an event a workflow emits without
#: having declared it.
TASK_DEFAULT: dict[str, str] = {
    _MATCHING: "Working through the candidates linked to this job.",
    _REPORT: "Working through this candidate's assessment.",
}

#: Priority 4, and the only sentence in this module that says nothing about the
#: task. Reachable only for a task with no default, which the build also
#: refuses, so in practice this is the answer to "the catalogue was somehow
#: empty" rather than a state the product ships in.
GENERIC = "Working on your request."


def phrase_for(task: str, kind: str) -> Phrase | None:
    return PHRASING.get((task, kind))


def plain_text(task: str, kind: str) -> str:
    """The no-facts sentence for a pair. Raises rather than guessing.

    Used by `services/matching_progress` to derive a stage's static description,
    so the stage list and the activity line cannot drift into two different
    sentences for the same piece of work.
    """
    phrase = PHRASING.get((task, kind))
    if phrase is None:
        raise KeyError(f"no activity phrase for task={task} kind={kind}")
    return phrase.plain
