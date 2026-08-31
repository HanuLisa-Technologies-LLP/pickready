"""The one embedding client: width, ordering, and the fallback's honesty.

THE WIDTH IS THE SCHEMA, and that is the first thing asserted here. Every
`vector(N)` column in the database is `vector(1024)`, and a mismatch does not
raise at write time on every backend -- it raises at QUERY time, on a machine
that is not the one where somebody changed the constant. So the constant is
compared against the model definitions rather than trusted.

THE ORDERING IS A DATA-CORRUPTION SURFACE. Voyage returns `data` with an
explicit `index`, and a batch that arrived out of order and was trusted would
attach every candidate's vector to the next candidate's row. Nothing about that
looks wrong afterwards: retrieval keeps working, it is simply ranking the wrong
people. It is the kind of defect no happy-path test ever shows, so it is tested
directly with a deliberately shuffled response.
"""
from __future__ import annotations

import pathlib

import pytest

from app.config import llm_providers
from app.services import embeddings


class _Settings:
    voyage_context_4 = "vk-test"


class _NoKey:
    voyage_context_4 = ""


def test_the_embedding_credential_is_read_from_exactly_one_named_variable() -> None:
    """`VOYAGE_CONTEXT_4`, written as a literal, because the hole a rename would
    open is SILENT.

    `embeddings.embed` falls back to deterministic pseudo-random unit vectors
    when the key is absent, so reading a variable nobody sets returns
    meaningless vectors of the right width: no exception, no log line, no empty
    result, and retrieval that has quietly stopped meaning anything. A test that
    derived the name from the setting would agree with any rename, including the
    one that reopens this.

    The old name is asserted GONE in the same breath. An alias left beside the
    canonical name is two names for one credential, and the one that is set is
    then a matter of which file somebody read.
    """
    from app.core.config import Settings

    # pydantic-settings maps a field to the env var of the same name,
    # case-insensitively, so the field name IS the contract with the
    # environment.
    assert "VOYAGE_CONTEXT_4".lower() in Settings.model_fields
    assert "voyage_api_key" not in Settings.model_fields
    assert embeddings.EMBEDDING_MODEL == "voyage-context-4"


# ── Width ────────────────────────────────────────────────────────────────────


def test_the_embedding_width_matches_every_orm_vector_column() -> None:
    """A pgvector column's width is fixed at DDL time. Changing
    `EMBEDDING_DIM` without a re-embed migration is a query-time failure, not a
    config change."""
    from pgvector.sqlalchemy import Vector

    from app.models.candidate import Profile
    from app.models.context import ContextChunk

    for model, column in ((Profile, "embedding"), (ContextChunk, "embedding")):
        col_type = model.__table__.columns[column].type
        assert isinstance(col_type, Vector), f"{model.__name__}.{column}"
        assert col_type.dim == embeddings.EMBEDDING_DIM, f"{model.__name__}.{column}"


def test_no_migration_declares_a_vector_of_a_different_width() -> None:
    """`jobs.embedding` and `jobs.reach_embedding` are not on any ORM model --
    they are added by raw DDL and written by raw SQL, because writing them
    through the ORM would make a JD edit and its embedding invalidation one
    transaction that can be rolled back together. So the ORM check above cannot
    see them, and this reads the DDL.

    Widening the constant without touching the migrations would leave the code
    producing vectors the database refuses, and the refusal surfaces on the
    first query rather than the first write.
    """
    import ast
    import re

    versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    declared = re.compile(r"[Vv]ector\((\d+)\)")

    def _ddl_widths(path: pathlib.Path) -> set[int]:
        """Widths named in EXECUTABLE code only.

        A migration's docstring legitimately quotes the width it is migrating
        away FROM -- 0058 explains why a `vector(384)` column cannot be widened
        in place -- and a scan that read prose would report the explanation as
        the defect. The AST walk sees string literals that are actually part of
        a statement (the DDL strings) and skips the module docstring.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        widths: set[int] = set()
        docstring = ast.get_docstring(tree, clean=False)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == docstring:
                    continue
                widths.update(int(m) for m in declared.findall(node.value))
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in {"Vector", "vector"}:
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                            widths.add(arg.value)
        return widths

    widths = {p.name: found for p in versions.glob("*.py") if (found := _ddl_widths(p))}
    assert widths, "no vector columns found -- the scan is broken, not the schema"

    # 0042 declared `jobs.reach_embedding` at 384 for the CPU-local bge-small
    # model AI Reach used to run. 0058 widens it to the platform width and NULLs
    # the vectors, so 0042's number is HISTORY: the migration that created it
    # must keep saying 384, or a fresh database would build a column 0058 then
    # fails to alter. Exempted by name, with 0058 asserted to exist, so the
    # exemption cannot outlive the migration that justifies it.
    superseded = {"0042_ai_reach_semantic_embeddings.py": {384}}
    assert (versions / "0058_single_embedding_space.py").exists(), (
        "0058 is what makes 0042's 384 historical. Without it the exemption "
        "below is hiding a live second vector space."
    )
    for name, found in widths.items():
        expected = superseded.get(name, {embeddings.EMBEDDING_DIM})
        assert found == expected, f"{name} declares {found}, expected {expected}"


def test_ai_reach_shares_the_one_embedding_space() -> None:
    """spec-doc5 §B.2: voyage-context-4 is the sole embedding model for EVERY
    RAG surface. AI Reach's role search was the one that was not.

    The COLUMN stays separate -- role-to-role similarity and candidate-to-job
    similarity are different questions with different invalidation triggers.
    The MODEL does not, because two models mean two vector spaces that look
    interchangeable in the schema and are not.
    """
    from app.services import reach_embeddings

    assert reach_embeddings.EMBEDDING_DIM == embeddings.EMBEDDING_DIM
    assert reach_embeddings.MODEL_NAME == embeddings.EMBEDDING_MODEL


def test_ai_reach_keeps_its_own_error_class() -> None:
    """`bd_leads` catches it to fall back to exact distinctive-role matching,
    which is the honest degraded answer for ROLE SEARCH and the wrong answer for
    anything else. Catching the shared class would make that fallback fire on a
    resume-parsing failure."""
    from app.services import reach_embeddings

    assert not issubclass(
        reach_embeddings.ReachEmbeddingError, embeddings.EmbeddingError
    )


def test_the_model_is_the_only_one_the_platform_permits() -> None:
    assert embeddings.EMBEDDING_MODEL == llm_providers.EMBEDDING_MODEL
    assert embeddings.EMBEDDING_MODEL == "voyage-context-4"


# ── The dev fallback ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_fallback_is_deterministic_and_correctly_shaped(monkeypatch) -> None:
    """Without it, the whole matching pipeline and the entire test suite would
    need a paid credential to run at all, and CI would become a thing that can
    fail because a vendor is down."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _NoKey())
    first = await embeddings.embed(["kafka partition rebalance"])
    second = await embeddings.embed(["kafka partition rebalance"])
    assert first == second
    assert len(first) == 1
    assert len(first[0]) == embeddings.EMBEDDING_DIM


@pytest.mark.asyncio
async def test_the_fallback_vectors_are_unit_length(monkeypatch) -> None:
    """Cosine distance on a non-normalised vector is not cosine distance."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _NoKey())
    vector = (await embeddings.embed(["anything"]))[0]
    magnitude = sum(component * component for component in vector) ** 0.5
    assert magnitude == pytest.approx(1.0, abs=1e-9)


def test_the_fallback_announces_itself(monkeypatch) -> None:
    """`is_semantic` exists so a caller that must not present a fallback
    ordering as a judgement can ask, rather than inferring it."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _NoKey())
    assert not embeddings.is_semantic()
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    assert embeddings.is_semantic()


@pytest.mark.asyncio
async def test_an_empty_batch_calls_nothing(monkeypatch) -> None:
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    assert await embeddings.embed([]) == []


# ── The real client ──────────────────────────────────────────────────────────


def _response(vectors, *, shuffle: bool = False):
    """A Voyage-shaped payload. `shuffle` reverses the rows while keeping the
    indices correct, which is exactly the case a naive reader gets wrong."""
    rows = [
        {"index": i, "embedding": vec} for i, vec in enumerate(vectors)
    ]
    if shuffle:
        rows = list(reversed(rows))
    return {"data": rows, "model": embeddings.EMBEDDING_MODEL}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _patch_client(monkeypatch, payload, recorder=None):
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            if recorder is not None:
                recorder.append({"url": url, "headers": headers, "json": json})
            return _FakeResponse(payload)

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_the_request_pins_the_model_and_the_width(monkeypatch) -> None:
    """`output_dimension` is stated at the call rather than inherited from
    whatever the vendor's default happens to be next year. The schema depends
    on it."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    calls: list[dict] = []
    _patch_client(monkeypatch, _response([[0.0] * embeddings.EMBEDDING_DIM]), calls)

    await embeddings.embed(["one"])
    body = calls[0]["json"]
    assert body["model"] == "voyage-context-4"
    assert body["output_dimension"] == embeddings.EMBEDDING_DIM
    assert body["input_type"] == embeddings.INPUT_TYPE_DOCUMENT


@pytest.mark.asyncio
async def test_the_key_travels_in_a_header_never_a_query_string(monkeypatch) -> None:
    """As a query parameter it lands in httpx's own INFO log line and from
    there into the log sink in plain text. That guarantee has been broken once
    before, on a different vendor."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    calls: list[dict] = []
    _patch_client(monkeypatch, _response([[0.0] * embeddings.EMBEDDING_DIM]), calls)

    await embeddings.embed(["one"])
    assert "vk-test" not in calls[0]["url"]
    assert calls[0]["headers"]["Authorization"] == "Bearer vk-test"


@pytest.mark.asyncio
async def test_a_query_is_embedded_differently_from_a_document(monkeypatch) -> None:
    """Voyage embeds a query and a document asymmetrically. Getting this
    backwards is a silent quality regression: retrieval keeps returning results,
    they are just worse."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    calls: list[dict] = []
    _patch_client(monkeypatch, _response([[0.0] * embeddings.EMBEDDING_DIM]), calls)

    await embeddings.embed_query("what did they build")
    assert calls[0]["json"]["input_type"] == embeddings.INPUT_TYPE_QUERY


@pytest.mark.asyncio
async def test_an_out_of_order_batch_is_reordered_by_index(monkeypatch) -> None:
    """The data-corruption case. A trusted out-of-order batch attaches every
    candidate's vector to the next candidate's row, and nothing downstream
    looks wrong."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    first = [1.0] + [0.0] * (embeddings.EMBEDDING_DIM - 1)
    second = [0.0, 1.0] + [0.0] * (embeddings.EMBEDDING_DIM - 2)
    _patch_client(monkeypatch, _response([first, second], shuffle=True))

    result = await embeddings.embed(["a", "b"])
    assert result[0] == first
    assert result[1] == second


@pytest.mark.asyncio
async def test_a_malformed_payload_raises_the_typed_error(monkeypatch) -> None:
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    _patch_client(monkeypatch, {"not_data": []})
    with pytest.raises(embeddings.EmbeddingError):
        await embeddings.embed(["one"])


@pytest.mark.asyncio
async def test_a_wrong_width_raises_rather_than_being_stored(monkeypatch) -> None:
    """Storing a 512-dim vector in a vector(1024) column fails at query time on
    a machine that is not this one."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    _patch_client(monkeypatch, _response([[0.0] * 512]))
    with pytest.raises(embeddings.EmbeddingError):
        await embeddings.embed(["one"])


@pytest.mark.asyncio
async def test_a_large_batch_is_split_and_rejoined_in_order(monkeypatch) -> None:
    """A caller embedding a whole resume corpus should never have to know the
    vendor's per-request ceiling exists."""
    monkeypatch.setattr(embeddings, "get_settings", lambda: _Settings())
    texts = [f"text-{i}" for i in range(embeddings.MAX_BATCH + 5)]
    seen: list[int] = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            batch = json["input"]
            seen.append(len(batch))
            # Each vector encodes its own text so ordering can be checked.
            return _FakeResponse(
                _response(
                    [
                        [float(int(t.split("-")[1]))] + [0.0] * (embeddings.EMBEDDING_DIM - 1)
                        for t in batch
                    ]
                )
            )

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", _Client)

    result = await embeddings.embed(texts)
    assert seen == [embeddings.MAX_BATCH, 5]
    assert [int(v[0]) for v in result] == list(range(len(texts)))
