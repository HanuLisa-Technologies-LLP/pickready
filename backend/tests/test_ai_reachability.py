"""Which AI packages a request or a worker can actually reach, made permanent.

RPN-AI-UP-001 W0. This codebase has been burned four times by work that was
green in isolation and unreachable in production: the whole of Part A during
spec-doc5, `probe_llm_models` firing against a deleted module for a release, 19
of 35 live jobs carrying a generation timestamp with zero competency rows, and
the RAG index that nothing has ever written. `claude.md` already names the
cheapest honest answer to "is the framework actually reachable": a grep of
`app/api` and `app/workers` for the package prefixes. This file is that check,
made permanent, made two-directional, and -- the part that matters -- made
TRANSITIVE.

WHY THE GREP IS NOT ENOUGH, AND WHAT IT COST
---------------------------------------------
RPN-AI-UP-001 section 1.1 concludes from that grep that `services/miti` and
`services/siddhi` are unreachable, calls the whole of Part A "an expensive unit
test", and makes W1 a wiring workstream on that basis. It is WRONG, and the
error is in the measurement rather than in the tree.

`workers/tasks.py` registers `pickready.run_functional_assessment`, which calls
`services/functional_assessment.run_assessment`, whose `synthesis_node` calls
`miti.live.evaluate_application` and composes the report through `siddhi`. Miti
and Siddhi are one hop from a worker, not zero, and the grep only ever looked
at depth zero. A second contributing reason is deliberate and documented in
that module: `from app.services.miti import live` is written INSIDE the
function precisely to break an import cycle, so it is invisible to any check
that reads module-level imports only.

So this test walks the real import graph from every module under `app/api` and
`app/workers`, follows imports at any nesting depth, and reports the shortest
path it found. A package is live when a request or a worker can get to it, not
when it happens to be named in those two directories.

TWO DIRECTIONS, BOTH LOAD BEARING
----------------------------------
`LIVE` fails when a package that is supposed to be on the live path loses its
last route to one. That is the regression this exists for.

`NOT_LIVE` fails when a package that is NOT supposed to be live acquires a
route. That direction looks pedantic and is the one that keeps the documents
honest: it is how W2 wiring `services/rag` is forced to move `rag` from one
list to the other in the same change, rather than leaving a standing document
claiming retrieval is dead while it quietly serves traffic. Same shape as
`test_runbook_parity` failing in both directions.

A package in neither list is not asserted about. An entry in either list is a
claim, so it is made deliberately and carries its reason.
"""
from __future__ import annotations

import ast
import collections
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

#: The roots. A module reachable from one of these is reachable from an HTTP
#: request or from a dispatched task, which is the only definition of "live"
#: this project accepts.
ROOT_DIRS = ("api", "workers")

#: Packages that MUST be reachable, each with the route that makes it so. The
#: reason is recorded because a failure here is a question about intent, and
#: the next reader needs to know whether the wiring was removed on purpose.
LIVE: dict[str, str] = {
    "app.services.hiring": (
        "api/jobs.py Gate 1; api/assessments.py; api/dashboard.py; "
        "workers/tasks.py pickready.draft_job_skills -> services/skills -> "
        "hiring.sutra"
    ),
    "app.services.miti": (
        "workers/tasks.py pickready.run_functional_assessment -> "
        "functional_assessment.synthesis_node -> miti.live.evaluate_application. "
        "Wired 2026-08-29; before that its only caller was a script."
    ),
    "app.services.siddhi": (
        "functional_assessment composes the PRISM payload through "
        "siddhi.synthesis behind the citation chokepoint; report_pdf renders it"
    ),
    "app.services.evidence": (
        "api/assessments.py directly, and miti.tiering underneath the scorer"
    ),
    "app.services.agents": (
        "identity, artifacts and gates, through ppi and functional_assessment"
    ),
    "app.services.proctoring": "api/proctoring.py and the assessment gate",
    "app.services.assessment_formats": "the six question formats on the live turn",
    "app.services.projects": "Project Evidence Intelligence, api and worker",
    "app.services.observability": (
        "RPN-AI-UP-001 W4.6, wired 2026-09-09. llm_router.invoke_llm opens a "
        "GenAI span at the one model-call chokepoint, and "
        "_attempt reports the token counts against it. Before that the trace "
        "module was reached only from reasoning/runner.py and recorded nothing."
    ),
    "app.services.assessment_pipeline": (
        "Vivekium release, 2026-09-24. workers/tasks.py "
        "pickready.run_functional_assessment -> functional_assessment "
        "-> assessment_pipeline.evidence, the one per-answer ledger writer, run "
        "as the scoring backfill. The conversation's per-answer call is wired by "
        "the assessment phase."
    ),
    "app.services.coding_assessment": (
        "Phase 4 WP-4B2. workers/coding_tasks.py registers "
        "pickready.execute_coding_submission, pickready.reconcile_coding_submissions, "
        "pickready.probe_code_execution and pickready.verify_code_execution_sandbox, "
        "which call submissions, sweeps and review."
    ),
    "app.services.code_execution": (
        "Phase 4. Reached through coding_assessment from the coding tasks; the "
        "port is the only way a program reaches the sandbox."
    ),
    "app.services.yukti": (
        "Vivekium release, Phase 2. api/jobs.py's ranked table -> "
        "job_candidates -> yukti.ranking (the one rank key and its words), and "
        "workers/tasks.py pickready.run_matching / pickready.yukti_score_profile "
        "-> matching -> yukti.scoring.score_links (the resume reading). It "
        "replaced the retired matcher, hiring.prescreen and services.longevity, "
        "which are deleted rather than left unreachable."
    ),
    "app.services.evidence_retrieval": (
        "PLAN-p5 WP5-E built it; WP5-D wired all three agents (Vivekium "
        "release). pickready.run_functional_assessment -> functional_assessment "
        "-> assessment_pipeline.grading hands transcript_passages_for_skill to "
        "Miti's item stage and assessment_pipeline.composition hands "
        "support_passages_for_statement to Siddhi's support check; the "
        "dispatched question generation (assessment_questions.generate) reads "
        "resume_passages_for_skill per skill and project_evidence_for_candidate."
    ),
    "app.services.tools": (
        "evidence_retrieval calls tools.executor.execute, so the capability "
        "check, the stage policy and the tenant check run on the scoring and "
        "question-writing paths before any row is read. Until the Vivekium "
        "release the package was reachable only because rbac reads the agent "
        "constants, and it guarded a path no route executed."
    ),
    "app.services.rag": (
        "RPN-AI-UP-001 W2, wired 2026-09-09. workers/tasks.py registers "
        "pickready.index_document and pickready.reconcile_context_index, which "
        "call rag.sources.load and rag.index.index_document. Before that the "
        "package was importable from a route and had never executed once."
    ),
}

#: Packages with NO route into them at all. Each entry is a claim about the
#: current tree and names the workstream expected to change it. When one
#: becomes reachable this test fails, and the fix is to MOVE the entry, which
#: is what keeps `claude.md` and this file from disagreeing.
#:
#: The reasoning planner and runner, the orchestration coordinator, router,
#: enforcement and versioning, the experience memory and the agent action
#: ledger used to sit here and in LIVE. They were deleted in the Vivekium
#: release, and `test_unreachable_subsystems_removed.py` keeps them gone: a
#: package that does not exist needs a removal sweep, not a reachability claim.
NOT_LIVE: dict[str, str] = {
    "app.evaluation": (
        "W7.4 requires this in the other direction too: nothing under "
        "app/services may import app/evaluation, and no route or worker may "
        "reach the judges. A judge that could serve a product request would "
        "destroy the closed two-model mapping's whole guarantee."
    ),
}

#: IMPORTABLE IS NOT EXERCISED, AND THE DIFFERENCE IS THE WHOLE POINT OF W2.
#:
#: The first version of this file put `rag` and `tools` in NOT_LIVE and failed,
#: because both ARE reachable in the import graph:
#:
#:   app.api.admin -> services.rbac -> services.tools -> services.tools.
#:   implementations -> services.rag.context
#:
#: `rbac` imports `tools.permissions` for the agent capability declarations,
#: and `tools.implementations` imports `rag.context` to define a handler. Both
#: imports are correct. NEITHER means the code runs on a request: no route
#: calls `tools.execute`, and nothing anywhere calls `rag.index.index_document`,
#: which is why `context_chunks` held zero rows in pilot on the day this was
#: written.
#:
#: Recording them as dead would have been false, and deleting the claim would
#: have lost the finding. So they are recorded HERE, where the assertion is
#: about the entry point that would prove execution rather than about the
#: import graph. `ENTRY_POINTS_WITHOUT_CALLERS` below is the sharp version of
#: the same claim, and it is the one W2 has to change.
#:
#: EMPTY SINCE THE VIVEKIUM RELEASE: `tools` left it when the Evidence RAG
#: reads became the first live `tools.execute` callers (see LIVE). Kept, like
#: `ENTRY_POINTS_WITHOUT_CALLERS`, because it is the shape the next such
#: finding needs, and its check iterates inside the body so an empty dict
#: passes having checked everything there was rather than emitting a skip.
IMPORTED_BUT_NOT_EXERCISED: dict[str, str] = {}

#: The behavioural half: a function that is the ONLY writer of a table and has
#: no caller. `(module, function)` -> the reason it currently has none.
#:
#: This is what "a timestamp is not evidence that work happened" looks like as
#: a source-level check. An import proves a name resolves; a call site proves
#: somebody meant it to run.
#:
#: `rag.index.index_document` WAS the only entry here, and W2 removed it by
#: giving it a caller. The dict is kept, empty, because the check below is the
#: shape the next dead entry point needs and rebuilding it would cost more than
#: the lines it occupies.
ENTRY_POINTS_WITHOUT_CALLERS: dict[tuple[str, str], str] = {
    # The skills contract landed (migration 0118) ahead of its callers, on
    # purpose. `lock_contract` moved to REQUIRED_CALLERS on 2026-09-24 when the
    # conversation start began calling it; the bound read below is reached
    # from `services/vaada_context` (not a route) and from Miti at grading.
    # The owning directory is `app/services`, so this asks about callers in
    # routes, workers and scripts.
    # Phase 4 WP-4B2 landed the coding services ahead of their three callers,
    # each owned by another package: the candidate's submit (Phase 3's respond
    # structured branch), the Run and submission-state routes (Phase 4 WP-4C,
    # now wired and moved to REQUIRED_CALLERS) and the grader (Phase 5's Miti
    # coding sub-stage and scoring hold). The tasks that DO call into the
    # package are in REQUIRED_CALLERS below.
    #
    # `accept_final` is reached through `final_answer.accept_structured_answer`
    # (4C), which lives in the same package, so it has no caller OUTSIDE the
    # package BY DESIGN: the turn engine calls the hook, and the hook calls
    # `accept_final`. The hook and `latest_draft` moved to REQUIRED_CALLERS at
    # the stage 2 integration, when the turn engine started calling them.
    ("app/services/coding_assessment/submissions.py", "accept_final"): (
        "the only writer of coding_submissions, reached only through "
        "final_answer.accept_structured_answer inside its own package."
    ),
    ("app/services/coding_assessment/evidence.py", "evidence_for_answer"): (
        "the per-answer reader. Miti reads the whole conversation once through "
        "`for_conversation` (REQUIRED_CALLERS), so this one has no grader "
        "caller; the recruiter view is the candidate for it."
    ),
    ("app/services/assessment_contract.py", "load_contract_for_conversation"): (
        "Vaada (conversation start) and Miti (grading) both read the contract "
        "bound to the conversation and log its digest; both are wired by the "
        "assessment and grading phases."
    ),
    # ORPHANED BY A DELETION, AND RECORDED RATHER THAN LEFT TO BE FOUND. The
    # reasoning runner was the only thing that built a `RequestTrace`, charged
    # it and persisted it into `agent_execution_traces`. It was unreachable and
    # was deleted in the Vivekium release, so these two lost their only caller
    # in the same change. They are kept because the tool layer the grading
    # phase wires may record through them; if it does not, the module goes.
    ("app/services/observability/trace.py", "persist"): (
        "the only writer of agent_execution_traces; its one caller was the "
        "deleted reasoning runner. Wire it from the tool layer or delete it."
    ),
    ("app/services/observability/trace.py", "add_cost"): (
        "prices a trace from cost_telemetry's per-call usage; its one caller "
        "was the deleted reasoning runner."
    ),
}

#: The positive form of what W2 established, and the regression it guards.
#: `(module, function)` -> where the call must come from.
#:
#: Asserting the caller EXISTS is strictly stronger than asserting the package
#: is importable, and it is the assertion that would have caught the original
#: defect: `services/rag` was importable from `api/admin` for its whole life
#: while `context_chunks` stayed empty in every environment.
REQUIRED_CALLERS: dict[tuple[str, str], str] = {
    # The grading split (PLAN-p5 WP5-D). Each of these guards a path that
    # fails SILENTLY without its caller: scoring would run over an executing
    # coding answer, Miti would grade coding by reading the code, and the
    # support check would never look past the cited answer. The four
    # `evidence_retrieval` entry points live at the `app/services` root, so
    # their callers are "inside the package" to this check by construction;
    # `tests/test_evidence_rag_wiring.py` pins each of those call sites
    # instead.
    ("app/services/coding_assessment/submissions.py", "scoring_hold"): (
        "app/workers/tasks.py, pickready.run_functional_assessment, before the "
        "credit check and before anything is spent."
    ),
    ("app/services/coding_assessment/evidence.py", "for_conversation"): (
        "app/services/assessment_pipeline/evidence.py load_inputs, handed to "
        "Miti's item stage as the 70 / 30 sandbox result per coding answer."
    ),
    ("app/services/siddhi/support.py", "statement_passage_source"): (
        "app/services/assessment_pipeline/composition.py compose. Without it "
        "the support check never looks in the candidate's other answers, so a "
        "true statement pinned to the wrong answer goes to review as "
        "unsupported and nobody is told where the candidate actually said it."
    ),
    ("app/services/siddhi/delivery.py", "gate_delivery"): (
        "app/api/assessments.py download_report_pdf. For its whole life before "
        "the Vivekium release G4 had no production caller, so every flagged "
        "report was downloadable while the gate read as enforced."
    ),
    ("app/services/siddhi/delivery.py", "prism_pdf"): (
        "the same PDF route, with the clearance gate_delivery minted. A route "
        "that called report_pdf.render_report_pdf directly would render a "
        "flagged report with G4 never having run."
    ),
    # The one line that hands a final v2 coding answer to execution (p4-4c
    # hunk 1, wired at the stage 2 integration). Without a caller a final
    # coding answer is stored and never executed, and nothing fails.
    ("app/services/coding_assessment/final_answer.py", "accept_structured_answer"): (
        "app/services/assessment_conversation/turns.py, submit_turn's "
        "structured branch, after the answer row is written."
    ),
    ("app/services/coding_assessment/submissions.py", "latest_draft"): (
        "app/services/assessment_conversation/turns.py: an expired coding turn "
        "with no answer in hand submits the latest Run's code."
    ),
    # A spoken answer's audio whose first deletion could not be confirmed, or
    # whose transcription never reported back. Wired into the hourly
    # recording repair at the stage 2 integration (p3-w5 hunk 2); without it
    # the S3 lifecycle rule on `voice-answers/` is the only backstop.
    ("app/services/assessment_conversation/voice_audio.py", "repair_pending_audio"): (
        "app/workers/tasks_media.py, pickready.reconcile_assessment_recordings."
    ),
    # Phase 4 WP-4B1, wired by the Phase 3 WP2 composer (the p4-4b1 hunk 3,
    # applied at the stage 2 integration). The only writer of an executed
    # coding question and its answer key: without a caller no coding question
    # is ever served, and nothing fails, because the slot quietly becomes prose.
    ("app/services/assessment_formats/coding_generation.py", "write_coding_question"): (
        "app/services/assessment_questions/generate.py, for every coding slot "
        "the budgeted mix allocates."
    ),
    ("app/services/assessment_formats/coding_generation.py", "persist_coding_question"): (
        "app/services/assessment_questions/generate.py, in the transaction "
        "that writes the candidate_questions row, so a question never exists "
        "without its key."
    ),
    ("app/services/assessment_contract.py", "lock_contract"): (
        "app/api/assessment_conversation.py, the first start. The only writer "
        "of job_skill_snapshots after migration 0118, in the transaction that "
        "stamps started_at; without it no skill ever locks (D5) and Miti has "
        "no snapshot to grade against."
    ),
    ("app/services/rag/index.py", "index_document"): (
        "app/workers/tasks.py, from pickready.index_document. Without a caller "
        "the index is never written, and retrieval over an empty table returns "
        "nothing SILENTLY: the lexical retriever ORs its terms and fusion "
        "tolerates an empty list, so it looks like a query with no good match."
    ),
    ("app/services/assessment_pipeline/evidence.py", "backfill_answer_evidence"): (
        "app/services/functional_assessment.py, the scoring pass. It is how an "
        "answer the conversation never filed still reaches Miti's ledger; "
        "without a caller the ledger is written by nothing at scoring time."
    ),
    # Vivekium release, job setup. Each of these is the only door to a state
    # the product depends on: without a caller the SWOT stays `generating`,
    # the skills are never drafted, and a lost draft is never repaired, and
    # every one of those failures is silent because nothing raises.
    ("app/services/swot_analysis.py", "request_generation"): (
        "app/api/assessments.py, POST .../swot-analysis/generate. The only way "
        "a SWOT generation is asked for and handed off after the commit."
    ),
    ("app/services/swot_analysis.py", "run_generation"): (
        "app/workers/tasks.py, pickready.generate_job_swot. The only writer of "
        "a generated SWOT draft."
    ),
    ("app/services/skills.py", "after_swot_saved"): (
        "app/api/assessments.py, the SWOT save and restore routes. The first "
        "human SWOT save is what starts the skills draft."
    ),
    ("app/services/skills.py", "request_draft"): (
        "app/workers/tasks.py, pickready.reconcile_job_setup. The repair path "
        "for a draft that never landed."
    ),
    ("app/services/rag/repair.py", "repair"): (
        "app/workers/tasks_retrieval.py, from pickready.repair_semantic_index. "
        "Without it a chunk written with a NULL vector during an embedding "
        "outage, or embedded by a retired model, stays keyword-only for ever: "
        "the reconcile sweep sees a document that HAS chunks and moves on."
    ),
    # Phase 4 WP-4B2: the coding tasks. Without the first, a final coding
    # answer is stored and never executed; without the others, a lost
    # dispatch is never repaired and a sandbox outage pages nobody.
    ("app/services/coding_assessment/submissions.py", "execute_submission"): (
        "app/workers/coding_tasks.py, pickready.execute_coding_submission."
    ),
    ("app/services/coding_assessment/sweeps.py", "probe"): (
        "app/workers/coding_tasks.py, pickready.probe_code_execution."
    ),
    ("app/services/coding_assessment/sweeps.py", "verify_sandbox"): (
        "app/workers/coding_tasks.py, pickready.verify_code_execution_sandbox."
    ),
    # Vivekium release, Phase 2. The two doors to the ranked table: without a
    # caller of `score_links` no link is ever read and every row stays "Not
    # checked yet"; without a caller of `order_by_sql` the page is ordered by
    # something other than the one key the dashboard and the ranked table
    # share, which is the drift the derived rank exists to prevent.
    ("app/services/yukti/scoring.py", "score_links"): (
        "app/services/matching.py, from run_matching and score_profile "
        "(pickready.run_matching and pickready.yukti_score_profile)."
    ),
    ("app/services/yukti/ranking.py", "order_by_sql"): (
        "app/services/job_candidates.py, the ranked table's ORDER BY."
    ),
    ("app/services/yukti/ranking.py", "rank_score_sql"): (
        "app/services/job_candidates.py and app/services/dashboard.py: the "
        "ranked table and the Candidate Dashboard read one key."
    ),
    # Phase 4 WP-4F: the code-execution port's two doors. Every program that
    # reaches the sandbox asks `get_provider` for it, and every answer is
    # judged by `outputs_match` in the application, never by the sandbox.
    # Without a caller of the first, the package is importable and runs
    # nothing; without the second, a hidden test is graded somewhere else.
    ("app/services/code_execution/provider.py", "get_provider"): (
        "app/services/coding_assessment/runs.py (Run) and submissions.py "
        "(the final answer), both reached from app/api/assessment_coding.py "
        "and app/workers/coding_tasks.py."
    ),
    ("app/services/code_execution/provider.py", "outputs_match"): (
        "app/services/coding_assessment/execution.py and keys.py, the only "
        "places a program's output is compared with an expected one."
    ),
    # Phase 4 WP-4C: the candidate's Run button and the final answer's state.
    # Without the first two the editor can only fail; without the third the
    # candidate cannot tell a stored final answer from a lost one.
    ("app/services/coding_assessment/runs.py", "start_run"): (
        "app/api/assessment_coding.py, POST .../coding/{qid}/runs. The only "
        "writer of coding_runs."
    ),
    ("app/services/coding_assessment/runs.py", "refresh_run"): (
        "app/api/assessment_coding.py, GET .../runs/{run_id}. Without it a "
        "queued run is never collected and the button spins until its deadline."
    ),
    ("app/services/coding_assessment/runs.py", "candidate_results"): (
        "app/api/assessment_coding.py, the Run poll route's response."
    ),
    ("app/services/coding_assessment/submissions.py", "candidate_state_word"): (
        "app/api/assessment_coding.py, GET .../coding/{qid}/submission."
    ),
    ("app/services/coding_assessment/transcript.py", "recruiter_views"): (
        "app/api/assessments.py, the recruiter transcript. Without it an "
        "executed coding answer reads as nothing beside the candidate's code."
    ),
    ("app/services/rag/sources.py", "pending"): (
        "app/workers/tasks.py, from pickready.reconcile_context_index. This is "
        "the sweep that asks the TABLE which documents have no chunks. Without "
        "it, a dispatch that never arrived leaves a resume invisible to "
        "retrieval forever, because nothing would ever ask again."
    ),
}


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _source_files() -> dict[str, pathlib.Path]:
    return {
        _module_name(p): p for p in APP.rglob("*.py") if "__pycache__" not in p.parts
    }


def _imports(path: pathlib.Path, this_module: str, known: set[str]) -> set[str]:
    """Every `app.*` module this file imports, at ANY nesting depth.

    Function-level imports are followed deliberately. This codebase uses them
    to break real import cycles -- `functional_assessment.synthesis_node`
    imports `miti.live` inside the function and says why -- so a check reading
    module-level imports only would report the live scorer as dead. That is
    precisely the mistake this file exists to correct.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    def _record(candidate: str) -> None:
        # `from app.services.miti import live` may name a module or a symbol.
        # Only real modules are recorded; the package itself is recorded
        # separately, because importing a symbol still creates the dependency.
        if candidate in known:
            found.add(candidate)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("app"):
                    continue
                _record(alias.name)
                parts = alias.name.split(".")
                for i in range(2, len(parts)):
                    _record(".".join(parts[:i]))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                pkg_parts = this_module.split(".")
                # A package's `__init__` is its own package; a module's package
                # is its parent.
                base_parts = pkg_parts if path.name == "__init__.py" else pkg_parts[:-1]
                if node.level > 1:
                    base_parts = base_parts[: len(base_parts) - (node.level - 1)]
                tail = node.module.split(".") if node.module else []
                base = ".".join([*base_parts, *tail])
            elif node.module and node.module.startswith("app"):
                base = node.module
            else:
                continue
            if not base.startswith("app"):
                continue
            _record(base)
            for alias in node.names:
                _record(f"{base}.{alias.name}")
    return found


def _reachable_with_paths() -> dict[str, list[str]]:
    """Breadth-first from every route and worker module.

    Breadth-first rather than depth-first so the recorded path is the SHORTEST
    one, which is what makes a failure message useful: "miti is reached in two
    hops through functional_assessment" is actionable, and a forty-module chain
    is not."""
    files = _source_files()
    known = set(files)
    roots = [
        name
        for name, path in files.items()
        if len(path.relative_to(APP).parts) > 1
        and path.relative_to(APP).parts[0] in ROOT_DIRS
    ]
    assert roots, "No route or worker modules found; the layout changed."

    paths: dict[str, list[str]] = {r: [r] for r in roots}
    queue = collections.deque(roots)
    while queue:
        current = queue.popleft()
        path = files.get(current)
        if path is None:
            continue
        for target in _imports(path, current, known):
            if target not in paths:
                paths[target] = [*paths[current], target]
                queue.append(target)
    return paths


@pytest.fixture(scope="module")
def reachable() -> dict[str, list[str]]:
    return _reachable_with_paths()


def _hits(reachable: dict[str, list[str]], package: str) -> dict[str, list[str]]:
    return {
        module: path
        for module, path in reachable.items()
        if module == package or module.startswith(f"{package}.")
    }


@pytest.mark.parametrize("package", sorted(LIVE))
def test_a_live_package_is_reachable_from_a_route_or_a_worker(
    package: str, reachable: dict[str, list[str]]
) -> None:
    hits = _hits(reachable, package)
    assert hits, (
        f"{package} is NOT reachable from app/api or app/workers.\n"
        f"It is supposed to be live because: {LIVE[package]}\n"
        "Either the wiring was removed, in which case restore it, or the "
        "package genuinely left the live path, in which case move it to "
        "NOT_LIVE in this file and correct claude.md in the SAME change. A "
        "standing document that misstates whether a subsystem is live is worse "
        "than no document."
    )


@pytest.mark.parametrize("package", sorted(NOT_LIVE))
def test_a_package_recorded_as_dead_has_not_quietly_become_live(
    package: str, reachable: dict[str, list[str]]
) -> None:
    hits = _hits(reachable, package)
    if not hits:
        return
    shortest = min(hits.values(), key=len)
    pytest.fail(
        f"{package} IS now reachable from app/api or app/workers, and this "
        "file still records it as dead.\n"
        f"  shortest path: {' -> '.join(shortest)}\n"
        f"  recorded reason for being dead: {NOT_LIVE[package]}\n"
        "If this is the workstream that wires it, move the entry to LIVE with "
        "the route that makes it live, and update claude.md in the same "
        "change. If it is accidental, the import is the defect."
    )


def test_a_package_recorded_as_importable_but_unexercised_is_still_importable(
    reachable: dict[str, list[str]]
) -> None:
    """The weaker claim, asserted so the stronger one below stays meaningful.

    If one of these loses its import path entirely it has become genuinely
    dead, which is a different fact from the one recorded here and belongs in
    NOT_LIVE. Asserting it keeps the two lists from silently swapping.

    Iterated inside the body, not parametrised: the dict is empty today, and a
    parametrised test over an empty collection is a SKIPPED placeholder."""
    for package, reason in sorted(IMPORTED_BUT_NOT_EXERCISED.items()):
        assert _hits(reachable, package), (
            f"{package} is no longer reachable at all. It was recorded as "
            f"importable but unexercised: {reason}\n"
            "Move it to NOT_LIVE, or restore the import."
        )


def _callers_of(relative: str, function: str) -> list[str]:
    """Every CALL site of `function` outside the package that defines it.

    Calls, not imports and not re-exports: `rag/__init__.py` names
    `index_document` in its `__all__`, and that is not somebody running it.

    Calls from inside the owning package are excluded too. A subsystem calling
    its own function proves the subsystem is internally consistent, which is
    not the question. The question is whether anything OUTSIDE it ever runs.
    """
    owning_dir = pathlib.Path(relative).parent.as_posix()
    callers: list[str] = []
    for module, path in _source_files().items():
        if path.relative_to(BACKEND).as_posix().startswith(owning_dir):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name == function:
                callers.append(f"{module}:{node.lineno}")
    return callers


def test_every_function_recorded_as_uncalled_still_has_no_caller() -> None:
    """A recorded dead entry point has not quietly been wired.

    The mirror of the test below. It is what forced this file to be updated
    when W2 gave `index_document` its first caller: a finding must not outlive
    the defect it describes.

    NOT PARAMETRISED, and the reason is worth the line. `ENTRY_POINTS_WITHOUT_
    CALLERS` is legitimately empty right now, and `@parametrize` over an empty
    collection emits a SKIPPED placeholder rather than nothing. The skip
    inventory then reports an undeclared skip, and the only ways to satisfy it
    are to declare a skip that is not really a skip or to delete the check --
    both of which trade a real assertion for a green summary line. Iterating
    inside the body means an empty dict is a test that passes having checked
    everything there was to check."""
    for target, reason in sorted(ENTRY_POINTS_WITHOUT_CALLERS.items()):
        callers = _callers_of(*target)
        assert not callers, (
            f"{target[0]}::{target[1]} now HAS callers: {callers}\n"
            f"It was recorded as uncalled because: {reason}\n"
            "If this is the workstream that wires it, move the entry to "
            "REQUIRED_CALLERS and assert the new behaviour instead."
        )


@pytest.mark.parametrize("target", sorted(REQUIRED_CALLERS), ids=lambda t: f"{t[1]}")
def test_a_function_that_must_be_called_still_is(target: tuple[str, str]) -> None:
    """The regression guard for everything W2 wired.

    Strictly stronger than "the package is importable", and it is the check
    that would have caught the original defect years earlier: `services/rag`
    was importable from `api/admin` the whole time `context_chunks` sat empty
    in every environment.

    It deliberately does not assert WHICH module calls it -- moving a call from
    `tasks.py` to a new module is a refactor, not a regression. What must not
    happen is the call disappearing."""
    callers = _callers_of(*target)
    assert callers, (
        f"{target[0]}::{target[1]} has NO caller outside its own package.\n"
        f"It must be called from: {REQUIRED_CALLERS[target]}\n"
        "A function with no caller does not fail, it does nothing, and the "
        "table it was supposed to write stays empty while every test passes."
    )


def test_miti_and_siddhi_are_reached_through_the_assessment_worker(
    reachable: dict[str, list[str]],
) -> None:
    """The specific claim RPN-AI-UP-001 section 1.1 gets wrong, pinned.

    Recorded as its own test rather than folded into the parametrised one
    because the PATH is the finding. If a future change moves scoring off
    `functional_assessment`, this fails carrying the path that used to hold,
    and the next reader can see exactly what was true before."""
    for package in ("app.services.miti", "app.services.siddhi"):
        hits = _hits(reachable, package)
        assert hits, f"{package} unreachable; see the parametrised test above."
        shortest = min(hits.values(), key=len)
        assert len(shortest) > 1, f"{package} appears to BE a route or worker module."
        assert shortest[0].startswith(("app.api", "app.workers")), (
            f"{package} reached, but the path does not start at a route or a "
            f"worker: {shortest}"
        )
