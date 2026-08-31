"""Re-embedding: the shadow swap, the provenance, the retry, and the migration.

WHAT THESE PROVE AND WHAT THEY DO NOT
--------------------------------------
Every test here runs against recorded fixtures and a stub embedder. They prove
the CODE'S logic: the resume predicate, the batching, the dimensionality check,
that the swap and the provenance stamp are one statement, that a credential
failure stops on the first attempt while a 429 does not, and that the retrieval
scorer computes recall and reciprocal rank correctly.

They prove nothing about the vendor. There is no Anthropic or Voyage credential
in this phase (spec-doc6 decision D6), so no request has been made to either
API, and no wording here or in the modules under test may imply otherwise. The
outstanding claims are listed in `VERIFICATION_PENDING.md` with the exact
command that would settle each one.

THE MIGRATION IS TESTED BY READING ITS SOURCE
-----------------------------------------------
`0062_embedding_provenance` claims to be safe under a rolling deploy. That
claim rests on the SHAPE of its statements, so the test reads them: no DROP
COLUMN, no SET NOT NULL, no ALTER TYPE, no index, no default, and the only
constraint change relaxes a rule rather than tightening one. Reading the source
is what turns the paragraph in the migration's docstring into something that
fails when it stops being true.
"""
from __future__ import annotations

import json
import math
import pathlib
import re

import httpx
import pytest

from app.config import llm_providers
from app.scripts import reembed

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "reembed"
SNAPSHOT = json.loads((FIXTURES / "schema_snapshot.json").read_text(encoding="utf-8"))
VECTOR_COLUMNS = SNAPSHOT["vector_columns"]

MIGRATION = (
    pathlib.Path(reembed.__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0062_embedding_provenance.py"
)
MIGRATION_SOURCE = MIGRATION.read_text(encoding="utf-8")
UPGRADE_SOURCE = MIGRATION_SOURCE.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]


# ── The migration is expand-only ─────────────────────────────────────────────


def test_the_upgrade_contains_no_destructive_statement() -> None:
    """Expand-only, so old and new code can run against it at the same time.

    A DROP COLUMN breaks the previous image mid-deploy. A SET NOT NULL breaks
    any insert the previous image makes without the column. An ALTER TYPE
    rewrites the table under a lock. None of them appear.
    """
    upper = UPGRADE_SOURCE.upper()
    for forbidden in ("DROP COLUMN", "SET NOT NULL", "ALTER TYPE", "CREATE INDEX", "DROP TABLE"):
        assert forbidden not in upper, f"the upgrade contains {forbidden}"


def test_every_added_column_is_nullable_with_no_default() -> None:
    """Nullable and defaultless means no table rewrite and no broken insert.

    A column with a default forces Postgres to consider a rewrite on older
    versions and, more importantly here, makes the provenance column claim a
    value nobody measured. NULL is the honest record of "produced by an
    unrecorded model", and it is exactly the predicate the script selects on.
    """
    for fragment in re.findall(r"ADD COLUMN IF NOT EXISTS [^,\n]+", UPGRADE_SOURCE):
        assert "NOT NULL" not in fragment.upper(), fragment
        assert "DEFAULT" not in fragment.upper(), fragment


def test_the_only_constraint_change_relaxes_a_rule() -> None:
    """CASCADE to SET NULL strictly REDUCES what a concurrent delete removes.

    That is the direction that is safe to apply before the new code ships: an
    old-image process deleting an evaluation between the migration and its own
    restart destroys less than it did before, never more.
    """
    assert "ON DELETE SET NULL" in UPGRADE_SOURCE
    assert "ON DELETE CASCADE" not in UPGRADE_SOURCE
    assert UPGRADE_SOURCE.count("DROP NOT NULL") == 2


def test_the_backfill_runs_before_the_cascade_is_relaxed() -> None:
    """The context can only be copied while the evaluation is still there."""
    backfill = UPGRADE_SOURCE.index("UPDATE review_dispositions d")
    relax = UPGRADE_SOURCE.index("DROP NOT NULL")
    assert backfill < relax


def test_the_downgrade_refuses_to_invent_a_reference() -> None:
    """Restoring the CASCADE and the NOT NULL cannot coexist with a detached
    row, and the migration says so by deleting only rows IT detached rather
    than by fabricating an evaluation id."""
    down = MIGRATION_SOURCE.split("def downgrade()", 1)[1]
    assert "detached_at IS NOT NULL" in down


# ── Targets cover the schema ─────────────────────────────────────────────────


def test_every_vector_column_in_the_schema_has_a_target() -> None:
    """Leaving one out leaves the same ambiguity alive on that column, which is
    the whole thing this script exists to end."""
    recorded = {(c["table"], c["column"]) for c in VECTOR_COLUMNS}
    covered = {(t.table, t.column) for t in reembed.TARGETS}
    assert recorded == covered


def test_every_vector_column_is_the_platform_width() -> None:
    for column in VECTOR_COLUMNS:
        assert column["type"] == f"vector({reembed.EMBEDDING_DIM})", column


def test_the_two_job_vectors_are_tracked_separately() -> None:
    """`jobs.embedding` ranks candidates against a JD and `jobs.reach_embedding`
    ranks roles against each other. They were deliberately kept as separate
    columns, so re-embedding one must never appear to vouch for the other."""
    jobs = [t for t in reembed.TARGETS if t.table == "jobs"]
    assert {t.column for t in jobs} == {"embedding", "reach_embedding"}
    assert len({t.model_column for t in jobs}) == 2
    assert len({t.shadow for t in jobs}) == 2


def test_an_unknown_target_refuses() -> None:
    with pytest.raises(reembed.ReembedRefused):
        reembed.target_for("profiles.nonexistent")


# ── The resume predicate and idempotency ─────────────────────────────────────


def test_a_row_stamped_with_the_current_contract_is_not_stale() -> None:
    """Idempotency is a property of the ROWS, not of a progress file somebody
    has to keep in step. A run that died half way leaves the shadow written for
    what it finished, and the next run selects exactly the remainder."""
    target = reembed.target_for("profiles.embedding")
    stale = reembed.stale_predicate(target)
    assert "embedding_model IS DISTINCT FROM :model" in stale
    assert "embedding_contract_version IS DISTINCT FROM :contract" in stale
    assert "p.embedding IS NOT NULL" in stale


def test_the_resume_predicate_skips_rows_already_shadowed() -> None:
    target = reembed.target_for("profiles.embedding")
    assert "embedding_shadow IS NULL" in reembed.pending_predicate(target)


def test_a_row_with_no_vector_is_not_this_script_s_work() -> None:
    """The matching pipeline backfills a NULL vector on demand. Claiming those
    here would make the work plan report a number nobody asked for."""
    for target in reembed.TARGETS:
        assert f"{target.column} IS NOT NULL" in reembed.stale_predicate(target)


def test_the_batch_query_is_ordered_by_primary_key() -> None:
    """Two runs must walk the table the same way, or a resumed run re-reads
    rows it already paid the vendor for."""
    for target in reembed.TARGETS:
        assert "ORDER BY" in reembed.select_batch_sql(target)


# ── The shadow swap ──────────────────────────────────────────────────────────


def test_the_write_only_touches_the_shadow() -> None:
    """Writing straight into the live column leaves a half-embedded index
    serving two vector spaces the moment a run fails part way."""
    for target in reembed.TARGETS:
        sql = reembed.write_shadow_sql(target)
        assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        assert assignments.startswith(f"{target.shadow} = ")
        assert target.model_column not in assignments
        assert f"{target.column} =" not in assignments


def test_the_swap_moves_the_vector_and_stamps_the_provenance_at_once() -> None:
    """Stamping the model while the live column still holds the old vector
    would make the provenance column lie for the whole run, and a crash in the
    middle would leave it lying permanently."""
    for target in reembed.TARGETS:
        sql = reembed.swap_sql(target)
        assert sql.count("UPDATE") == 1
        assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        assert f"{target.column} = " in assignments
        assert f"{target.model_column} = :model" in assignments
        assert f"{target.contract_column} = :contract" in assignments
        assert f"{target.generated_column} = now()" in assignments
        assert f"{target.shadow} = NULL" in assignments


def test_the_dimensionality_check_is_a_second_independent_check() -> None:
    """`embeddings.embed` already refuses a malformed batch. This is the check
    that would catch a shadow written by an older version of this script with a
    different output dimension."""
    sql = reembed.shadow_ready_sql(reembed.target_for("profiles.embedding"))
    assert "vector_dims" in sql
    assert f"= {reembed.EMBEDDING_DIM}" in sql


def test_the_swap_is_scoped_to_one_tenant() -> None:
    for target in reembed.TARGETS:
        if target.tenant_column:
            assert ":tenant" in reembed.swap_sql(target)


def test_the_jd_text_excludes_compensation() -> None:
    """An embedding is a model prompt, and ESD 16 keeps salary out of every
    model prompt."""
    for target in reembed.TARGETS:
        assert "compensation" not in target.text_sql.lower()
        assert "salary" not in target.text_sql.lower()
        assert "ctc" not in target.text_sql.lower()


def test_the_text_is_rebuilt_from_the_row_not_from_a_python_builder() -> None:
    """Re-embedding must be reproducible from the database alone. A builder in
    Python means the text depends on an application module that may have moved
    since the vector was written, and the contract version could not then be
    trusted to describe it."""
    for target in reembed.TARGETS:
        assert target.text_sql.strip()
        assert "(" in target.text_sql or "." in target.text_sql


# ── Retry, backoff and classification come from the router ───────────────────


class _Response:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}


def _http_error(status: int, headers: dict[str, str] | None = None) -> Exception:
    request = httpx.Request("POST", "https://api.voyageai.com/v1/embeddings")
    return httpx.HTTPStatusError(
        f"{status}", request=request, response=httpx.Response(status, headers=headers or {})
    )


@pytest.mark.asyncio
async def test_a_credential_failure_stops_on_the_first_attempt() -> None:
    """No amount of waiting fixes a revoked key. Same rule the router follows,
    and it is why the breaker trips on the first occurrence there too."""
    calls = 0

    async def failing(_batch):
        nonlocal calls
        calls += 1
        raise _http_error(401)

    async def _no_sleep(_seconds):
        return None

    with pytest.raises(reembed.ReembedRefused) as raised:
        await reembed.embed_with_backoff(["a"], embedder=failing, sleep=_no_sleep)
    assert calls == 1
    assert "credential" in str(raised.value)
    assert "Nothing was swapped" in str(raised.value)


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_on_the_router_s_curve() -> None:
    waits: list[float] = []
    calls = 0

    async def flaky(batch):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _http_error(429)
        return [[0.0] * reembed.EMBEDDING_DIM for _ in batch]

    async def _sleep(seconds):
        waits.append(seconds)

    result = await reembed.embed_with_backoff(["a"], embedder=flaky, sleep=_sleep)
    assert calls == 3
    assert len(result) == 1
    assert waits == [
        llm_providers.backoff_seconds(2),
        llm_providers.backoff_seconds(3),
    ]


@pytest.mark.asyncio
async def test_the_vendor_s_own_retry_after_wins_over_the_local_curve() -> None:
    """Strictly better information than any local backoff curve, and honouring
    it is the difference between backing off and guessing."""
    waits: list[float] = []
    calls = 0

    async def flaky(batch):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429, {"retry-after": "2"})
        return [[0.0] * reembed.EMBEDDING_DIM for _ in batch]

    async def _sleep(seconds):
        waits.append(seconds)

    await reembed.embed_with_backoff(["a"], embedder=flaky, sleep=_sleep)
    assert waits == [2.0]


@pytest.mark.asyncio
async def test_a_retry_after_longer_than_the_cap_is_bounded() -> None:
    waits: list[float] = []
    calls = 0

    async def flaky(batch):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429, {"retry-after": "9999"})
        return [[0.0] * reembed.EMBEDDING_DIM for _ in batch]

    async def _sleep(seconds):
        waits.append(seconds)

    await reembed.embed_with_backoff(["a"], embedder=flaky, sleep=_sleep)
    assert waits == [llm_providers.BACKOFF_MAX_SECONDS]


@pytest.mark.asyncio
async def test_a_transient_failure_eventually_gives_up_and_swaps_nothing() -> None:
    async def always(_batch):
        raise _http_error(503)

    async def _sleep(_seconds):
        return None

    with pytest.raises(reembed.ReembedRefused) as raised:
        await reembed.embed_with_backoff(["a"], embedder=always, sleep=_sleep)
    assert "provider_error" in str(raised.value)


def test_the_backoff_curve_is_the_router_s_and_not_a_second_copy() -> None:
    """spec-doc6 section 10.1 rule 12: one implementation per concept. The
    curve, its cap and the failure classification all live in the routing
    policy, so this module must not define its own."""
    source = pathlib.Path(reembed.__file__).read_text(encoding="utf-8")
    assert "backoff_seconds" in source
    assert "BACKOFF_MAX_SECONDS" in source
    assert not re.search(r"^\s*BACKOFF_BASE", source, re.MULTILINE)
    assert "2 **" not in source, "a second exponential curve"


# ── The retrieval evaluation set and its scorer ──────────────────────────────


def _bag_of_words(text: str) -> list[float]:
    """A deterministic hashing embedder. A TEST DOUBLE, and it is not a model.

    It has enough geometry for the scorer's arithmetic to be checkable, and no
    semantics at all, which is why the CLI refuses `--confirm` without a real
    credential rather than falling through to something like this.
    """
    import hashlib

    vector = [0.0] * reembed.EMBEDDING_DIM
    for token in text.lower().split():
        index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
        vector[index % reembed.EMBEDDING_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


async def _stub_embedder(batch):
    return [_bag_of_words(item) for item in batch]


def test_the_evaluation_set_meets_the_specified_size() -> None:
    payload = reembed.load_eval_set()
    assert len(payload["pairs"]) >= 50
    assert len(payload["documents"]) >= 20


def test_a_short_evaluation_set_refuses(tmp_path: pathlib.Path) -> None:
    """A smaller set makes a recall figure that moves on one document look like
    a quality change."""
    path = tmp_path / "small.json"
    path.write_text(
        json.dumps(
            {
                "documents": [{"id": "a", "text": "one"}],
                "pairs": [{"query": "one", "expected": ["a"]}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(reembed.ReembedRefused) as raised:
        reembed.load_eval_set(path)
    assert "at least 50" in str(raised.value)


def test_a_pair_expecting_a_document_that_does_not_exist_refuses(
    tmp_path: pathlib.Path,
) -> None:
    payload = reembed.load_eval_set()
    payload["pairs"][0]["expected"] = ["a-document-nobody-wrote"]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(reembed.ReembedRefused) as raised:
        reembed.load_eval_set(path)
    assert "a-document-nobody-wrote" in str(raised.value)


@pytest.mark.asyncio
async def test_a_perfect_embedder_scores_one_and_a_useless_one_does_not() -> None:
    """The scorer is checked in both directions, because a metric that always
    returns a good number is indistinguishable from a good model."""
    documents = [{"id": str(i), "text": f"token{i}"} for i in range(60)]
    pairs = [{"query": f"token{i}", "expected": [str(i)]} for i in range(60)]
    payload = {"documents": documents, "pairs": pairs}

    # A one-hot embedder, so the expected document is uniquely nearest and both
    # metrics must be exactly 1.0. Deliberately not the hashing double used
    # elsewhere: that one collides occasionally, which is a property of the
    # double rather than of the scorer, and a test that tolerated the collision
    # would also tolerate a scorer that ranked slightly wrong.
    async def one_hot(batch):
        vectors = []
        for item in batch:
            index = int(item.replace("token", ""))
            vector = [0.0] * reembed.EMBEDDING_DIM
            vector[index] = 1.0
            vectors.append(vector)
        return vectors

    perfect = await reembed.score_retrieval(one_hot, payload=payload)
    assert perfect.recall_at_k == 1.0
    assert perfect.mean_reciprocal_rank == 1.0

    async def useless(batch):
        return [[1.0] + [0.0] * (reembed.EMBEDDING_DIM - 1) for _ in batch]

    flat = await reembed.score_retrieval(useless, payload=payload)
    assert flat.recall_at_k < 0.2


@pytest.mark.asyncio
async def test_the_scorer_runs_over_the_real_evaluation_set() -> None:
    """Proves the harness is wired to the committed set and produces a figure.

    The NUMBER here is a property of a hashing test double, not of
    voyage-context-4, and it is deliberately not asserted against a quality
    threshold. The real before and after figures need a Voyage credential and
    are listed in VERIFICATION_PENDING.md.
    """
    score = await reembed.score_retrieval(_stub_embedder)
    assert score.pairs >= 50
    assert 0.0 <= score.recall_at_k <= 1.0
    assert 0.0 <= score.mean_reciprocal_rank <= 1.0
    assert score.k == reembed.RECALL_AT


def test_cosine_of_orthogonal_vectors_is_zero() -> None:
    assert reembed.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert reembed.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert reembed.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ── The refusal that keeps this honest ───────────────────────────────────────


@pytest.mark.asyncio
async def test_running_without_a_voyage_key_refuses(monkeypatch) -> None:
    """The single worst outcome available here is writing the deterministic dev
    fallback into every profile and stamping it voyage-context-4: retrieval
    returns confident nonsense, and the provenance column built to make this
    answerable asserts that a real model produced it."""
    plan = reembed.Plan(statuses=[], keys_present=False)

    async def _collect(_session):
        return plan

    monkeypatch.setattr(reembed, "collect_status", _collect)
    monkeypatch.setattr(reembed, "_tenants", lambda _session: _empty())
    monkeypatch.setattr(reembed, "superadmin_scope", _NoopScope)

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return None

        async def __aexit__(self, *_):
            return False

    with pytest.raises(reembed.ReembedRefused) as raised:
        await reembed.run(_Factory(), apply=True)
    assert "VOYAGE_CONTEXT_4" in str(raised.value)
    assert "voyage-context-4" in str(raised.value)


async def _empty():
    return []


class _NoopScope:
    def __init__(self, _session):
        pass

    async def __aenter__(self):
        return None

    async def __aexit__(self, *_):
        return False


def test_the_plan_rounds_provider_round_trips_up() -> None:
    target = reembed.target_for("profiles.embedding")
    plan = reembed.Plan(
        statuses=[
            reembed.TargetStatus(
                target=target,
                tenant="t",
                tenant_name="Acme",
                pending=reembed.BATCH_ROWS + 1,
            )
        ]
    )
    assert plan.batches == 2
    rendered = reembed.render_plan(plan)
    assert "estimates, not a quotation" in rendered
    assert "credentials present       : no" in rendered


def test_stale_rows_with_no_source_text_are_reported_not_retried_forever() -> None:
    target = reembed.target_for("profiles.embedding")
    plan = reembed.Plan(
        statuses=[
            reembed.TargetStatus(
                target=target, tenant="t", tenant_name="Acme", without_text=3
            )
        ]
    )
    assert "cannot be re-embedded" in reembed.render_plan(plan)
    assert "Acme: 3" in reembed.render_plan(plan)


def test_only_the_permitted_model_strings_appear() -> None:
    source = pathlib.Path(reembed.__file__).read_text(encoding="utf-8")
    for match in re.findall(r"claude-[a-z0-9.\-]+|voyage-[a-z0-9.\-]+", source):
        assert match in {
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
            "voyage-context-4",
        }, match
