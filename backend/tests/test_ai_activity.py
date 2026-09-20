"""The context-aware AI activity status layer.

`ai-upgrade-spec-doc.md` "Case 3" is the specification. The properties worth
testing are the ones a screenshot cannot check, and they are all of the form
"the status must not be able to say something untrue":

  * the same event under two tasks must not produce the same sentence, because
    that is the generic rotation the whole feature exists to replace;
  * a count must not appear unless the workflow computed it;
  * a sentence must never expose the model's private reasoning;
  * a failure must END the activity rather than leave the last line on screen;
  * two operations running at once must not overwrite each other.

The catalogue is swept whole rather than checked at a call site. A rule enforced
at one call site is a rule the next entry breaks, which is the argument
`test_candidate_updates.py` already makes about the Updates feed.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.services import matching_progress as mp
from app.services.activity import events as ev
from app.services.activity import phrasing, render, stream, workflows


# ── Every sentence the product can show ─────────────────────────────────────


def all_catalogue_text() -> list[tuple[str, str]]:
    """(where, text) for every string a reader can be shown by this layer."""
    rows: list[tuple[str, str]] = []
    for (task, kind), phrase in phrasing.PHRASING.items():
        rows.append((f"{task}/{kind}/plain", phrase.plain))
        if phrase.detailed:
            rows.append((f"{task}/{kind}/detailed", phrase.detailed))
    for task, text in phrasing.TASK_DEFAULT.items():
        rows.append((f"{task}/default", text))
    rows.append(("generic", phrasing.GENERIC))
    for workflow in workflows.WORKFLOWS.values():
        rows.append((f"{workflow.task}/label", workflow.label))
    return rows


# ── The requirement: same event, different task, different sentence ─────────


def test_the_same_event_reads_differently_under_a_different_task():
    """Case 3 section 8, stated as the example that defines the feature.

    If the sentence depended on the event alone, every workflow would walk the
    same script and the user would see one sequence whatever they asked for.
    """
    shared = [
        kind
        for kind in ev.EVENT_KINDS
        if (ev.TASK_JOB_CANDIDATE_MATCHING, kind) in phrasing.PHRASING
        and (ev.TASK_ASSESSMENT_REPORT, kind) in phrasing.PHRASING
    ]
    assert len(shared) >= 7, "the two wired workflows barely overlap; widen the check"
    for kind in shared:
        matching = phrasing.PHRASING[(ev.TASK_JOB_CANDIDATE_MATCHING, kind)].plain
        report = phrasing.PHRASING[(ev.TASK_ASSESSMENT_REPORT, kind)].plain
        assert matching != report, kind


def test_two_tasks_render_the_same_event_kind_into_different_lines():
    """The property end to end, through the renderer rather than the table."""
    lines = {
        task: render.render(
            ev.ActivityEvent(task=task, kind=ev.REQUIREMENTS_IDENTIFIED),
            operation_id="op",
            sequence=1,
        ).text
        for task in (ev.TASK_JOB_CANDIDATE_MATCHING, ev.TASK_ASSESSMENT_REPORT)
    }
    assert len(set(lines.values())) == 2, lines


def test_a_whole_run_of_each_workflow_shares_no_sentence_with_the_other():
    """Two different requests must not produce the same activity messages.

    The prohibition is on the SEQUENCE, not on one line, so this walks both
    declared workflows end to end and compares the transcripts.
    """
    transcripts = {}
    for task, workflow in workflows.WORKFLOWS.items():
        run = stream.ActivityStream(task, operation_id=f"op-{task}")
        for kind in workflow.stages:
            run.emit(kind)
        run.complete()
        transcripts[task] = [line.text for line in run.lines]
    matching, report = (
        transcripts[ev.TASK_JOB_CANDIDATE_MATCHING],
        transcripts[ev.TASK_ASSESSMENT_REPORT],
    )
    assert not set(matching) & set(report)


# ── No generic rotation, and no timer ───────────────────────────────────────


def test_nothing_in_this_layer_schedules_anything():
    """The prohibited implementation is a timer over a message list.

    An import of a scheduler or a sleep in this package would be that mechanism
    under another name, whatever the module was called.
    """
    for module in (phrasing, render, stream, workflows, ev):
        source = inspect.getsource(module)
        for forbidden in ("setInterval", "time.sleep", "asyncio.sleep", "itertools.cycle", "threading.Timer"):
            assert forbidden not in source, f"{module.__name__}: {forbidden}"


def test_the_status_generator_calls_no_model():
    """Case 3 section 6. A separate model call per status update would add
    latency and cost, and would let the status contradict the execution."""
    for module in (phrasing, render, stream, workflows, ev, mp):
        source = inspect.getsource(module)
        for forbidden in ("invoke_llm", "llm_router", "chat_completion", "agent_loop", "openai"):
            assert forbidden not in source, f"{module.__name__}: {forbidden}"


def test_the_generic_line_is_unreachable_for_every_declared_workflow():
    """Priority 4 must be the fallback, never the normal experience.

    Every stage a workflow declares, and all three terminal kinds, must resolve
    to a phrase from the catalogue. A workflow half-declared fails here rather
    than shipping a screen that says "Working on your request".
    """
    for task, workflow in workflows.WORKFLOWS.items():
        assert task in ev.TASK_KINDS, task
        assert task in phrasing.TASK_DEFAULT, task
        wanted = (*workflow.stages, *workflows.REQUIRED_TERMINAL_KINDS)
        for kind in wanted:
            assert kind in ev.EVENT_KINDS, f"{task}/{kind}"
            line = render.render(
                ev.ActivityEvent(task=task, kind=kind), operation_id="op", sequence=1
            )
            assert line.source == ev.SOURCE_EVENT, f"{task}/{kind} fell back"
            assert line.text != phrasing.GENERIC, f"{task}/{kind}"


def test_every_registered_task_kind_declares_a_workflow():
    """The other direction. A task with no declaration has no stage list, so
    nothing holds its copy to the checks above."""
    assert set(workflows.WORKFLOWS) == set(ev.TASK_KINDS)


def test_a_fallback_is_recorded_on_the_line_rather_than_hidden():
    """No silent fallbacks. A screen showing the generic line must be
    identifiable as such from the payload the browser received."""
    line = render.render(
        # A registered task, a real event kind, and deliberately no phrase for
        # the pair: `TASK_CANCELLED` exists for both, so use a kind the matching
        # workflow does not declare.
        ev.ActivityEvent(task=ev.TASK_JOB_CANDIDATE_MATCHING, kind=ev.GAPS_IDENTIFIED),
        operation_id="op",
        sequence=1,
    )
    assert line.source == ev.SOURCE_TASK_DEFAULT
    assert line.text == phrasing.TASK_DEFAULT[ev.TASK_JOB_CANDIDATE_MATCHING]


# ── No chain of thought ─────────────────────────────────────────────────────


#: Case 3 section 17. Each of these is a phrase from the specification's own
#: "do not show" list, or the vocabulary a model uses when narrating its private
#: deliberation rather than its observable workflow.
_REASONING_PHRASES = (
    "my reasoning",
    "i'm reasoning",
    "reasoning through",
    "chain of thought",
    "thinking about",
    "let me think",
    "i think",
    "probability",
    "internally",
    "considering whether",
    "first i",
    "then i",
    "hidden",
    "prompt",
    "token",
)


def test_no_catalogue_sentence_exposes_private_reasoning():
    for where, text in all_catalogue_text():
        lowered = text.lower()
        for phrase in _REASONING_PHRASES:
            assert phrase not in lowered, f"{where}: {phrase}"


def test_no_catalogue_sentence_names_infrastructure():
    """A status is not a debugging console (Case 3 sections 21 and 25).

    A module name, a provider, a bucket or a status code on a recruiter's
    screen is an internal detail leaking through the one surface that is
    supposed to describe their work.
    """
    for where, text in all_catalogue_text():
        lowered = text.lower()
        for leak in (
            "redis",
            "lambda",
            "fargate",
            "postgres",
            "sql",
            "traceback",
            "exception",
            "http",
            "s3",
            "bucket",
            ".py",
        ):
            assert leak not in lowered, f"{where}: {leak}"


# ── No false precision ──────────────────────────────────────────────────────


def test_no_catalogue_sentence_carries_a_literal_number_or_a_percentage():
    """Case 3 section 18. A number in the copy is a number nobody measured.

    The only numbers that may reach a reader are the facts a workflow computed,
    and those arrive through a placeholder that the renderer refuses to fill
    unless the value was actually supplied.
    """
    for where, text in all_catalogue_text():
        assert not re.search(r"\d", text), where
        assert "%" not in text, where


def test_no_catalogue_sentence_states_a_grade_or_an_estimate():
    """claude.md: no grade and no band reaches a client, and Case 3 section 18
    forbids a completion estimate.

    "Scoring" and "scored" survive as VERBS naming the work, which is what the
    job page has always called it. What is banned is the assessment output
    itself and any claim about how far along the run is, because neither is
    something an activity line is in a position to know.
    """
    for where, text in all_catalogue_text():
        lowered = text.lower()
        for banned in (
            "highly matching",
            "moderately matching",
            "not matching",
            "percent",
            "out of",
            "almost done",
            "nearly done",
            "estimated",
            "halfway",
        ):
            assert banned not in lowered, f"{where}: {banned}"


def test_a_count_is_refused_unless_the_workflow_supplied_it():
    """The detailed sentence is unreachable without its facts, so an absent
    count cannot become a zero, a guess or a stale value on screen."""
    without = render.render(
        ev.ActivityEvent(
            task=ev.TASK_JOB_CANDIDATE_MATCHING, kind=ev.SKILLS_COMPARED
        ),
        operation_id="op",
        sequence=1,
    )
    assert (
        without.text
        == phrasing.PHRASING[
            (ev.TASK_JOB_CANDIDATE_MATCHING, ev.SKILLS_COMPARED)
        ].plain
    )
    assert not re.search(r"\d", without.text)

    with_fact = render.render(
        ev.ActivityEvent(
            task=ev.TASK_JOB_CANDIDATE_MATCHING,
            kind=ev.SKILLS_COMPARED,
            facts={"candidate_count": 12},
        ),
        operation_id="op",
        sequence=2,
    )
    assert "12 candidates" in with_fact.text


def test_a_count_of_one_is_spoken_in_the_singular():
    line = render.render(
        ev.ActivityEvent(
            task=ev.TASK_JOB_CANDIDATE_MATCHING,
            kind=ev.SKILLS_COMPARED,
            facts={"candidate_count": 1},
        ),
        operation_id="op",
        sequence=1,
    )
    assert "1 candidate against" in line.text


@pytest.mark.parametrize("bad", [None, -3, "many", 4.5, True])
def test_a_malformed_count_drops_the_sentence_rather_than_printing_it(bad):
    """The failure this prevents is a workflow passing a placeholder value and
    the renderer printing it. Dropping to the true sentence is the safe
    direction; cleaning the value up quietly is not."""
    line = render.render(
        ev.ActivityEvent(
            task=ev.TASK_JOB_CANDIDATE_MATCHING,
            kind=ev.SKILLS_COMPARED,
            facts={"candidate_count": bad},
        ),
        operation_id="op",
        sequence=1,
    )
    assert not re.search(r"\d", line.text)


# ── User context reaches the status, safely ─────────────────────────────────


def test_the_user_s_own_role_title_reaches_the_line():
    """Case 3 section 9: "the fintech role" rather than "your document"."""
    line = render.render(
        ev.ActivityEvent(
            task=ev.TASK_JOB_CANDIDATE_MATCHING,
            kind=ev.TASK_STARTED,
            facts={"role_title": "Senior Product Manager"},
        ),
        operation_id="op",
        sequence=1,
    )
    assert "Senior Product Manager" in line.text


@pytest.mark.parametrize(
    "bad_title",
    [
        "",
        "   ",
        "Head of Product " + chr(8212) + " Fintech",
        "Lead {evil}",
        "Manager\nwith a newline",
        "x" * (phrasing.MAX_TEXT_FACT_CHARS + 1),
    ],
)
def test_an_unusable_title_falls_back_to_the_plain_sentence(bad_title):
    """A client types the job title. It is not this layer's copy, so it is
    checked rather than trusted, and a value that fails is not repaired."""
    line = render.render(
        ev.ActivityEvent(
            task=ev.TASK_JOB_CANDIDATE_MATCHING,
            kind=ev.TASK_STARTED,
            facts={"role_title": bad_title},
        ),
        operation_id="op",
        sequence=1,
    )
    assert (
        line.text
        == phrasing.PHRASING[
            (ev.TASK_JOB_CANDIDATE_MATCHING, ev.TASK_STARTED)
        ].plain
    )


def test_an_undeclared_fact_is_never_interpolated():
    """An undeclared name has no agreed spoken form, so printing it would be
    the renderer inventing one."""
    line = render.render(
        ev.ActivityEvent(
            task=ev.TASK_JOB_CANDIDATE_MATCHING,
            kind=ev.TASK_STARTED,
            facts={"internal_pool_id": "abc"},
        ),
        operation_id="op",
        sequence=1,
    )
    assert "abc" not in line.text


# ── The em dash, and the product name ───────────────────────────────────────


def test_no_em_dash_anywhere_in_the_catalogue():
    """Repo-wide rule, and this is copy a client reads."""
    dash = chr(8212)
    for where, text in all_catalogue_text():
        assert dash not in text, where


def test_the_product_is_never_called_anything_but_readypick():
    """The specification calls the product Readypeek throughout. It is
    Vivekium, and user-visible copy says that or says nothing."""
    for where, text in all_catalogue_text():
        assert "readypeek" not in text.lower(), where
        assert "pickready" not in text.lower(), where
        for occurrence in re.findall(r"(?i)readypick", text):
            assert occurrence == "Vivekium", f"{where}: {occurrence}"


def test_every_sentence_is_a_sentence():
    """Not a status code and not a fragment: it is read aloud by a screen
    reader and shown inline in a paragraph."""
    for where, text in all_catalogue_text():
        if where.endswith("/label"):
            continue
        assert text[0].isupper(), where
        assert text.endswith("."), where
        assert chr(8230) not in text, where


# ── Errors and cancellation terminate ───────────────────────────────────────


def test_a_failure_replaces_the_running_line_and_ends_the_stream():
    """Case 3 section 25. The named failure is a user left reading "I'm
    analysing your document" after the request has already failed."""
    run = stream.ActivityStream(ev.TASK_JOB_CANDIDATE_MATCHING, operation_id="op")
    run.emit(ev.SKILLS_COMPARED)
    running = run.current
    run.fail()
    assert run.state == ev.STATE_ERROR
    assert run.current is not None
    assert run.current.text != running.text
    assert run.current.terminal
    assert "try again" in run.current.text.lower()


def test_a_failure_line_carries_no_reason_at_all():
    """`fail` takes no argument, so there is no parameter through which an
    exception message, a prompt or a path could reach a browser."""
    signature = inspect.signature(stream.ActivityStream.fail)
    assert list(signature.parameters) == ["self"]


def test_nothing_is_recorded_after_a_terminal_state():
    """A stale line arriving after the operation ended is exactly what section
    24 lists, and the count of what was dropped is published rather than
    swallowed."""
    run = stream.ActivityStream(ev.TASK_ASSESSMENT_REPORT, operation_id="op")
    run.emit(ev.TASK_STARTED)
    run.fail()
    assert run.emit(ev.SKILLS_COMPARED) is None
    assert run.state == ev.STATE_ERROR
    assert run.current is not None and run.current.kind == ev.TASK_FAILED
    assert run.payload()["dropped_after_terminal"] == 1


def test_cancellation_terminates_the_stream_too():
    run = stream.ActivityStream(ev.TASK_ASSESSMENT_REPORT, operation_id="op")
    run.emit(ev.TASK_STARTED)
    run.cancel()
    assert run.state == ev.STATE_CANCELLED
    assert run.emit(ev.DOCUMENT_PARSED) is None


def test_a_degraded_step_is_reported_as_a_gap_and_is_not_terminal():
    """A run that lost its embedding stage really did rank on keywords alone.
    Reporting that as complete would present a degraded run as a full one."""
    run = stream.ActivityStream(ev.TASK_JOB_CANDIDATE_MATCHING, operation_id="op")
    run.unavailable("The embedding service was unavailable for this run.")
    assert run.state == ev.STATE_RUNNING
    assert run.current is not None
    assert "unavailable" in run.current.detail


# ── Concurrency ─────────────────────────────────────────────────────────────


def test_two_operations_carry_different_identifiers_and_do_not_collide():
    a = stream.ActivityStream(ev.TASK_JOB_CANDIDATE_MATCHING)
    b = stream.ActivityStream(ev.TASK_JOB_CANDIDATE_MATCHING)
    assert a.operation_id != b.operation_id
    a.emit(ev.TASK_STARTED)
    b.emit(ev.SKILLS_COMPARED)
    b.fail()
    # b terminating says nothing about a, and every line is attributable.
    assert a.state == ev.STATE_RUNNING
    assert a.current is not None and a.current.operation_id == a.operation_id
    assert b.current is not None and b.current.operation_id == b.operation_id
    assert a.payload()["operation_id"] != b.payload()["operation_id"]


def test_the_caller_s_own_run_id_is_used_when_it_has_one():
    """The browser holds the dispatched run id before the first event exists,
    so it can attribute the very first payload it receives."""
    run = stream.ActivityStream(ev.TASK_ASSESSMENT_REPORT, operation_id="run-123")
    run.emit(ev.TASK_STARTED)
    assert run.payload()["operation_id"] == "run-123"
    assert run.current is not None and run.current.operation_id == "run-123"


def test_the_log_is_bounded():
    """The payload is written to Redis on every change and read by a browser.
    An unbounded log grows both without limit."""
    run = stream.ActivityStream(ev.TASK_JOB_CANDIDATE_MATCHING, operation_id="op")
    for _ in range(stream.MAX_LINES * 2):
        run.emit(ev.SKILLS_COMPARED)
    assert len(run.lines) == stream.MAX_LINES
    # The NEWEST survive: the reader is being shown what is happening now.
    assert run.lines[-1].sequence == stream.MAX_LINES * 2


# ── Refusals that are programming errors, not runtime conditions ────────────


def test_an_unregistered_task_is_refused():
    with pytest.raises(ev.UnknownTask):
        stream.ActivityStream("summarise_the_universe")


def test_an_undeclared_event_kind_is_refused():
    run = stream.ActivityStream(ev.TASK_JOB_CANDIDATE_MATCHING)
    with pytest.raises(ev.UnknownEvent):
        run.emit("THINKING_REALLY_HARD")


def test_a_failing_publisher_never_reaches_the_work():
    def _explode(_payload):
        raise RuntimeError("the status record is unreachable")

    run = stream.ActivityStream(
        ev.TASK_JOB_CANDIDATE_MATCHING, publish=_explode
    )
    run.emit(ev.TASK_STARTED)  # must not raise
    run.fail()
    assert run.state == ev.STATE_ERROR


# ── The matching workflow is genuinely wired ────────────────────────────────


def test_every_matching_stage_declares_an_activity_event_the_catalogue_covers():
    """The stage list and the activity line are one sentence, not two.

    A stage whose event has no phrase would render a blank description on a
    running screen, so it fails the build instead.
    """
    for stage in mp.STAGES:
        assert stage.event in ev.EVENT_KINDS, stage.key
        assert stage.detail == phrasing.plain_text(mp.TASK, stage.event)
        assert stage.detail.endswith("."), stage.key


def test_each_matching_stage_has_its_own_sentence():
    """Two stages sharing a phrase would draw the same description twice and
    make the plan unreadable."""
    details = [stage.detail for stage in mp.STAGES]
    assert len(set(details)) == len(details)


def test_the_matching_run_publishes_activity_beside_its_stages():
    """The activity block travels in the payload the job page already polls, so
    the progress and the activity cannot be read a poll apart."""
    seen: list[dict] = []
    progress = mp.Progress(publish=seen.append, operation_id="run-abc")
    progress.start("understanding")
    assert seen[-1]["activity"]["operation_id"] == "run-abc"
    assert seen[-1]["activity"]["state"] == ev.STATE_RUNNING
    assert seen[-1]["activity"]["line"]["kind"] == ev.TASK_STARTED


def test_a_matching_run_reads_as_a_progression_of_real_work():
    """Case 3 section 10: the activity evolves through what the pipeline did,
    with the findings it computed between the steps."""
    progress = mp.Progress(operation_id="run-abc")
    progress.start("understanding")
    progress.start("semantic_retrieval")
    progress.finish("semantic_retrieval", "12 resumes matched on meaning.")
    progress.start("scoring", "Assessing the pool against this job's categories.")
    lines = [
        row["detail"] or row["text"]
        for row in progress.payload()["activity"]["log"]
    ]
    assert lines == [
        phrasing.plain_text(mp.TASK, ev.TASK_STARTED),
        phrasing.plain_text(mp.TASK, ev.SEMANTIC_SEARCH_COMPLETED),
        "12 resumes matched on meaning.",
        "Assessing the pool against this job's categories.",
    ]


def test_a_skipped_matching_stage_reaches_the_activity_line_as_a_gap():
    progress = mp.Progress(operation_id="run-abc")
    progress.skip(
        "semantic_retrieval",
        "The embedding service was unavailable, so this run ranks on keywords alone.",
    )
    activity = progress.payload()["activity"]
    assert activity["line"]["kind"] == ev.STEP_UNAVAILABLE
    assert activity["state"] == ev.STATE_RUNNING


def test_a_failed_matching_stage_terminates_the_activity_without_quoting_it():
    """The stage row keeps the pipeline's reason. The activity line, which is
    what a browser renders, gets the fixed failure sentence and nothing else."""
    progress = mp.Progress(operation_id="run-abc")
    progress.start("scoring")
    progress.fail("scoring", "The scoring provider returned nothing usable.")
    payload = progress.payload()
    stage = next(row for row in payload["stages"] if row["key"] == "scoring")
    assert stage["detail"] == "The scoring provider returned nothing usable."
    assert payload["activity"]["state"] == ev.STATE_ERROR
    assert "provider" not in payload["activity"]["line"]["text"].lower()
    assert payload["activity"]["line"]["detail"] == ""


def test_scoring_progress_does_not_emit_a_line_per_batch():
    """Text changing under the reader with no new milestone behind it is the
    animation section 21 rules out."""
    progress = mp.Progress(operation_id="run-abc")
    progress.start("scoring")
    before = len(progress.payload()["activity"]["log"])
    progress.scored(1, 9)
    progress.scored(5, 9)
    assert len(progress.payload()["activity"]["log"]) == before


def test_the_counts_a_matching_line_may_speak_come_from_the_run():
    progress = mp.Progress(operation_id="run-abc")
    progress.scored(0, 9)
    progress.complete()
    assert "9 candidates" in progress.payload()["activity"]["line"]["text"]


def test_the_role_the_recruiter_is_hiring_for_reaches_the_line():
    """Case 3 section 9: name the work, not "your document".

    The title becomes known after the run has already opened, which is why
    `describe` exists separately from `start`.
    """
    progress = mp.Progress(operation_id="run-abc")
    progress.describe(role_title="Senior Product Manager")
    progress.start("understanding")
    assert (
        "Senior Product Manager"
        in progress.payload()["activity"]["line"]["text"]
    )


def test_a_fact_the_catalogue_does_not_declare_cannot_be_pushed_into_a_line():
    progress = mp.Progress(operation_id="run-abc")
    progress.describe(role_title="Manager", secret_note="internal only")
    progress.start("understanding")
    assert "internal only" not in progress.payload()["activity"]["line"]["text"]


def test_a_queued_run_is_attributable_before_the_worker_picks_it_up():
    payload = mp.empty_payload(operation_id="run-abc")
    assert payload["activity"]["operation_id"] == "run-abc"
    assert payload["activity"]["state"] == ev.STATE_IDLE
    assert payload["activity"]["line"] is None
