"""Trajectory evaluation: the SEQUENCE an agent took, not only what it returned.

    python -m app.scripts.eval_trajectory

WHY A THIRD EVAL WHEN TWO ALREADY GATE CI
------------------------------------------
`eval_interview.py` measures the conversational agent's JUDGEMENT across a
labelled set. `eval_agents.py` measures the FRAMEWORK: routing against
permissions, tool reachability, deadline feasibility, ten past defects. Both ask
a question about a single point in time. Neither can see a run that produced the
right report by reflecting eleven times, re-retrieving the same chunk on every
pass, retrying a permission refusal until the deadline, and refusing a budget
after the money was already spent. Every one of those runs looks like a success
at the point both existing evals inspect.

The specification states it plainly, and the sentence is the whole reason this
file exists: "A correct final output achieved via unsafe or excessively
expensive behavior should not be considered a fully successful execution."

WHY IT IS OFFLINE AND DETERMINISTIC
-----------------------------------
Same reason `eval_interview` is, and the reason is worth restating because it is
what makes the exit code mean anything: a rate that moves must mean the CODE
changed, not that a provider sampled differently that afternoon. Nothing here
calls a model, opens a socket or touches the database. Two runs on unchanged
code print byte-identical JSON.

HOW IT AVOIDS MARKING ITS OWN HOMEWORK
---------------------------------------
Two halves, and the split matters.

  * A LABELLED CORPUS of synthetic trajectories. Each carries the set of
    violations it is built to exhibit, including a healthy one that must exhibit
    none. Every checker runs against every trajectory and the detected set is
    compared with the declared set, so a checker that stopped detecting its own
    defect fails, and so does one that started flagging a clean run. A gate that
    cannot fail is not a gate.
  * LIVE PROBES against the real modules -- `agent_loop`, `Budget`,
    `tools.permissions`, `tools.errors`. Their thresholds are READ from the
    module that owns them rather than restated here, because a second copy of a
    limit is a limit that eventually disagrees with the first. The healthy
    trajectory's stage order is built from `reasoning.planner` itself, so a
    dependency the planner starts declaring wrongly fails this eval rather than
    being reproduced faithfully by a hand-written fixture.

WHAT IT DELIBERATELY DOES NOT CLAIM
-----------------------------------
It does not threshold cost or latency. Spec 43 says those targets are to be
determined empirically after instrumentation, and a ceiling invented before the
measurement is a number that will be tuned by feel forever. The counters are
reported and nothing gates on them.

It does not report a human-quality figure. See the section at the bottom: those
metrics need blind human review against defined rubrics, and an unmeasurable
quality figure reported as 0.0 is a number that means nothing and looks like
something.

EVERYTHING PRINTED HERE IS OPERATOR DATA
-----------------------------------------
Counts, timings, stage names, violation codes. None of it may reach a response
schema and none of it is client-visible: the no-numbers-to-a-client rule is not
suspended because the numbers are about the machine.

EXIT CODE
---------
Non-zero when a labelled trajectory is misclassified or a live probe fails.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services import agent_loop
from app.services.reasoning import planner
from app.services.reliability import budget as budgeting
from app.services.tools import errors as tool_errors
from app.services.tools import permissions, registry
from app.services.verification import base as verification

# Written by another workstream and imported defensively. Absent is reported as
# "unavailable" rather than skipped silently: a check nobody can see is not
# running is a check that has already stopped protecting anything. Absent is
# also never a FAILURE, because a missing optional module is not a regression in
# the trajectory rules this file is about.
try:  # pragma: no cover -- exercised by whichever half of the tree is present
    from app.services.agents import envelope as _envelope
except ImportError:  # pragma: no cover
    _envelope = None  # type: ignore[assignment]


# ── The shape of a trajectory ────────────────────────────────────────────────
# Deliberately a plain dataclass rather than a reader of `agent_execution_traces`.
# A persisted trace carries identifiers, counts and timings and never content,
# which is correct for the database and useless for building the pathological
# runs this file has to detect. These are constructed, labelled and disposable.


@dataclass(frozen=True)
class Step:
    """One stage of one run, with everything a trajectory rule needs to judge it."""

    stage: str
    #: The runtime agent id from `tools.permissions`. Checked against the real
    #: grant table, never against a copy of it.
    agent: str = ""
    #: Set when this stage reached data through `tools.execute`.
    tool: str | None = None
    depends_on: tuple[str, ...] = ()
    #: Seconds from the start of the run. Offsets rather than clock times so a
    #: trajectory is comparable between machines and between runs.
    start_offset_s: float = 0.0
    duration_s: float = 0.0
    #: A digest STANDS IN for the output. Never the output: this file is a
    #: sibling of the trace allowlist, and the same reason applies -- a detail
    #: can quote a candidate.
    input_digest: str = ""
    output_digest: str = ""
    #: The retrieval query plus its scope, digested. Two retrieve stages sharing
    #: one of these asked the index the identical question twice.
    query_digest: str = ""
    #: Class NAME of whatever this step failed with, resolved against
    #: `tools.errors.is_retryable` rather than judged here.
    error_class: str | None = None
    #: True when the framework attempted this step again after `error_class`.
    retried: bool = False
    #: A call that changes a row. No registered tool does today, which is why
    #: the absence check below is worth keeping.
    mutates: bool = False
    idempotency_key: str | None = None
    #: Severities the verifier returned at this stage, from
    #: `verification.SEVERITY_*`. Drives the trivial-replan rule.
    finding_severities: tuple[str, ...] = ()
    #: Whether this stage had anything the previous one did not. A reflect stage
    #: with nothing new is reflection for its own sake.
    new_evidence: bool = True


TERMINAL_STATES: frozenset[str] = frozenset({"completed", "degraded", "refused"})


@dataclass(frozen=True)
class Trajectory:
    """One labelled run. `expected` is the ground truth, and it is HAND-WRITTEN.

    Hand-written is the point. Ground truth generated by the same class of
    machinery being evaluated measures agreement with that machinery, not
    correctness, which is the identical objection this codebase raises to an
    LLM judge and to a synthesised expert dataset.
    """

    name: str
    task_type: str
    agent: str
    steps: tuple[Step, ...]
    deadline_seconds: float = agent_loop.BACKGROUND_DEADLINE
    terminal_state: str = "completed"
    loop_count: int = 1
    #: Refusals as `Budget` records them. Empty on a run that never hit a ceiling.
    budget_refusals: tuple[str, ...] = ()
    #: Whether the ceiling was consulted before the work it was guarding.
    budget_checked_before_work: bool = True
    #: Whether a ceiling was reached at all, which is what makes an EMPTY
    #: refusal list either healthy or a silent stop.
    budget_ceiling_reached: bool = False
    expected: frozenset[str] = frozenset()


# ── Violation codes ──────────────────────────────────────────────────────────
# One code per named rule. They are strings rather than an enum so the JSON is
# readable by somebody who has never opened this file.

DEPENDENCY_VIOLATION = "dependency_violation"
REFLECTION_WITHOUT_NEW_EVIDENCE = "reflection_without_new_evidence"
IDENTICAL_REGENERATION = "identical_regeneration"
REDUNDANT_RETRIEVAL = "redundant_retrieval"
RETRY_STORM = "retry_storm"
CHAIN_ADDED_NO_INFORMATION = "chain_added_no_information"
INFINITE_SELF_CRITIQUE = "infinite_self_critique"
TRIVIAL_REPLAN = "trivial_replan"
NONTERMINAL = "nonterminal"
DEADLINE_OBSERVED_NOT_PREDICTED = "deadline_observed_not_predicted"
BUDGET_REFUSAL_AFTER_WORK = "budget_refusal_after_work"
UNRECORDED_BUDGET_REFUSAL = "unrecorded_budget_refusal"
TOOL_OUTSIDE_GRANT = "tool_outside_grant"
RETRIED_NONRETRYABLE = "retried_nonretryable"
UNKEYED_MUTATION = "unkeyed_mutation"

#: Stage names this file recognises. A trajectory may carry others and they are
#: simply not subject to the stage-specific rules.
STAGE_EXECUTE = "execute"
STAGE_REFLECT = "reflect"
STAGE_RETRIEVE = "retrieve"
STAGE_REPLAN = "replan"
STAGE_HANDOFF = "handoff"

#: How many identical attempts at one tool stop being a retry and become a
#: storm. NOT a number chosen here: it is the tool's own registered
#: `max_attempts`, falling back to `agent_loop.BACKGROUND_ATTEMPTS` for a stage
#: that reached no registered tool. The executor already refuses to exceed it,
#: so a trajectory that did is evidence the framework was bypassed.
def _attempt_ceiling(tool: str | None) -> int:
    spec = registry.get(tool) if tool else None
    return spec.max_attempts if spec is not None else agent_loop.BACKGROUND_ATTEMPTS


# ── The rules ────────────────────────────────────────────────────────────────
# Each returns the offending details, empty when the trajectory is clean on that
# rule. Details are phrased so an operator reading the JSON knows which stage to
# open, which is the same register `verification.Finding.recommendation` uses.


def _dependency_violations(run: Trajectory) -> list[str]:
    """A stage that ran before something it declared a dependency on.

    The failure this prevents is a synthesis stage that scored items it had not
    read yet: it produces a plausible report from an empty context, and nothing
    downstream can tell that apart from a report written from a thin resume.
    """
    seen: set[str] = set()
    problems: list[str] = []
    for step in run.steps:
        missing = [name for name in step.depends_on if name not in seen]
        if missing:
            problems.append(
                f"{step.stage} ran before its dependencies {sorted(missing)}"
            )
        seen.add(step.stage)
    return problems


def _reflection_without_new_evidence(run: Trajectory) -> list[str]:
    """Reflecting on nothing new. Spec 6.4's first anti-pattern.

    `agent_loop.reflection_text` turns a rejection into an instruction. A
    reflect stage whose input carries no finding the previous one did not
    already carry produces the identical instruction, so the next attempt gets
    the identical prompt, and the loop pays for a model call to be told the same
    thing twice.
    """
    problems: list[str] = []
    for step in run.steps:
        if step.stage == STAGE_REFLECT and not step.new_evidence:
            problems.append(
                f"{step.stage} at {step.start_offset_s:g}s reflected with no "
                "finding the previous reflection did not already carry"
            )
    return problems


def _identical_regeneration(run: Trajectory) -> list[str]:
    """Two execute stages that produced byte-identical output.

    The second one cost a model call and changed nothing. Worse, it is the shape
    a loop takes when the reflection never reached the prompt at all, which is
    exactly the `.format()` defect `eval_interview` was extended to catch on the
    conversational side: a fallback is a legitimate output, so it is
    indistinguishable from success unless something asserts the difference.
    """
    problems: list[str] = []
    previous: str | None = None
    for step in run.steps:
        if step.stage != STAGE_EXECUTE:
            continue
        if previous is not None and step.output_digest and step.output_digest == previous:
            problems.append(
                f"{step.stage} at {step.start_offset_s:g}s reproduced the "
                "previous attempt exactly"
            )
        previous = step.output_digest
    return problems


def _redundant_retrieval(run: Trajectory) -> list[str]:
    """The same query, against the same scope, asked twice.

    Retrieval is not free and it is not idempotent by declaration:
    `retrieve_context` is registered `idempotent=False` precisely because the
    index is rewritten when a resume finishes parsing. So the second identical
    round is neither cached nor useful, and it spends an interactive budget an
    agent is about to need for generation.
    """
    problems: list[str] = []
    asked: set[str] = set()
    for step in run.steps:
        if step.stage != STAGE_RETRIEVE or not step.query_digest:
            continue
        if step.query_digest in asked:
            problems.append(
                f"{step.stage} at {step.start_offset_s:g}s re-asked the index an "
                "identical query with an unchanged scope"
            )
        asked.add(step.query_digest)
    return problems


def _retry_storms(run: Trajectory) -> list[str]:
    """More attempts at one tool than the executor would ever have allowed."""
    problems: list[str] = []
    counted: dict[str, int] = {}
    for step in run.steps:
        if not step.tool:
            continue
        key = f"{step.tool}:{step.input_digest}"
        counted[key] = counted.get(key, 0) + 1
    for key, count in sorted(counted.items()):
        tool = key.split(":", 1)[0]
        ceiling = _attempt_ceiling(tool)
        if count > ceiling:
            problems.append(
                f"{tool} was attempted {count} times on one payload; its "
                f"registered ceiling is {ceiling}"
            )
    return problems


def _chain_added_no_information(run: Trajectory) -> list[str]:
    """A handoff whose output is its input.

    The multi-agent failure spec 6.4 names: three agents in a chain, each
    forwarding what it received, and a trace that looks busy. The cost is real
    and the information gain is zero, which is only visible if somebody compares
    the two ends of each hop.
    """
    problems: list[str] = []
    for step in run.steps:
        if step.stage != STAGE_HANDOFF:
            continue
        if step.input_digest and step.output_digest == step.input_digest:
            problems.append(
                f"{step.agent or 'agent'} handed on exactly what it received at "
                f"{step.start_offset_s:g}s"
            )
    return problems


def _infinite_self_critique(run: Trajectory) -> list[str]:
    """More reflect stages than `Budget` permits replans.

    The ceiling is READ from `reliability.budget`, not restated. Three replans is
    already a task that is not converging; the point of reading the constant is
    that raising it there raises it here, and lowering it there fails this.
    """
    reflections = sum(1 for step in run.steps if step.stage == STAGE_REFLECT)
    if reflections > budgeting.MAX_REPLANS:
        return [
            f"{reflections} reflect stages exceed the {budgeting.MAX_REPLANS} "
            "replans the budget allows"
        ]
    return []


def _trivial_replans(run: Trajectory) -> list[str]:
    """A replan triggered by an observation that did not disqualify anything.

    `Verdict.passed` is arithmetic: one low finding costs 0.05 against a floor
    of 0.7, so an output carrying only low findings PASSED. Replanning on it
    spends a whole attempt to fix something the verifier explicitly declined to
    fail, and it is the cheapest kind of runaway to write by accident.
    """
    problems: list[str] = []
    for step in run.steps:
        if step.stage != STAGE_REPLAN:
            continue
        verdict = verification.verdict(
            "trajectory",
            [
                verification.Finding(severity, "trajectory", step.stage, "", "")
                for severity in step.finding_severities
            ],
        )
        if verdict.passed:
            problems.append(
                f"{step.stage} at {step.start_offset_s:g}s replanned on findings "
                f"the verifier passed (confidence {verdict.confidence:g})"
            )
    return problems


def _nonterminal(run: Trajectory) -> list[str]:
    """Every loop must have reached a terminal state inside its bounds.

    `run_loop` never raises and always returns, so a run recorded as still open
    means something outside it kept the loop alive -- an outer retry, a task
    requeued, a supervisor restarting a stage. That is the shape an unbounded
    system takes even when every individual loop is bounded.
    """
    problems: list[str] = []
    if run.terminal_state not in TERMINAL_STATES:
        problems.append(
            f"the run ended in {run.terminal_state!r}, which is not one of "
            f"{sorted(TERMINAL_STATES)}"
        )
    if run.loop_count > budgeting.MAX_ITERATIONS:
        problems.append(
            f"{run.loop_count} loops exceed the {budgeting.MAX_ITERATIONS} "
            "iteration ceiling"
        )
    return problems


def _deadline_not_predicted(run: Trajectory) -> list[str]:
    """An attempt was STARTED that could not finish inside the budget.

    This is the codebase's own scar, applied to a whole trajectory rather than
    to one loop. `elapsed >= deadline` sounds right and is not: an attempt
    bounded at 24s under a 26s deadline passes `24 >= 26` as False, a second
    attempt starts, and the true worst case is 48 seconds with a candidate
    watching a text box. The predicate here is the one `agent_loop` and
    `tools.executor` both settled on, `elapsed + longest_attempt_so_far >=
    deadline`, so a trajectory whose last attempt started with insufficient
    remaining budget is a failure even if it happened to finish.
    """
    problems: list[str] = []
    longest = 0.0
    for step in run.steps:
        if step.stage != STAGE_EXECUTE:
            continue
        if longest and step.start_offset_s + longest >= run.deadline_seconds:
            problems.append(
                f"{step.stage} started at {step.start_offset_s:g}s when the "
                f"longest attempt so far was {longest:g}s against a "
                f"{run.deadline_seconds:g}s deadline"
            )
        longest = max(longest, step.duration_s)
    return problems


def _budget_refused_after_work(run: Trajectory) -> list[str]:
    """Checking a ceiling afterwards makes it a report, not a limit."""
    if not run.budget_checked_before_work:
        return ["the budget was consulted after the work it was guarding"]
    return []


def _unrecorded_budget_refusal(run: Trajectory) -> list[str]:
    """A ceiling that stopped something silently.

    Spec 12, and `Budget.refusals` exists for exactly this: a budget that
    stopped something without recording it is indistinguishable from a task that
    simply finished, so the operator sees a shorter report and no reason.
    """
    if run.budget_ceiling_reached and not run.budget_refusals:
        return ["a ceiling was reached and no refusal was recorded"]
    return []


def _tools_outside_grant(run: Trajectory) -> list[str]:
    """Every tool call was inside the CALLING agent's grant.

    Resolved against `tools.permissions` live, so a grant widened in that table
    widens what passes here, and a trajectory reaching for something the table
    never gave it fails whichever way the table moves.
    """
    problems: list[str] = []
    for step in run.steps:
        if not step.tool:
            continue
        if not permissions.is_granted(step.agent, step.tool):
            problems.append(
                f"{step.agent!r} called {step.tool!r}, which it does not hold"
            )
    return problems


def _retried_nonretryable(run: Trajectory) -> list[str]:
    """Only retryable error classes were retried.

    `tools.errors` owns the split and this reads it rather than restating it. A
    retried permission refusal is the expensive direction twice over: it burns
    the deadline, and it is the shape a successful injection takes when the
    agent keeps reaching for a tool it was refused.
    """
    problems: list[str] = []
    classes = {
        name: getattr(tool_errors, name)
        for name in dir(tool_errors)
        if isinstance(getattr(tool_errors, name), type)
        and issubclass(getattr(tool_errors, name), BaseException)
    }
    for step in run.steps:
        if not step.retried or not step.error_class:
            continue
        cls = classes.get(step.error_class)
        if cls is None:
            problems.append(
                f"{step.stage} retried on {step.error_class!r}, which is not a "
                "class tools.errors defines"
            )
            continue
        if not tool_errors.is_retryable(cls("t", "d") if issubclass(cls, tool_errors.ToolError) else cls()):
            problems.append(
                f"{step.stage} retried a {step.error_class}, which is not retryable"
            )
    return problems


def _unkeyed_mutations(run: Trajectory) -> list[str]:
    """A mutating call with no idempotency key.

    The product already learned this in the money path: Razorpay delivers
    webhooks at least once and a platform redelivers tasks, so a double effect is
    the DEFAULT behaviour unless something prevents it. An agent retrying a
    mutating tool is the same situation with a less careful caller.
    """
    problems: list[str] = []
    for step in run.steps:
        if step.mutates and not step.idempotency_key:
            problems.append(
                f"{step.tool or step.stage} mutates and carries no idempotency key"
            )
    return problems


#: The rule table. Order is reporting order only.
RULES: tuple[tuple[str, Callable[[Trajectory], list[str]]], ...] = (
    (DEPENDENCY_VIOLATION, _dependency_violations),
    (REFLECTION_WITHOUT_NEW_EVIDENCE, _reflection_without_new_evidence),
    (IDENTICAL_REGENERATION, _identical_regeneration),
    (REDUNDANT_RETRIEVAL, _redundant_retrieval),
    (RETRY_STORM, _retry_storms),
    (CHAIN_ADDED_NO_INFORMATION, _chain_added_no_information),
    (INFINITE_SELF_CRITIQUE, _infinite_self_critique),
    (TRIVIAL_REPLAN, _trivial_replans),
    (NONTERMINAL, _nonterminal),
    (DEADLINE_OBSERVED_NOT_PREDICTED, _deadline_not_predicted),
    (BUDGET_REFUSAL_AFTER_WORK, _budget_refused_after_work),
    (UNRECORDED_BUDGET_REFUSAL, _unrecorded_budget_refusal),
    (TOOL_OUTSIDE_GRANT, _tools_outside_grant),
    (RETRIED_NONRETRYABLE, _retried_nonretryable),
    (UNKEYED_MUTATION, _unkeyed_mutations),
)


def evaluate(run: Trajectory) -> dict[str, list[str]]:
    """Every rule against one trajectory. Keys are the codes that fired."""
    found: dict[str, list[str]] = {}
    for code, rule in RULES:
        details = rule(run)
        if details:
            found[code] = details
    return found


# ── The labelled corpus ──────────────────────────────────────────────────────


def _healthy() -> Trajectory:
    """A clean run whose stage order comes from the REAL planner.

    Built rather than hand-written on purpose. A fixture that restated the
    dependency graph would agree with a planner that had started declaring it
    wrongly, and the disagreement is the only thing worth detecting. The agent
    is the one `orchestration.router` would route this task to, so the tool
    grants are the real ones too.
    """
    plan = planner.plan("ppi_report", permissions.AGENT_PPI_REPORT)
    registered = registry.names()
    steps: list[Step] = []
    offset = 0.0
    for subtask in plan.subtasks:
        tool = subtask.name if subtask.name in registered else None
        steps.append(
            Step(
                stage=subtask.name,
                agent=permissions.AGENT_PPI_REPORT,
                tool=tool,
                depends_on=subtask.depends_on,
                start_offset_s=round(offset, 3),
                duration_s=0.4,
                input_digest=f"in:{subtask.name}",
                output_digest=f"out:{subtask.name}",
                query_digest=f"q:{subtask.name}" if subtask.name == "retrieve_context" else "",
            )
        )
        offset += 0.4
    steps.append(
        Step(
            stage=STAGE_EXECUTE,
            agent=permissions.AGENT_PPI_REPORT,
            start_offset_s=round(offset, 3),
            duration_s=1.2,
            output_digest="report:v1",
        )
    )
    return Trajectory(
        name="healthy_ppi_report",
        task_type="ppi_report",
        agent=permissions.AGENT_PPI_REPORT,
        steps=tuple(steps),
        terminal_state="completed",
        loop_count=1,
        expected=frozenset(),
    )


def _degraded_but_honest() -> Trajectory:
    """A provider outage handled the way the product promises.

    Two attempts, the second declined because it could not FINISH inside the
    budget, terminal state `degraded`. This trajectory must come back clean:
    degradation is expected operation, not a defect, and a trajectory eval that
    flagged it would push somebody to hide the degradation instead of recording
    it.
    """
    return Trajectory(
        name="degraded_interactive_turn",
        task_type="interviewer",
        agent=permissions.AGENT_INTERVIEWER,
        steps=(
            Step(
                stage=STAGE_EXECUTE,
                agent=permissions.AGENT_INTERVIEWER,
                start_offset_s=0.0,
                duration_s=11.0,
                output_digest="",
                error_class="ToolTimeout",
                retried=True,
            ),
            Step(
                stage=STAGE_EXECUTE,
                agent=permissions.AGENT_INTERVIEWER,
                start_offset_s=11.0,
                duration_s=11.0,
                output_digest="turn:v1",
            ),
        ),
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
        terminal_state="degraded",
        loop_count=1,
        expected=frozenset(),
    )


def _refused_by_budget() -> Trajectory:
    """A ceiling reached, refused before the work, and recorded. Also clean."""
    return Trajectory(
        name="budget_refused_and_recorded",
        task_type="ppi_report",
        agent=permissions.AGENT_PPI_REPORT,
        steps=(
            Step(
                stage=STAGE_EXECUTE,
                agent=permissions.AGENT_PPI_REPORT,
                start_offset_s=0.0,
                duration_s=1.0,
                output_digest="draft:v1",
            ),
        ),
        terminal_state="refused",
        budget_ceiling_reached=True,
        budget_refusals=("cost: 0.2600 USD would exceed the 0.2400 USD ceiling",),
        expected=frozenset(),
    )


def _one_step(code: str, **overrides: Any) -> Step:
    base = dict(
        stage=STAGE_EXECUTE,
        agent=permissions.AGENT_PPI_REPORT,
        start_offset_s=0.0,
        duration_s=0.5,
        output_digest=f"out:{code}",
        input_digest=f"in:{code}",
    )
    base.update(overrides)
    return Step(**base)  # type: ignore[arg-type]


def _defective() -> list[Trajectory]:
    """One trajectory per rule, each labelled with exactly the code it exhibits.

    Written to trip ONE rule. A fixture that tripped three would still pass a
    weakened checker, because the other two would carry the assertion.
    """
    agent = permissions.AGENT_PPI_REPORT
    return [
        Trajectory(
            name="synthesis_before_retrieval",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(
                    DEPENDENCY_VIOLATION,
                    stage="synthesise",
                    depends_on=("score_items",),
                ),
                _one_step(DEPENDENCY_VIOLATION, stage="score_items", start_offset_s=0.5),
            ),
            expected=frozenset({DEPENDENCY_VIOLATION}),
        ),
        Trajectory(
            name="reflection_on_nothing_new",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(REFLECTION_WITHOUT_NEW_EVIDENCE, stage=STAGE_REFLECT, new_evidence=False),
            ),
            expected=frozenset({REFLECTION_WITHOUT_NEW_EVIDENCE}),
        ),
        Trajectory(
            name="regenerated_the_same_answer",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(IDENTICAL_REGENERATION, output_digest="same"),
                _one_step(IDENTICAL_REGENERATION, output_digest="same", start_offset_s=0.5),
            ),
            expected=frozenset({IDENTICAL_REGENERATION}),
        ),
        Trajectory(
            name="asked_the_index_twice",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(REDUNDANT_RETRIEVAL, stage=STAGE_RETRIEVE, query_digest="kafka|resume:1"),
                _one_step(
                    REDUNDANT_RETRIEVAL,
                    stage=STAGE_RETRIEVE,
                    query_digest="kafka|resume:1",
                    start_offset_s=0.5,
                ),
            ),
            expected=frozenset({REDUNDANT_RETRIEVAL}),
        ),
        Trajectory(
            name="hammered_one_tool",
            task_type="ppi_report",
            agent=agent,
            steps=tuple(
                _one_step(
                    RETRY_STORM,
                    stage="extract_jd",
                    tool="extract_jd",
                    input_digest="job:1",
                    start_offset_s=index * 0.5,
                )
                for index in range(_attempt_ceiling("extract_jd") + 2)
            ),
            expected=frozenset({RETRY_STORM}),
        ),
        Trajectory(
            name="chain_forwarded_its_input",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(
                    CHAIN_ADDED_NO_INFORMATION,
                    stage=STAGE_HANDOFF,
                    input_digest="same",
                    output_digest="same",
                ),
            ),
            expected=frozenset({CHAIN_ADDED_NO_INFORMATION}),
        ),
        Trajectory(
            name="critiqued_itself_forever",
            task_type="ppi_report",
            agent=agent,
            steps=tuple(
                _one_step(
                    INFINITE_SELF_CRITIQUE,
                    stage=STAGE_REFLECT,
                    start_offset_s=index * 0.5,
                    output_digest=f"reflect:{index}",
                )
                for index in range(budgeting.MAX_REPLANS + 1)
            ),
            expected=frozenset({INFINITE_SELF_CRITIQUE}),
        ),
        Trajectory(
            name="replanned_on_a_passing_verdict",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(
                    TRIVIAL_REPLAN,
                    stage=STAGE_REPLAN,
                    finding_severities=(verification.SEVERITY_LOW,),
                ),
            ),
            expected=frozenset({TRIVIAL_REPLAN}),
        ),
        Trajectory(
            name="never_reached_a_terminal_state",
            task_type="ppi_report",
            agent=agent,
            steps=(_one_step(NONTERMINAL),),
            terminal_state="running",
            expected=frozenset({NONTERMINAL}),
        ),
        Trajectory(
            name="started_an_attempt_it_could_not_finish",
            task_type="interviewer",
            agent=permissions.AGENT_INTERVIEWER,
            steps=(
                _one_step(
                    DEADLINE_OBSERVED_NOT_PREDICTED,
                    agent=permissions.AGENT_INTERVIEWER,
                    start_offset_s=0.0,
                    duration_s=24.0,
                ),
                _one_step(
                    DEADLINE_OBSERVED_NOT_PREDICTED,
                    agent=permissions.AGENT_INTERVIEWER,
                    start_offset_s=24.0,
                    duration_s=24.0,
                    output_digest="second",
                ),
            ),
            deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
            expected=frozenset({DEADLINE_OBSERVED_NOT_PREDICTED}),
        ),
        Trajectory(
            name="checked_the_ceiling_afterwards",
            task_type="ppi_report",
            agent=agent,
            steps=(_one_step(BUDGET_REFUSAL_AFTER_WORK),),
            budget_checked_before_work=False,
            budget_ceiling_reached=True,
            budget_refusals=("cost: recorded after the spend",),
            expected=frozenset({BUDGET_REFUSAL_AFTER_WORK}),
        ),
        Trajectory(
            name="stopped_silently_at_a_ceiling",
            task_type="ppi_report",
            agent=agent,
            steps=(_one_step(UNRECORDED_BUDGET_REFUSAL),),
            budget_ceiling_reached=True,
            budget_refusals=(),
            expected=frozenset({UNRECORDED_BUDGET_REFUSAL}),
        ),
        Trajectory(
            name="email_agent_read_a_resume",
            task_type="email",
            agent=permissions.AGENT_EMAIL,
            steps=(
                _one_step(
                    TOOL_OUTSIDE_GRANT,
                    stage="extract_resume",
                    agent=permissions.AGENT_EMAIL,
                    tool="extract_resume",
                ),
            ),
            expected=frozenset({TOOL_OUTSIDE_GRANT}),
        ),
        Trajectory(
            name="retried_a_permission_refusal",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(
                    RETRIED_NONRETRYABLE,
                    stage="extract_jd",
                    tool="extract_jd",
                    error_class="ToolPermissionError",
                    retried=True,
                ),
            ),
            expected=frozenset({RETRIED_NONRETRYABLE}),
        ),
        Trajectory(
            name="mutated_without_a_key",
            task_type="ppi_report",
            agent=agent,
            steps=(
                _one_step(
                    UNKEYED_MUTATION,
                    stage="record_decision",
                    mutates=True,
                    idempotency_key=None,
                ),
            ),
            expected=frozenset({UNKEYED_MUTATION}),
        ),
    ]


def corpus() -> list[Trajectory]:
    """Healthy first, so a reader sees the clean cases before the pathological."""
    return [_healthy(), _degraded_but_honest(), _refused_by_budget(), *_defective()]


# ── Live probes against the real modules ─────────────────────────────────────
# The corpus measures the RULES. These measure the CODE the rules describe, so a
# rule that is still correct about a framework that stopped behaving that way
# fails here rather than passing quietly in both places.


async def _probe_deadline_predicts() -> list[str]:
    """`run_loop` must decline an attempt it cannot finish, and take one it can.

    Both directions. A loop that never retries satisfies the first half and has
    silently thrown away the reflection the whole framework is built on, which
    is why the fast case is asserted in the same probe.
    """
    problems: list[str] = []

    async def _slow(_reflection: str) -> str:
        await asyncio.sleep(0.3)
        return "unusable"

    async def _fast(_reflection: str) -> str:
        await asyncio.sleep(0.01)
        return "unusable"

    slow = await agent_loop.run_loop(
        name="eval_trajectory.slow",
        execute=_slow,
        evaluate=lambda _value: agent_loop.reject("try again"),
        fallback="fallback",
        max_attempts=2,
        deadline_seconds=0.5,
    )
    if slow.attempts != 1:
        problems.append(
            f"a 0.3s attempt under a 0.5s deadline ran {slow.attempts} attempts; "
            "the deadline observed rather than predicted"
        )
    if not slow.degraded or slow.value != "fallback":
        problems.append("a loop that ran out of time did not degrade to its fallback")

    fast = await agent_loop.run_loop(
        name="eval_trajectory.fast",
        execute=_fast,
        evaluate=lambda _value: agent_loop.reject("try again"),
        fallback="fallback",
        max_attempts=2,
        deadline_seconds=0.5,
    )
    if fast.attempts != 2:
        problems.append(
            f"a 0.01s attempt under a 0.5s deadline ran {fast.attempts} attempts; "
            "the retry the reflection exists for never happened"
        )
    return problems


def _probe_budget_refuses_before_work() -> list[str]:
    """The ceiling must be consulted first, and every refusal recorded."""
    problems: list[str] = []
    budget = budgeting.Budget("ppi_report")
    for _ in range(budgeting.MAX_ITERATIONS):
        budget.begin_iteration()
    before = budget.iterations
    try:
        budget.begin_iteration()
    except budgeting.BudgetExceeded:
        if budget.iterations != before:
            problems.append(
                "the refused iteration was counted, so the check ran after the work"
            )
    else:
        problems.append(
            f"{budgeting.MAX_ITERATIONS} iterations did not reach the ceiling"
        )
    if not budget.refusals:
        problems.append("a ceiling was reached and Budget recorded no refusal")

    replans = budgeting.Budget("ppi_report")
    for _ in range(budgeting.MAX_REPLANS):
        replans.begin_replan()
    try:
        replans.begin_replan()
    except budgeting.BudgetExceeded:
        pass
    else:
        problems.append(f"{budgeting.MAX_REPLANS} replans did not reach the ceiling")
    return problems


def _probe_error_taxonomy() -> list[str]:
    """Only the classes that say they are retryable may be retried."""
    problems: list[str] = []
    for name in ("ToolTimeout", "RetryableToolError"):
        if not tool_errors.is_retryable(getattr(tool_errors, name)("t", "d")):
            problems.append(f"{name} stopped being retryable")
    for name in (
        "ToolNotFound",
        "ToolPermissionError",
        "ToolInputError",
        "ToolOutputError",
        "ToolExecutionError",
    ):
        if tool_errors.is_retryable(getattr(tool_errors, name)("t", "d")):
            problems.append(f"{name} became retryable, which buys nothing but latency")
    if tool_errors.is_retryable(ValueError("a genuine bug")):
        problems.append("an unrecognised exception is retried, tripling a real bug")
    return problems


def _probe_tool_surface_is_read_only() -> list[str]:
    """No registered tool mutates, and no agent is granted one that could.

    An ABSENCE check, which is the only kind worth having here. Spec 24.4 is
    that authorisation is never enforced by a model, and the enforcement in this
    product is that a write tool does not exist to be reached for.
    """
    mutating_prefixes = (
        "write_", "update_", "create_", "delete_", "send_", "set_",
        "reject_", "approve_", "revoke_", "override_",
    )
    problems: list[str] = []
    for name in sorted(registry.names()):
        if name.startswith(mutating_prefixes):
            problems.append(f"tool {name!r} names a mutation and is reachable by an agent")
    for agent, granted in sorted(permissions.AGENT_TOOLS.items()):
        for name in sorted(granted):
            if name.startswith(mutating_prefixes):
                problems.append(f"{agent} is granted the mutating tool {name!r}")
    return problems


def _probe_optional_run_budget() -> tuple[str, list[str]]:
    """The workflow-level ceilings, when that module is present.

    Reported as "unavailable" rather than skipped when it is not. A check that
    quietly vanished when an import failed is a check that has stopped
    protecting anything while still appearing in the list.
    """
    if _envelope is None or not hasattr(_envelope, "RunBudget"):
        return (
            "unavailable",
            [
                "UNAVAILABLE: app.services.agents.envelope.RunBudget is not "
                "present, so the workflow-level ceilings were not checked"
            ],
        )
    problems: list[str] = []
    interactive = _envelope.RunBudget.for_task("interviewer", interactive=True)
    background = _envelope.RunBudget.for_task("ppi_report", interactive=False)
    if interactive.max_latency_ms >= background.max_latency_ms:
        problems.append(
            "an interactive run is allowed at least as long as a background one"
        )
    if interactive.max_retries != budgeting.MAX_REPLANS:
        problems.append(
            "RunBudget carries its own retry ceiling instead of the one "
            "reliability.budget owns"
        )
    if interactive.max_steps != budgeting.MAX_ITERATIONS:
        problems.append(
            "RunBudget carries its own step ceiling instead of the one "
            "reliability.budget owns"
        )
    return ("ok" if not problems else "failed", problems)


# ── Cost and latency counters ────────────────────────────────────────────────


def counters(runs: list[Trajectory]) -> dict[str, Any]:
    """Aggregate cost shape across the corpus. REPORTED, NEVER THRESHOLDED.

    Spec 43 says the targets are to be determined empirically after
    instrumentation. A ceiling invented before the measurement is a number
    somebody tunes by feel forever, and it would fail runs for being unusual
    rather than for being wrong. Operator data: none of it may reach a response
    schema.
    """
    model_calls = sum(
        1 for run in runs for step in run.steps if step.stage == STAGE_EXECUTE
    )
    tool_calls = sum(1 for run in runs for step in run.steps if step.tool)
    retrievals = sum(
        1 for run in runs for step in run.steps if step.stage == STAGE_RETRIEVE
    )
    loops = [run.loop_count for run in runs]
    return {
        "trajectories": len(runs),
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "retrieval_rounds": retrievals,
        "average_loops": round(sum(loops) / len(loops), 4) if loops else 0.0,
        "note": (
            "reported, never thresholded: spec 43 requires these targets to be "
            "set from live instrumentation rather than guessed here"
        ),
    }


# ── Human-quality metrics (spec 23) ──────────────────────────────────────────

#: Named so the absence is enumerable rather than a sentence somebody can read
#: past. Everything here is a judgement about how the product FEELS to a person.
HUMAN_QUALITY_DIMENSIONS: tuple[str, ...] = (
    "naturalness",
    "conversational_continuity",
    "question_relevance",
    "non_repetition",
    "appropriate_probing",
    "perceived_fairness",
    "clarity",
    "specificity",
    "tone",
    "report_readability",
    "usefulness_of_recommendations",
    "perceived_personalization",
)

_HUMAN_QUALITY_EXPLANATION = (
    "UNAVAILABLE: these require blind human review against defined rubrics over "
    "50-100 stratified expert-rated cases, which is HUMAN work and must never be "
    "synthesised; ground truth produced by the same class of model being "
    "evaluated measures agreement with that model, not quality."
)


def human_quality_section() -> dict[str, Any]:
    """Separate from the technical metrics, and unavailable on purpose.

    Kept in its own section rather than mixed into the rates above because the
    two answer different questions and are measured by different means. A proxy
    score invented here would be a number that means nothing and looks like
    something, which is the failure `eval_agents` already refuses to commit.
    """
    return {
        "status": "UNAVAILABLE",
        "explanation": _HUMAN_QUALITY_EXPLANATION,
        "dimensions": {name: "UNAVAILABLE" for name in HUMAN_QUALITY_DIMENSIONS},
    }


# ── Runner ───────────────────────────────────────────────────────────────────


@dataclass
class CorpusResult:
    matched: int = 0
    total: int = 0
    mismatches: list[str] = field(default_factory=list)
    per_trajectory: dict[str, Any] = field(default_factory=dict)


def run_corpus(runs: list[Trajectory]) -> CorpusResult:
    result = CorpusResult()
    for run in runs:
        found = evaluate(run)
        detected = frozenset(found)
        result.total += 1
        if detected == run.expected:
            result.matched += 1
        else:
            missed = sorted(run.expected - detected)
            spurious = sorted(detected - run.expected)
            result.mismatches.append(
                f"{run.name}: missed {missed}, falsely flagged {spurious}"
            )
        result.per_trajectory[run.name] = {
            "expected": sorted(run.expected),
            "detected": sorted(detected),
            "details": {code: found[code] for code in sorted(found)},
        }
    return result


def main() -> int:
    runs = corpus()
    report: dict[str, Any] = {}

    corpus_result = run_corpus(runs)
    report["trajectory_rules"] = {
        # 1.0 is the only defensible threshold for a set labelled by hand in
        # this file: every case is either detected or it is not, and there is no
        # sampling anywhere for a rate below 1.0 to be measuring.
        "threshold": 1.0,
        "matched": corpus_result.matched,
        "total": corpus_result.total,
        "rate": round(corpus_result.matched / corpus_result.total, 4)
        if corpus_result.total
        else 0.0,
        "mismatches": corpus_result.mismatches,
        "rules_checked": [code for code, _ in RULES],
        "trajectories": corpus_result.per_trajectory,
    }

    probes: dict[str, Any] = {}
    probes["deadline_predicts_the_next_attempt"] = asyncio.run(_probe_deadline_predicts())
    probes["budget_refuses_before_the_work"] = _probe_budget_refuses_before_work()
    probes["error_taxonomy_only_retries_retryables"] = _probe_error_taxonomy()
    probes["tool_surface_is_read_only"] = _probe_tool_surface_is_read_only()
    optional_status, optional_notes = _probe_optional_run_budget()
    report["live_probes"] = {
        "problems": {name: found for name, found in probes.items() if found},
        "ok": not any(probes.values()),
    }
    report["optional_checks"] = {
        "workflow_run_budget": {"status": optional_status, "notes": optional_notes},
    }

    report["counters"] = counters(runs)
    report["human_quality"] = human_quality_section()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))

    failed = bool(corpus_result.mismatches) or any(probes.values()) or optional_status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
