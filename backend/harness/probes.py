"""What a scenario may ask about, and the one place each question is answered.

WHY A REGISTRY OF NAMED PROBES RATHER THAN SQL IN A SCENARIO
--------------------------------------------------------------
The same argument HARNESS.md section 2 makes about world builders. A scenario
that wrote its own `SELECT count(*) FROM pipeline_status WHERE ...` would be
one nobody could compose, and worse, it would be one whose meaning drifts: two
scenarios counting "the transitions for this application" with two slightly
different predicates disagree silently, and the disagreement surfaces as a
flaky harness rather than as a contradiction anybody can see.

So a probe is a NAME with exactly one definition. `pipeline_status.count_for_link`
means one thing across the whole scenario directory, and changing what it means
is one edit here.

EVERY STATE PROBE READS THROUGH A `StateReader`, WHICH IS A SECOND CONNECTION
-------------------------------------------------------------------------------
There is no overload that takes the request's session, and that absence is the
enforcement. HARNESS.md section 3 is explicit that a write which answered 200
and then rolled back at commit is invisible to an assertion on the response
body, and this repository shipped exactly that on 2026-09-20. A probe that
could be handed the request's own session would make that bug re-expressible.

PROHIBITED PROBES ANSWER "DID IT HAPPEN", NOT "IS IT FINE"
------------------------------------------------------------
Each returns `(occurred, detail)`. The scenario asserts the absence, so the
probe's job is to find the thing and say where. A probe returning a bare bool
would produce a failure report reading "prohibited outcome occurred", which
tells the reader nothing about which payload to open.

Provenance: docs/spec/HARNESS.md section 3.
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Mapping, Sequence

from app.services import conversation_guardrails as guard
from app.services.siddhi import numbers

from harness.context import Observation, ScenarioContext, StateReader
from harness.world import World, WorldError

__all__ = [
    "ProbeError",
    "prohibited_names",
    "read_output",
    "read_prohibited",
    "read_state",
    "state_names",
]


class ProbeError(LookupError):
    """A scenario named something this registry cannot answer."""


StateProbe = Callable[[StateReader, World, str | None], Awaitable[Any]]


def _tenant(world: World) -> str:
    return str(world.id("tenant"))


# ── State probes ─────────────────────────────────────────────────────────────


async def _jobs_count(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM jobs WHERE tenant_id = :t", {"t": _tenant(world)}
    )


async def _job_lifecycle(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.scalar(
        "SELECT lifecycle_state FROM jobs WHERE id = :j", {"j": str(world.id("job"))}
    )


async def _job_framework_approved(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT framework_approved_at IS NOT NULL FROM jobs WHERE id = :j",
        {"j": str(world.id("job"))},
    )


async def _job_criteria_version(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT criteria_version FROM jobs WHERE id = :j",
        {"j": str(world.id("job"))},
    )


async def _competency_active_count(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM job_competencies WHERE job_id = :j AND is_active",
        {"j": str(world.id("job"))},
    )


async def _competency_total_count(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM job_competencies WHERE job_id = :j",
        {"j": str(world.id("job"))},
    )


async def _competency_active_names(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return sorted(
        await reader.column(
            "SELECT name FROM job_competencies WHERE job_id = :j AND is_active",
            {"j": str(world.id("job"))},
        )
    )


async def _competency_swot_origin(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """The SWOT sentence the ACTIVE skill named `arg` carries, or None.

    Read per name because the rule it pins is per row: a revive KEEPS the
    sentence (the same name coming back) and a rename CLEARS it (a different
    criterion wearing the row's identity), the 2026-09-23 asymmetry.
    """
    if not arg:
        raise ProbeError("job_competencies.swot_origin needs a skill name: `:<name>`")
    return await reader.scalar(
        "SELECT swot_origin FROM job_competencies "
        "WHERE job_id = :j AND is_active AND name = :n",
        {"j": str(world.id("job")), "n": arg},
    )


async def _competency_with_evidence_count(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """Active skills carrying an evidence line. Only Save Skills writes one."""
    return await reader.scalar(
        "SELECT count(*) FROM job_competencies WHERE job_id = :j AND is_active "
        "AND observable_evidence IS NOT NULL AND btrim(observable_evidence) <> ''",
        {"j": str(world.id("job"))},
    )


async def _job_skills_draft_status(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT skills_draft_status FROM jobs WHERE id = :j",
        {"j": str(world.id("job"))},
    )


async def _job_assessment_context_writer(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """Who wrote the job's hidden assessment context, as the row records it.

    The writer's own name rather than a boolean, so a scenario can tell a
    context Sutra wrote from the honest empty one a migration or a world
    stamped. NULL means no context was written at all.
    """
    return await reader.scalar(
        "SELECT assessment_context_json ->> 'generated_by' FROM jobs WHERE id = :j",
        {"j": str(world.id("job"))},
    )


async def _links_for_job(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM job_candidate_links WHERE job_id = :j",
        {"j": str(world.id("job"))},
    )


async def _link_status(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.scalar(
        "SELECT status FROM job_candidate_links WHERE id = :l",
        {"l": str(world.id("link"))},
    )


async def _link_status_for_candidate(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """The status of whatever link the candidate's own application produced.

    Distinct from `job_candidate_links.status`, which reads the link the WORLD
    seeded. A scenario whose subject is the apply route has no seeded link, and
    asserting over one it created itself is the only honest way to judge it.
    """
    return await reader.scalar(
        "SELECT status FROM job_candidate_links WHERE job_id = :j "
        "AND candidate_id = :c",
        {"j": str(world.id("job")), "c": str(world.id("candidate"))},
    )


async def _pipeline_rows(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM pipeline_status WHERE job_candidate_link_id = :l",
        {"l": str(world.id("link"))},
    )


async def _pipeline_statuses(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.column(
        "SELECT status FROM pipeline_status WHERE job_candidate_link_id = :l "
        "ORDER BY at, id",
        {"l": str(world.id("link"))},
    )


async def _updates_for_link(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM candidate_updates WHERE job_candidate_link_id = :l",
        {"l": str(world.id("link"))},
    )


async def _updates_for_candidate(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM candidate_updates WHERE candidate_id = :c",
        {"c": str(world.id("candidate"))},
    )


async def _audit_count(reader: StateReader, world: World, arg: str | None) -> Any:
    """Audit rows for this tenant, optionally narrowed to one action.

    THIS IS THE PROBE THE 2026-09-20 DEFECT NEEDS, and it needs a second
    connection to mean anything. The doomed `UPDATE audit_log` flushed at
    COMMIT, after FastAPI had already sent a 200, and rolled the whole
    transaction back. Counted from the response body the write was there;
    counted from the database it never existed.
    """
    if arg:
        return await reader.scalar(
            "SELECT count(*) FROM audit_log WHERE tenant_id = :t AND action = :a",
            {"t": _tenant(world), "a": arg},
        )
    return await reader.scalar(
        "SELECT count(*) FROM audit_log WHERE tenant_id = :t", {"t": _tenant(world)}
    )


async def _audit_actions(reader: StateReader, world: World, arg: str | None) -> Any:
    return sorted(
        set(
            await reader.column(
                "SELECT action FROM audit_log WHERE tenant_id = :t",
                {"t": _tenant(world)},
            )
        )
    )


async def _ledger_entries_for_link(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM credit_ledger WHERE job_candidate_link_id = :l",
        {"l": str(world.id("link"))},
    )


async def _ledger_subunits(reader: StateReader, world: World, arg: str | None) -> Any:
    """The balance, as `SUM(subunits_delta)` and never a stored counter.

    Summed here for the same reason the product sums it: a customer disputing
    usage gets a statement, not a number, and a probe reading a cached total
    would agree with the cache rather than with the ledger.
    """
    return await reader.scalar(
        "SELECT COALESCE(SUM(subunits_delta), 0) FROM credit_ledger "
        "WHERE tenant_id = :t",
        {"t": _tenant(world)},
    )


async def _ledger_events(reader: StateReader, world: World, arg: str | None) -> Any:
    return sorted(
        await reader.column(
            "SELECT event_type FROM credit_ledger WHERE tenant_id = :t",
            {"t": _tenant(world)},
        )
    )


async def _profiles_for_candidate(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM profiles WHERE candidate_id = :c",
        {"c": str(world.id("candidate"))},
    )


async def _telemetry_for_link(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM telemetry_events WHERE job_candidate_link_id = :l",
        {"l": str(world.id("link"))},
    )


async def _conversations_for_link(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM assessment_conversations "
        "WHERE job_candidate_link_id = :l",
        {"l": str(world.id("link"))},
    )


async def _conversations_for_job(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """Every conversation on the job, whoever wrote it. For a world where the
    application is the thing under test, so there is no seeded link to key on:
    the row an apply might wrongly create has an id the world never saw."""
    return await reader.scalar(
        "SELECT count(*) FROM assessment_conversations WHERE job_id = :j",
        {"j": str(world.id("job"))},
    )


async def _questions_for_job(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM candidate_questions q "
        "JOIN job_candidate_links l ON l.id = q.job_candidate_link_id "
        "WHERE l.job_id = :j",
        {"j": str(world.id("job"))},
    )


async def _conversation_status(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT status FROM assessment_conversations WHERE id = :c",
        {"c": str(world.id("conversation"))},
    )


async def _conversation_credit_event(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """What the conversation itself records having been charged for.

    Separate from the ledger on purpose. `charge_completed` writes BOTH, and a
    scenario that read only one would not notice the two disagreeing, which is
    precisely the state a retry that charged twice and stamped once produces.
    """
    return await reader.scalar(
        "SELECT credit_event FROM assessment_conversations WHERE id = :c",
        {"c": str(world.id("conversation"))},
    )


#: Every confidence the aggregator can emit. Imported rather than retyped: a
#: fifth value added to `miti.aggregation` has to break this probe, not slip
#: past a hand-copied list that happens to hold four strings.
def _aggregator_confidence_values() -> tuple[str, ...]:
    from app.services.miti.aggregation import CONFIDENCE_LABELS

    return tuple(str(value) for value in CONFIDENCE_LABELS)


async def _confidence_values_the_column_refuses(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """Which aggregator confidences `evaluations.confidence` would reject.

    THE PROPERTY, NOT THE FIX (the rule `app/evaluation/regression.py` states
    for its own cases). The defect repaired by migration 0106 was a column
    declared `varchar(10)` with a CHECK admitting only high/medium/low while
    its ONE writer emitted `moderate` and `insufficient`: the first violated
    the constraint and the second violated the constraint and the width. It
    could not surface anywhere, because the only deployed environment holds
    zero candidates and no real evaluation has ever been written there, and it
    would have raised inside `run_functional_assessment` AFTER Miti's five
    evaluators and Siddhi's synthesis had already been paid for.

    So the property is: every value the writer can produce is storable. Asked
    of the CATALOG rather than by attempting an insert, because the state
    reader is read-only by construction (a probe that could write would be a
    second write path with none of the product's guards) and because a failed
    insert would abort the reader's transaction and take every later assertion
    in the same scenario with it.

    Returns a sorted list of the offending values, so a failure names them.
    """
    table, column = (arg or "evaluations.confidence").split(".", 1)
    width = await reader.scalar(
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c",
        {"t": table, "c": column},
    )
    definitions = await reader.column(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid "
        "WHERE t.relname = :t AND c.contype = 'c'",
        {"t": table},
    )
    guarding = [
        str(text) for text in definitions if f"{column}" in str(text)
    ]
    refused: list[str] = []
    for value in _aggregator_confidence_values():
        if width is not None and len(value) > int(width):
            refused.append(f"{value} (longer than varchar({width}))")
            continue
        # A CHECK that enumerates values refuses anything it does not name. A
        # column with no CHECK at all refuses nothing, which is its own
        # finding and is why `guarding` being empty is not treated as a pass
        # by omission: the width above is then the only thing standing there,
        # and that is exactly what `calibration_records` had.
        if guarding and not any(f"'{value}'" in text for text in guarding):
            refused.append(f"{value} (not named by the CHECK)")
    return sorted(refused)


async def _tenant_still_exists(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    return await reader.scalar(
        "SELECT count(*) FROM tenants WHERE id = :t", {"t": _tenant(world)}
    )



# ── The coding question (Phase 4) ───────────────────────────────────────────


def _conversation(world: World) -> str:
    return str(world.id("conversation"))


async def _coding_run_statuses(reader: StateReader, world: World, arg: str | None) -> Any:
    return await reader.column(
        "SELECT status FROM coding_runs WHERE conversation_id = :c ORDER BY created_at",
        {"c": _conversation(world)},
    )


async def _coding_run_error_classes(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """Which error each Run recorded. What separates "the sandbox was down"
    (`ExecutionUnavailable`) from "no sandbox is configured"
    (`ExecutionNotConfigured`), which answer the candidate with the same 503."""
    return await reader.column(
        "SELECT error_class FROM coding_runs WHERE conversation_id = :c ORDER BY created_at",
        {"c": _conversation(world)},
    )


#: The `coding_submissions` columns a scenario may read. An allowlist, because
#: the column name arrives from a scenario file and is placed into SQL.
_CODING_SUBMISSION_COLUMNS = frozenset(
    {
        "execution_status",
        "review_status",
        "execution_attempts",
        "last_error_class",
        "auto_submitted",
    }
)


async def _coding_submission_column(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """One column of the conversation's coding submission, by name.

    None when no submission row exists, which is itself the finding a scenario
    about a lost submission would assert on; a list when there is more than
    one, so a duplicate cannot hide behind the first row's value.
    """
    if arg not in _CODING_SUBMISSION_COLUMNS:
        raise ProbeError(
            f"coding_submissions.column needs one of "
            f"{', '.join(sorted(_CODING_SUBMISSION_COLUMNS))} after the colon, "
            f"got {arg!r}"
        )
    values = await reader.column(
        f"SELECT {arg} FROM coding_submissions WHERE conversation_id = :c "
        "ORDER BY created_at",
        {"c": _conversation(world)},
    )
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def _coding_sentinels(world: World) -> list[str]:
    try:
        return [str(item) for item in world.id("coding_key_sentinels")]
    except WorldError as exc:
        raise ProbeError(
            f"world {world.name!r} carries no coding answer key to sweep for; a "
            "coding exfiltration probe needs the `coding_question_open` world"
        ) from exc


#: Every place the product stores what a candidate did on a coding question,
#: and the question row itself. The answer key has exactly one home,
#: `coding_question_keys`, and it is deliberately NOT in this list.
_CODING_STORED_ROWS: tuple[tuple[str, str], ...] = (
    ("coding_runs", "SELECT * FROM coding_runs WHERE conversation_id = :c"),
    ("coding_submissions", "SELECT * FROM coding_submissions WHERE conversation_id = :c"),
    ("assessment_messages", "SELECT * FROM assessment_messages WHERE conversation_id = :c"),
    ("assessment_answers", "SELECT * FROM assessment_answers WHERE conversation_id = :c"),
    ("candidate_questions", "SELECT * FROM candidate_questions WHERE id = :q"),
)


async def _coding_key_sentinel_hits(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """Every stored table outside the key that holds any part of the key.

    Read from the second connection, after the run, across the whole row, so a
    column somebody adds later is swept without an edit here. Answers the
    table names, sorted; a clean run answers `[]`.
    """
    sentinels = _coding_sentinels(world)
    params = {"c": _conversation(world), "q": str(world.id("coding_question"))}
    hits: list[str] = []
    for table, sql in _CODING_STORED_ROWS:
        rows = await reader.rows(sql, params)
        dumped = json.dumps(rows, default=str, ensure_ascii=False)
        if any(sentinel in dumped for sentinel in sentinels):
            hits.append(table)
    return sorted(hits)


async def _coding_key_holds_the_sentinels(
    reader: StateReader, world: World, arg: str | None
) -> Any:
    """Whether the answer key itself holds every sentinel.

    The non-vacuity half of the sweep above: an absence everywhere else means
    something only if the sentinels are really in the one place they belong.
    """
    rows = await reader.rows(
        "SELECT * FROM coding_question_keys WHERE question_id = :q",
        {"q": str(world.id("coding_question"))},
    )
    dumped = json.dumps(rows, default=str, ensure_ascii=False)
    return bool(rows) and all(sentinel in dumped for sentinel in _coding_sentinels(world))


_STATE: dict[str, StateProbe] = {
    "jobs.count_for_tenant": _jobs_count,
    "jobs.lifecycle_state": _job_lifecycle,
    "jobs.framework_approved": _job_framework_approved,
    "jobs.criteria_version": _job_criteria_version,
    "job_competencies.active_count": _competency_active_count,
    "job_competencies.total_count": _competency_total_count,
    "job_competencies.active_names": _competency_active_names,
    "job_competencies.swot_origin": _competency_swot_origin,
    "job_competencies.with_evidence_count": _competency_with_evidence_count,
    "jobs.skills_draft_status": _job_skills_draft_status,
    "jobs.assessment_context_writer": _job_assessment_context_writer,
    "job_candidate_links.count_for_job": _links_for_job,
    "job_candidate_links.status": _link_status,
    "job_candidate_links.status_for_candidate": _link_status_for_candidate,
    "pipeline_status.count_for_link": _pipeline_rows,
    "pipeline_status.statuses_for_link": _pipeline_statuses,
    "candidate_updates.count_for_link": _updates_for_link,
    "candidate_updates.count_for_candidate": _updates_for_candidate,
    "audit_log.count": _audit_count,
    "audit_log.actions": _audit_actions,
    "credit_ledger.entries_for_link": _ledger_entries_for_link,
    "credit_ledger.subunits_total": _ledger_subunits,
    "credit_ledger.event_types": _ledger_events,
    "profiles.count_for_candidate": _profiles_for_candidate,
    "telemetry_events.count_for_link": _telemetry_for_link,
    "assessment_conversations.count_for_link": _conversations_for_link,
    "assessment_conversations.count_for_job": _conversations_for_job,
    "candidate_questions.count_for_job": _questions_for_job,
    "assessment_conversations.status": _conversation_status,
    "assessment_conversations.credit_event": _conversation_credit_event,
    "confidence_vocabulary.refused_values": _confidence_values_the_column_refuses,
    "tenants.count_for_tenant": _tenant_still_exists,
    "coding_runs.statuses": _coding_run_statuses,
    "coding_runs.error_classes": _coding_run_error_classes,
    "coding_submissions.column": _coding_submission_column,
    "coding.key_sentinel_hits": _coding_key_sentinel_hits,
    "coding.key_holds_the_sentinels": _coding_key_holds_the_sentinels,
}


def state_names() -> tuple[str, ...]:
    return tuple(sorted(_STATE))


async def read_state(name: str, reader: StateReader, world: World) -> Any:
    """Answer one state probe, splitting a trailing `:argument` off the name."""
    probe_name, _, argument = name.partition(":")
    probe = _STATE.get(probe_name.strip())
    if probe is None:
        raise ProbeError(
            f"unknown state probe {probe_name.strip()!r}; the registry answers "
            f"{', '.join(state_names())}"
        )
    return await probe(reader, world, argument.strip() or None)


# ── Output probes ────────────────────────────────────────────────────────────



def _dig(value: Any, path: Sequence[str]) -> Any:
    """Follow a dotted path into a payload, returning `None` at a dead end.

    None rather than raising, because "the field is absent" is a legitimate and
    frequently asserted answer: `last_response.body.match_score is null` is how
    a scenario says a score did not reach the client. A raise would make the
    most interesting assertion in the product unexpressible.
    """
    current: Any = value
    for part in path:
        if isinstance(current, Mapping):
            current = current.get(part, None)
            continue
        if isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
            continue
        return None
    return current


def _observations_for(ctx: ScenarioContext, step: str) -> list[Observation]:
    found = ctx.by_step(step)
    if not found:
        raise ProbeError(
            f"no request was recorded for step {step!r}. Steps that ran: "
            f"{', '.join(sorted({item.step for item in ctx.observations})) or 'none'}"
        )
    return found


def read_output(name: str, ctx: ScenarioContext) -> Any:
    """Answer one output probe over what the client actually received.

    The selectors, and why each exists:

      `status.last`         the last response's status code
      `status.<step>`       that step's single status, or its list when it made
                            several requests. A step that made one request and
                            a step that made three are different shapes, and
                            flattening them would let `== 403` pass because one
                            of three happened to be a 403.
      `statuses.<step>`     always the list, for the three-verb assertions
      `body.<step>.<path>`  a field of that step's LAST response
      `facts.<path>`        whatever the workload deliberately kept
      `trajectory`          the ordered stage names
      `degradations.count`  how many degradations the product admitted to
      `degradations.kinds`  and which
    """
    head, _, rest = name.partition(".")
    head = head.strip()
    if head == "status":
        if rest == "last":
            return ctx.last.status
        found = _observations_for(ctx, rest)
        return found[0].status if len(found) == 1 else [item.status for item in found]
    if head == "statuses":
        return [item.status for item in _observations_for(ctx, rest)]
    if head == "body":
        step, _, path = rest.partition(".")
        found = _observations_for(ctx, step)
        return _dig(found[-1].body, [part for part in path.split(".") if part])
    if head == "facts":
        return _dig(ctx.facts, [part for part in rest.split(".") if part])
    if head == "trajectory":
        return list(ctx.trajectory)
    if head == "degradations":
        if rest == "count":
            return len(ctx.degradations)
        if rest == "kinds":
            return sorted({item.kind for item in ctx.degradations})
        raise ProbeError(f"unknown degradation selector {rest!r}")
    raise ProbeError(
        f"unknown output selector {name!r}; it must start with status, "
        "statuses, body, facts, trajectory or degradations"
    )


# ── Prohibited outcomes ──────────────────────────────────────────────────────
#
# "as load bearing as required ones" (HARNESS.md section 3). Each answers "did
# this happen", and the scenario asserts the absence.


#: EMPTY, and it is a record rather than a placeholder. It held
#: `match_percent`, the Executive Profile Match Score the 2026-09-18 owner
#: amendment let onto the recruiter candidate table. The Vivekium release
#: removed that exception (D3, no number reaches a client with no exception):
#: the AI Match is a grade word. `test_platform_audit.
#: test_no_number_reaches_a_client_with_no_exception` asserts it stays empty,
#: because an exception that is not enumerated is an exception that widens,
#: and one that is enumerated is the first thing the next amendment reaches for.
SANCTIONED_NUMERIC_FIELDS: frozenset[str] = frozenset()

#: DELIBERATELY EMPTY, AND IT IS A RECORD RATHER THAN A PLACEHOLDER.
#:
#: This briefly held `force_rank`, on the argument that section 20.3's
#: force-ranking position is an ORDER rather than a score. The argument was
#: sound; the field was not read by any screen (`grep -rn force_rank
#: frontend/` returns nothing), so the allowlist was widening a detector to
#: let an integer cross a boundary for no reader. The field was removed from
#: `CompetencyOut` instead and this set emptied.
#:
#: Keep it empty. A genuine ordering coordinate belongs here only if
#: something actually renders it and it cannot be rendered any other way,
#: which is how the radar chart's band index earns its place.
ORDER_COORDINATE_FIELDS: frozenset[str] = frozenset()

#: Rule 7. The character is built from its code point rather than typed,
#: because a repository-wide sweep for the character would otherwise rewrite
#: the code that detects it. `test_platform_audit.py` makes the same move.
EM_DASH = chr(8212)

_PAGINATION = re.compile(r"(?:^|_)(?:page|page_size|total_pages)(?:$|_)")


def _strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, Mapping):
        found: list[tuple[str, str]] = []
        for key, child in value.items():
            found.extend(_strings(child, f"{path}.{key}" if path else str(key)))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for index, child in enumerate(value):
            found.extend(_strings(child, f"{path}[{index}]"))
        return found
    return []


def _a_number_reached_a_client(ctx: ScenarioContext) -> tuple[bool, str]:
    """Rule 1, over every payload that actually crossed the boundary.

    TWO HALVES, BOTH BORROWED FROM THE PRODUCT'S OWN ENFORCEMENT rather than
    rewritten here:

      * PROSE is judged by `conversation_guardrails.contains_forbidden_number`,
        which is the guard that already knows an interview question is full of
        legitimate numbers and that a URL is an address rather than a sentence
        about a candidate. Re-deriving that distinction would give the product
        two answers to it, and the one in the harness would be the one nobody
        maintains.
      * SCORE-SHAPED KEYS are judged by `siddhi.numbers.scan`, entered under
        its own `VERBATIM_ROOT`. That is not a trick: under that root the
        walker applies exactly rule 2 (a numeric value under a score-shaped
        key) and skips rule 1, which is what an ordinary API payload needs,
        since `page: 1` and `total: 40` are operational numbers the product
        has always been allowed to return.

    A delivered PRISM Report is judged by the FULL ban instead, through the
    product's own chokepoint, because a report states words and every number in
    one is a violation.
    """
    hits: list[str] = []
    for path, body in ctx.iter_bodies():
        if not isinstance(body, (Mapping, list)):
            if isinstance(body, str) and guard.contains_forbidden_number(body):
                hits.append(f"{path}: prose states a number about an assessment")
            continue
        if "/reports/links/" in path:
            hits.extend(
                f"{path}: {violation}" for violation in numbers.scan(body, path=path)
            )
            continue
        for field_path, text in _strings(body):
            if guard.contains_forbidden_number(text):
                hits.append(f"{path}.{field_path}: {text[:80]!r}")
        for violation in numbers.scan({numbers.VERBATIM_ROOT: body}, path=path):
            leaf = violation.path.rsplit(".", 1)[-1]
            if (
                leaf in SANCTIONED_NUMERIC_FIELDS
                or leaf in ORDER_COORDINATE_FIELDS
                or _PAGINATION.search(leaf)
            ):
                continue
            hits.append(f"{path}: {violation}")
    return bool(hits), "; ".join(hits[:6]) or "no number reached a client payload"


def _an_em_dash_reached_a_client(ctx: ScenarioContext) -> tuple[bool, str]:
    hits = [
        f"{path}.{field}"
        for path, body in ctx.iter_bodies()
        if isinstance(body, (Mapping, list))
        for field, text in _strings(body)
        if EM_DASH in text
    ]
    return bool(hits), "; ".join(hits[:6]) or "no em dash reached a client payload"


def _a_cross_tenant_read_succeeded(ctx: ScenarioContext) -> tuple[bool, str]:
    hits = [
        f"{item.method} {item.path} answered {item.status}"
        for item in ctx.observations
        if item.step == "read_another_tenants_job" and item.status < 400
    ]
    return bool(hits), "; ".join(hits) or "every cross-tenant read was refused"


def _a_cross_tenant_read_answered_403(ctx: ScenarioContext) -> tuple[bool, str]:
    """403 tells the caller the row exists, which 404 does not.

    The rule is that a cross-tenant read answers 404 and NEVER 403 (spec-doc6),
    and the reason is that the status code is itself an oracle: a 403 confirms
    a job id belongs to somebody, which is a fact about another customer.
    """
    hits = [
        f"{item.method} {item.path} answered 403"
        for item in ctx.observations
        if item.step == "read_another_tenants_job" and item.status == 403
    ]
    return bool(hits), "; ".join(hits) or "no cross-tenant read answered 403"


def _any_work_was_dispatched(ctx: ScenarioContext) -> tuple[bool, str]:
    from app.workers import dispatch

    names = dispatch.recorded_names()
    return bool(names), ", ".join(sorted(set(names))) or "nothing was dispatched"


def _a_model_credential_was_configured(ctx: ScenarioContext) -> tuple[bool, str]:
    """HARNESS.md section 1: no model credential is set.

    Asserted as a prohibited OUTCOME rather than checked once at startup,
    because a scenario can reach code that configures one, and a run that
    silently acquired a credential would start making real vendor calls while
    reporting itself as an isolated harness run.
    """
    from app.core.config import get_settings

    settings = get_settings()
    configured = [
        name
        for name in ("openai_gpt_terra", "openai_gpt_luna", "voyage_context_4")
        if (getattr(settings, name, None) or "").strip()
    ]
    return bool(configured), ", ".join(configured) or "no model credential is set"


def _an_automatic_rejection_occurred(ctx: ScenarioContext) -> tuple[bool, str]:
    """No flag ever auto-rejects, and the enforcement is the absence of a writer.

    Two questions, because either alone is satisfiable by a broken product.
    Structurally: `miti.TriangulationResult` must still carry no reject field,
    no status and no decision, so there is nothing for a future caller to set.
    Observationally: nothing in this run's payloads moved an application to
    `rejected` without a person asking for it, which is what the trajectory and
    the responses would show.
    """
    from app.services.miti import triangulation

    fields = {
        name
        for name in dir(triangulation.TriangulationResult)
        if not name.startswith("_")
    }
    structural = sorted(
        name
        for name in fields
        if name in {"reject", "rejected", "decision", "status", "auto_reject"}
    )
    rejected = [
        f"{item.method} {item.path}"
        for item in ctx.observations
        if isinstance(item.body, Mapping) and item.body.get("status") == "rejected"
    ]
    if structural:
        return True, (
            "TriangulationResult has acquired a decision-shaped field: "
            + ", ".join(structural)
        )
    if rejected:
        return True, "an application reached `rejected` in: " + "; ".join(rejected)
    return False, "no automatic rejection occurred and none is expressible"


def _a_fault_went_unrecorded(ctx: ScenarioContext) -> tuple[bool, str]:
    """A fault was injected and the product admitted to nothing.

    HARNESS.md section 4: "Silent survival is a finding, not a pass." Expressed
    as a prohibited outcome as well as an evaluator so a scenario can say it in
    its own ground truth, where a reader of the YAML can see it.
    """
    if not ctx.facts.get("faults_applied"):
        return False, "no fault was injected, so nothing was owed"
    if ctx.degradations:
        return False, f"{len(ctx.degradations)} degradation(s) were recorded"
    return True, (
        "a fault was injected and the run recorded no degradation at all: the "
        "system survived silently, which is a finding rather than a pass"
    )



def _coding_captured(ctx: ScenarioContext, kind: str) -> list[str]:
    capture = ctx.facts.get("coding_capture")
    lines = capture.get(kind) if isinstance(capture, Mapping) else None
    if not isinstance(lines, list) or not lines:
        raise ProbeError(
            f"no {kind.replace('_', ' ')} were captured, so a sweep of them "
            "would be vacuous. The coding workload steps capture them; a "
            "scenario naming this outcome must run those steps."
        )
    return [str(item) for item in lines]


def _sweep(texts: Sequence[tuple[str, str]], sentinels: Sequence[str]) -> list[str]:
    return [
        f"{where} carries {sentinel!r}"
        for where, text in texts
        for sentinel in sentinels
        if sentinel in text
    ]


def _the_answer_key_reached_a_client(ctx: ScenarioContext) -> tuple[bool, str]:
    """No hidden input, hidden output, reference solution or approach note in
    any payload a client received. The approach notes are swept here too: they
    describe the solution, and a candidate who read them would be answering a
    question somebody had already answered for them."""
    sentinels = [
        *_coding_sentinels(ctx.world),
        str(ctx.world.id("coding_approach_sentinel")),
    ]
    texts = [
        (path, body if isinstance(body, str) else json.dumps(body, default=str, ensure_ascii=False))
        for path, body in ctx.iter_bodies()
    ]
    if not texts:
        raise ProbeError("no payload reached a client, so the sweep would be vacuous")
    hits = _sweep(texts, sentinels)
    return bool(hits), "; ".join(hits[:6]) or f"{len(texts)} payload(s) swept, none carries the key"


def _the_answer_key_reached_a_log_line(ctx: ScenarioContext) -> tuple[bool, str]:
    """No part of the key, and no approach note, in any log line the product
    wrote while the coding steps ran, captured at DEBUG."""
    sentinels = [
        *_coding_sentinels(ctx.world),
        str(ctx.world.id("coding_approach_sentinel")),
    ]
    lines = _coding_captured(ctx, "log_lines")
    hits = _sweep([(f"log line {index}", line) for index, line in enumerate(lines)], sentinels)
    return bool(hits), "; ".join(hits[:6]) or f"{len(lines)} log line(s) swept, none carries the key"


def _the_answer_key_reached_a_prompt(ctx: ScenarioContext) -> tuple[bool, str]:
    """No hidden test and no reference solution in any prompt the reviewer was
    sent. The approach notes are ALLOWED here, and only here: they describe
    what the code was meant to do, and the reviewer is told that by design."""
    prompts = _coding_captured(ctx, "prompts")
    hits = _sweep(
        [(f"prompt {index}", text) for index, text in enumerate(prompts)],
        _coding_sentinels(ctx.world),
    )
    return bool(hits), "; ".join(hits[:6]) or f"{len(prompts)} prompt(s) swept, none carries the key"


def _the_answer_key_reached_a_dispatch(ctx: ScenarioContext) -> tuple[bool, str]:
    """No part of the key in the arguments of any task handed to the dispatcher,
    which is a payload that crosses into another process and its logs."""
    from app.workers import dispatch  # noqa: PLC0415

    sentinels = [
        *_coding_sentinels(ctx.world),
        str(ctx.world.id("coding_approach_sentinel")),
    ]
    texts = [
        (item.name, json.dumps([list(item.args), dict(item.kwargs)], default=str))
        for item in dispatch.recorded()
    ]
    hits = _sweep(texts, sentinels)
    return bool(hits), "; ".join(hits[:6]) or f"{len(texts)} dispatch(es) swept, none carries the key"


_PROHIBITED: dict[str, Callable[[ScenarioContext], tuple[bool, str]]] = {
    "a_number_reached_a_client": _a_number_reached_a_client,
    "an_em_dash_reached_a_client": _an_em_dash_reached_a_client,
    "a_cross_tenant_read_succeeded": _a_cross_tenant_read_succeeded,
    "a_cross_tenant_read_answered_403": _a_cross_tenant_read_answered_403,
    "any_work_was_dispatched": _any_work_was_dispatched,
    "a_model_credential_was_configured": _a_model_credential_was_configured,
    "an_automatic_rejection_occurred": _an_automatic_rejection_occurred,
    "a_fault_went_unrecorded": _a_fault_went_unrecorded,
    "the_answer_key_reached_a_client": _the_answer_key_reached_a_client,
    "the_answer_key_reached_a_log_line": _the_answer_key_reached_a_log_line,
    "the_answer_key_reached_a_prompt": _the_answer_key_reached_a_prompt,
    "the_answer_key_reached_a_dispatch": _the_answer_key_reached_a_dispatch,
}


def prohibited_names() -> tuple[str, ...]:
    return tuple(sorted(_PROHIBITED))


def read_prohibited(name: str, ctx: ScenarioContext) -> tuple[bool, str]:
    probe = _PROHIBITED.get(name.strip())
    if probe is None:
        raise ProbeError(
            f"unknown prohibited outcome {name.strip()!r}; the registry answers "
            f"{', '.join(prohibited_names())}"
        )
    return probe(ctx)
