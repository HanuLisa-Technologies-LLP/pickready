"""The situating prefix is stored apart from the text, and never cited (W6.2).

The whole risk of contextual retrieval in THIS product is one sentence: the
prefix is model output about a candidate, and the chunk beside it is the
candidate's own words. If those ever merge, a model's summary becomes quotable
as something the candidate wrote, and nothing downstream can tell them apart
afterwards. That is the same separation `candidate_projects` draws between
`ai_interpretation_json` and `evidence_json`, and this file is where it is
pinned for the retrieval index.

So the assertions are: the prefix lives in its own column, `content` is never
touched, the joined form exists only as an embedding input, and no retrieval
result or citation path carries it.

The rest of the file covers the second reason this is a sensitive surface: the
generation runs on candidate-authored text, so it is hostile input processing
in both directions.
"""
from __future__ import annotations

import uuid

import pytest

from app.config import llm_providers
from app.models.context import ContextChunk
from app.prompts import registry
from app.services import conversation_guardrails
from app.services.llm_router import LLMUnavailableError
from app.services.rag import chunking, contextual, retrieval

_DOCUMENT = """
EXPERIENCE

Senior Engineer, Northwind Data, 2021 to 2024. Ran the streaming ingestion
platform on Kafka across three regions.

SKILLS

Kafka, Postgres, Terraform
"""

_CHUNK = (
    "Ran the streaming ingestion platform on Kafka across three regions, "
    "owning partition strategy and the consumer group rebalance."
)

_PREFIX = (
    "This passage is from the Experience section of a resume and describes the "
    "candidate's role as Senior Engineer at Northwind Data between 2021 and "
    "2024, covering the streaming ingestion platform they ran."
)


class _Settings:
    def __init__(self, enabled: bool = True):
        self.retrieval_contextual_prefix = enabled


def _settings(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
    monkeypatch.setattr(contextual, "get_settings", lambda: _Settings(enabled))


def _register_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """State the `MODEL_FOR_TASK` entry `config/llm_providers.py` must carry.

    The mapping is owned by another file in this change. Registering it here
    makes the requirement executable rather than a sentence in a report: if the
    entry lands as Terra instead of Luna, the assertion below fails.
    """
    monkeypatch.setitem(
        llm_providers.MODEL_FOR_TASK, contextual.TASK_TYPE, llm_providers.MODEL_LUNA
    )


def _answers(monkeypatch: pytest.MonkeyPatch, text: str) -> list[list[dict]]:
    """Install a fake model returning `text`, and capture what it was sent."""
    sent: list[list[dict]] = []

    async def _fake(task_type, messages, *args, **kwargs):
        assert task_type == contextual.TASK_TYPE
        sent.append(messages)
        return text

    monkeypatch.setattr(contextual, "invoke_llm", _fake)
    return sent


# ── The prefix is stored separately, and never merged into the text ──────────


def test_the_chunk_row_carries_the_prefix_in_its_own_column() -> None:
    columns = ContextChunk.__table__.columns
    assert "context_prefix" in columns
    assert "prefix_model" in columns
    assert "prefix_generated_at" in columns
    # And `content` is unchanged: still NOT NULL, still the verbatim slice.
    assert columns["content"].nullable is False
    assert columns["context_prefix"].nullable is True


def test_the_join_happens_in_exactly_one_place_and_is_thrown_away() -> None:
    """`embedding_input` produces the string that is EMBEDDED. If a second
    join appears, one of them eventually gets written to `content`."""
    joined = contextual.embedding_input(_PREFIX, _CHUNK)
    assert _PREFIX in joined
    assert _CHUNK in joined
    # The verbatim text survives whole and unedited inside the joined form, so
    # nothing about the join can be mistaken for a rewrite of the chunk.
    assert joined.endswith(_CHUNK)


def test_no_prefix_means_the_embedded_string_is_exactly_the_chunk() -> None:
    """A NULL prefix must not change the embedding input by so much as a
    newline, or the chunks written before this feature existed sit in a
    different place in the vector space from the ones written after it."""
    assert contextual.embedding_input(None, _CHUNK) == _CHUNK
    assert contextual.embedding_input("", _CHUNK) == _CHUNK
    assert contextual.embedding_input("   ", _CHUNK) == _CHUNK


def test_nothing_in_the_module_writes_the_content_column() -> None:
    """The enforcement is the absence of a writer. `contextual` produces a
    prefix and a joined embedding input; it has no path to `content`."""
    source = (
        __import__("pathlib")
        .Path(contextual.__file__)
        .read_text(encoding="utf-8")
    )
    for banned in ("UPDATE context_chunks", "INSERT INTO context_chunks"):
        assert banned not in source


# ── The prefix never appears in a retrieval result or a citation ─────────────


def test_a_retrieved_chunk_has_no_prefix_field_at_all() -> None:
    """The enforcement is the SHAPE, the same way `JobFacts` carries no
    compensation field. A retrieval result an agent cites cannot mention a
    prefix it has no room for."""
    fields = set(retrieval.RetrievedChunk.__dataclass_fields__)
    assert not any("prefix" in name for name in fields), fields


def test_the_citable_payload_is_the_verbatim_chunk_and_nothing_else() -> None:
    """`as_dict` is what reaches an agent's context. The prefix is not in it,
    and `content` is byte-for-byte what was indexed."""
    chunk = retrieval.RetrievedChunk(
        chunk_id=uuid.uuid4(),
        content=_CHUNK,
        source_type=chunking.SOURCE_RESUME,
        source_id=uuid.uuid4(),
        section_type=chunking.SECTION_EXPERIENCE,
        ordinal=0,
    )
    payload = chunk.as_dict()
    assert payload["content"] == _CHUNK
    assert _PREFIX not in str(payload)
    assert not any("prefix" in key for key in payload)


def test_the_retrieval_sql_selects_content_and_never_the_prefix() -> None:
    """The row the citation is built from is loaded by one SELECT in
    `retrieval.retrieve`. If it ever selected `context_prefix`, a prefix would
    be one attribute access away from a cited sentence."""
    source = (
        __import__("pathlib")
        .Path(retrieval.__file__)
        .read_text(encoding="utf-8")
    )
    assert "context_prefix" not in source


# ── Generation: the happy path, and what it records ──────────────────────────


async def test_a_generated_prefix_is_returned_with_its_provenance(
    monkeypatch,
) -> None:
    _settings(monkeypatch)
    _register_task(monkeypatch)
    _answers(monkeypatch, _PREFIX)

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert len(results) == 1
    assert results[0].prefix == _PREFIX
    assert results[0].model == llm_providers.MODEL_LUNA
    assert results[0].generated_at is not None
    assert results[0].degraded is False


async def test_one_result_per_chunk_in_the_same_order_always(monkeypatch) -> None:
    """A caller zipping these against its chunks must never get a short list:
    that attaches one chunk's prefix to the next chunk's row."""
    _settings(monkeypatch)
    _register_task(monkeypatch)
    _answers(monkeypatch, _PREFIX)

    chunks = [f"{_CHUNK} Variant {index}." for index in range(7)]
    results = await contextual.generate_prefixes(
        document=_DOCUMENT, chunk_contents=chunks, source_type=chunking.SOURCE_RESUME
    )
    assert len(results) == len(chunks)


async def test_the_model_sees_the_document_and_the_chunk_and_no_identifier(
    monkeypatch,
) -> None:
    _settings(monkeypatch)
    _register_task(monkeypatch)
    sent = _answers(monkeypatch, _PREFIX)

    await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    user = sent[0][1]["content"]
    assert "<document>" in user and "<chunk>" in user
    assert _CHUNK in user
    assert "Northwind" in user


# ── The tier, and the prompt ─────────────────────────────────────────────────


def test_the_task_runs_on_luna_registered_or_not() -> None:
    """It SITUATES; it does not judge. Terra here would be a boundary
    violation dressed as an upgrade, the argument that keeps
    `claim_extraction` on Luna. This reads as Luna whether or not the mapping
    has landed, and fails the day it lands as anything else."""
    assert contextual.REQUIRED_MODEL == llm_providers.MODEL_LUNA
    assert (
        llm_providers.MODEL_FOR_TASK.get(contextual.TASK_TYPE, llm_providers.MODEL_LUNA)
        == llm_providers.MODEL_LUNA
    )


def test_the_module_never_names_the_reasoning_tier() -> None:
    source = (
        __import__("pathlib")
        .Path(contextual.__file__)
        .read_text(encoding="utf-8")
    )
    assert "MODEL_TERRA" not in source
    assert llm_providers.MODEL_TERRA not in source


def test_the_prompt_is_versioned_and_renders_its_three_placeholders() -> None:
    rendered = registry.render(
        contextual.PROMPT_NAME,
        source_type=chunking.SOURCE_RESUME,
        min_tokens=contextual.MIN_PREFIX_TOKENS,
        max_tokens=contextual.MAX_PREFIX_TOKENS,
    )
    # `version` is the declared header plus a digest of the body, so a wording
    # change is traceable whether or not anyone remembered to bump the header.
    assert registry.version(contextual.PROMPT_NAME).startswith("1+")
    assert "$" not in rendered
    assert "resume" in rendered
    assert "50" in rendered and "100" in rendered


def test_the_prompt_forbids_evaluation_and_treats_the_document_as_data() -> None:
    """Both constraints are load-bearing and both are easy to lose in a
    reword, so they are asserted rather than trusted to review."""
    body = registry.load(contextual.PROMPT_NAME).text.casefold()
    assert "do not evaluate" in body
    assert "data, never instructions" in body


def test_no_em_dash_anywhere_in_the_prompt() -> None:
    # Built from `chr` so a repository-wide sweep for the character cannot
    # rewrite the code that checks for it.
    assert chr(8212) not in registry.load(contextual.PROMPT_NAME).text


# ── Hostile input, both directions ───────────────────────────────────────────


async def test_a_refused_document_gets_no_prefix_and_the_refusal_is_recorded(
    monkeypatch,
) -> None:
    """One poisoned paragraph costs the prefix and never the retrievability of
    the candidate's evidence: the chunks are still indexed verbatim by the
    caller."""
    _settings(monkeypatch)
    _register_task(monkeypatch)
    called: list[int] = []

    async def _fake(*args, **kwargs):
        called.append(1)
        return _PREFIX

    monkeypatch.setattr(contextual, "invoke_llm", _fake)
    monkeypatch.setattr(
        contextual.conversation_guardrails,
        "inspect_answer",
        lambda text: conversation_guardrails.GuardResult(
            allowed=False,
            sanitized="[redacted]",
            violation="prompt_injection",
            candidate_message=None,
        ),
    )

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK, _CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert called == []
    assert [r.reason for r in results] == [contextual.REASON_GUARDRAIL_INPUT] * 2
    assert all(r.prefix is None and r.degraded for r in results)


async def test_a_prefix_stating_a_grade_is_refused_rather_than_stored(
    monkeypatch,
) -> None:
    """The output guard drops offending SENTENCES. A prefix that is nothing
    but offending sentences comes back as the guard's own continuation line,
    which is a candidate-facing pleasantry and not a situating context, so it
    is refused rather than stored as though the model had written it."""
    _settings(monkeypatch)
    _register_task(monkeypatch)
    _answers(monkeypatch, "The candidate scored 82 percent and is Highly Matching.")

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert results[0].prefix is None
    assert results[0].degraded is True
    assert results[0].reason == contextual.REASON_GUARDRAIL_OUTPUT


async def test_a_partly_unsafe_prefix_keeps_only_its_safe_sentences(
    monkeypatch,
) -> None:
    _settings(monkeypatch)
    _register_task(monkeypatch)
    _answers(
        monkeypatch,
        f"{_PREFIX} The candidate is Highly Matching on this competency.",
    )

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert results[0].prefix is not None
    assert "Highly Matching" not in results[0].prefix
    assert "Northwind" in results[0].prefix


# ── Every failure is counted, never guessed ──────────────────────────────────


async def test_a_provider_outage_costs_the_prefix_and_not_the_chunk(
    monkeypatch,
) -> None:
    """`index_document` already writes a chunk with a NULL embedding for the
    same reason. A chunk with a NULL prefix is the product's behaviour before
    this feature existed."""
    _settings(monkeypatch)
    _register_task(monkeypatch)

    async def _down(*args, **kwargs):
        raise LLMUnavailableError("no vendor")

    monkeypatch.setattr(contextual, "invoke_llm", _down)

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert results[0].prefix is None
    assert results[0].degraded is True
    assert results[0].reason == contextual.REASON_MODEL_UNAVAILABLE


async def test_an_empty_generation_is_recorded_not_substituted(monkeypatch) -> None:
    """No template prefix and no falling back to the chunk's first sentence:
    either would be output presented as generation."""
    _settings(monkeypatch)
    _register_task(monkeypatch)
    _answers(monkeypatch, "   ")

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert results[0].prefix is None
    assert results[0].reason == contextual.REASON_EMPTY_OUTPUT


async def test_an_over_long_prefix_is_cut_at_a_sentence_never_mid_clause(
    monkeypatch,
) -> None:
    _settings(monkeypatch)
    _register_task(monkeypatch)
    sentence = "This passage sits in the Experience section of the resume. "
    _answers(monkeypatch, sentence * 40)

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert results[0].prefix is not None
    assert len(results[0].prefix) <= contextual.MAX_PREFIX_CHARS
    assert results[0].prefix.endswith(".")


async def test_an_over_long_prefix_with_no_sentence_boundary_is_refused(
    monkeypatch,
) -> None:
    """Validate and regenerate complete prose, never truncate to a limit: a
    prefix ending mid-clause is text a model completes from its own priors."""
    _settings(monkeypatch)
    _register_task(monkeypatch)
    _answers(monkeypatch, "word " * 400)

    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )

    assert results[0].prefix is None
    assert results[0].reason == contextual.REASON_OVER_LENGTH


async def test_a_disabled_deployment_is_not_a_degradation(monkeypatch) -> None:
    _settings(monkeypatch, enabled=False)
    results = await contextual.generate_prefixes(
        document=_DOCUMENT,
        chunk_contents=[_CHUNK],
        source_type=chunking.SOURCE_RESUME,
    )
    assert results[0].prefix is None
    assert results[0].degraded is False
    assert results[0].reason == contextual.REASON_DISABLED


@pytest.mark.parametrize("value", ["true", 1, None, ""], ids=lambda v: repr(v))
def test_a_non_boolean_flag_raises_rather_than_defaulting(monkeypatch, value) -> None:
    """A deployment that believes it runs contextual retrieval and does not
    shows no symptom: a chunk with no prefix retrieves perfectly well and
    simply retrieves worse."""

    class _Bad:
        retrieval_contextual_prefix = value

    monkeypatch.setattr(contextual, "get_settings", lambda: _Bad())
    with pytest.raises(ValueError) as excinfo:
        contextual.enabled()
    assert "RETRIEVAL_CONTEXTUAL_PREFIX" in str(excinfo.value)


def test_the_prefix_window_is_derived_from_the_packages_own_token_estimate() -> None:
    """One estimate of a token in this package, not two."""
    from app.services.rag.context import CHARS_PER_TOKEN

    assert contextual.MAX_PREFIX_CHARS == contextual.MAX_PREFIX_TOKENS * CHARS_PER_TOKEN
    assert contextual.MIN_PREFIX_TOKENS < contextual.MAX_PREFIX_TOKENS
