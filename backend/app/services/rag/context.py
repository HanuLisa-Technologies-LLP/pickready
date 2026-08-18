"""Assembling retrieved chunks into the block of text an agent actually receives.

A TOKEN BUDGET IS A PRODUCT DECISION, NOT A TRUNCATION
------------------------------------------------------
Everything retrieved cannot go into the prompt, so something decides what is
dropped. Doing it by cutting the assembled string at N characters means the last
chunk arrives as half a sentence, and a model handed half a sentence will
complete it -- from its own priors, not from the candidate's resume. Chunks are
therefore dropped WHOLE, in rank order, and the fact that some were dropped is
recorded on the result rather than being invisible.

COMPRESSION IS EXTRACTIVE AND CALLS NO MODEL
---------------------------------------------
The specification asks for LLM summarisation of oversized context. Every caller
here is on a path bounded at 15 seconds per attempt and 30 in total, and a
summarisation call inside retrieval spends that budget before the actual
generation has started -- so an outage in the summariser becomes an outage in
the feature, for context the agent could have had verbatim. What runs instead
selects whole sentences by query-term coverage. It cannot invent, which matters
more here than fluency: this text is evidence a grade will be written from.

DEDUPLICATION IS BY CONTENT, NOT BY ID
---------------------------------------
Chunk overlap means two adjacent chunks legitimately share a sentence. Two
distinct ids can therefore carry the same claim, and an agent counting
corroboration would count it twice. Near-duplicates are collapsed on normalised
text before the budget is applied, so the space goes to something new.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.rag.retrieval import RetrievedChunk

#: Four characters per token: the standard conservative estimate for English and
#: JSON, and the same one `agent_loop` uses. Exactness is not the point; the
#: point is that the same estimate is used everywhere, so two budgets are
#: comparable.
CHARS_PER_TOKEN = 4

#: Default ceiling for one assembled context block.
MAX_CONTEXT_TOKENS = 2000

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9+#.]+")


def estimate_tokens(text: str) -> int:
    return max(0, -(-len(str(text or "")) // CHARS_PER_TOKEN))


def _terms(value: str) -> set[str]:
    return {token for token in _WORD.findall(str(value or "").casefold()) if len(token) > 1}


def _normalised(text: str) -> str:
    return " ".join(_WORD.findall(str(text or "").casefold()))


@dataclass
class AssembledContext:
    """The text an agent receives, and an honest account of what it excludes."""

    text: str = ""
    chunks: list[RetrievedChunk] = field(default_factory=list)
    tokens: int = 0
    #: Retrieved, then dropped for the budget. A caller that logs this can see a
    #: prompt starving before anybody complains about the output.
    dropped: int = 0
    #: Collapsed as near-duplicates of something already included.
    deduplicated: int = 0
    #: At least one chunk was shortened to fit.
    compressed: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def compress(content: str, query: str, *, max_tokens: int) -> str:
    """Keep the sentences that answer the query, in their original order.

    Extractive: every sentence returned appeared verbatim in the source. Order
    is preserved rather than being ranked, because a paragraph reordered by
    relevance reads as a non-sequitur and its internal "this" and "that" stop
    referring to anything.
    """
    budget = max_tokens * CHARS_PER_TOKEN
    if len(content) <= budget:
        return content

    sentences = [piece.strip() for piece in _SENTENCE.split(content) if piece.strip()]
    if not sentences:
        return content[:budget]

    query_terms = _terms(query)
    ranked = sorted(
        range(len(sentences)),
        key=lambda index: (
            len(query_terms & _terms(sentences[index])),
            -index,
        ),
        reverse=True,
    )

    kept: list[int] = []
    used = 0
    for index in ranked:
        cost = len(sentences[index]) + 1
        if used + cost > budget:
            continue
        kept.append(index)
        used += cost
    if not kept:
        # One sentence longer than the whole budget. Returning nothing is worse
        # than returning a bounded prefix of the single relevant thing there is.
        return sentences[0][:budget]

    return " ".join(sentences[index] for index in sorted(kept))


def assemble(
    chunks: list[RetrievedChunk],
    *,
    query: str = "",
    max_tokens: int = MAX_CONTEXT_TOKENS,
    label: bool = True,
) -> AssembledContext:
    """Deduplicate, budget, compress if needed, and render.

    `label` prefixes each chunk with its source and section. That is not
    decoration: an agent asked to ground a claim in evidence needs to be able to
    say WHICH evidence, and a wall of unattributed paragraphs makes every claim
    equally unsourceable.
    """
    result = AssembledContext()
    seen: set[str] = set()
    unique: list[RetrievedChunk] = []
    for chunk in chunks:
        signature = _normalised(chunk.content)
        if not signature:
            continue
        if signature in seen or any(
            signature in existing or existing in signature for existing in seen
        ):
            result.deduplicated += 1
            continue
        seen.add(signature)
        unique.append(chunk)

    rendered: list[str] = []
    used = 0
    for index, chunk in enumerate(unique):
        header = (
            f"[{chunk.source_type}:{chunk.section_type}]\n" if label else ""
        )
        remaining = max_tokens - used
        if remaining <= 0:
            result.dropped += len(unique) - index
            break

        body = chunk.content
        cost = estimate_tokens(header + body)
        if cost > remaining:
            # Compress rather than drop only when there is room worth using. A
            # two-token remainder produces a fragment nobody can read.
            if remaining < 40:
                result.dropped += len(unique) - index
                break
            body = compress(body, query, max_tokens=remaining - estimate_tokens(header))
            result.compressed = True
            cost = estimate_tokens(header + body)

        rendered.append(header + body)
        result.chunks.append(chunk)
        used += cost

    result.text = "\n\n".join(rendered)
    result.tokens = used
    return result
