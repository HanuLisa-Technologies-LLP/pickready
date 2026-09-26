"""The order a prompt's static context is written in, so a cache can hit it.

WHAT THE VENDOR ACTUALLY DOES, AND WHAT IT DOES NOT TAKE
----------------------------------------------------------
This product calls OpenAI Chat Completions over raw `httpx`. That API applies
prompt caching AUTOMATICALLY to a long enough IDENTICAL PREFIX of a request and
reports the hit back as `usage.prompt_tokens_details.cached_tokens`, which
`llm_router._cached_prompt_tokens` reads. There is no `cache_control` marker to
send, no `cache` field, and nothing in this module emits one: a request-side
marker would be a parameter the endpoint does not define, and this repository
has already had one 400 (`max_tokens`) teach it what that costs.

So the ONE lever the product has over caching is ORDER. A prefix is only
identical if every byte before the divergence is identical, which means the
parts that do not change between two calls must come FIRST and the parts that
change on every turn must come LAST. That is the whole mechanism and it is why
this module exists at all: it is a hundred lines of ordering, not a feature.

THE ORDER, AND WHY IT IS THIS ORDER
-------------------------------------
`SEGMENT_ORDER` below runs from the most widely shared to the most specific:

  * the system prompt is shared by every call of that task type, anywhere;
  * the job description is shared by every candidate on that job;
  * Drishti's and Bodha's outputs are job-level derivations, shared the same way;
  * the assessment matrix is the job's frozen criteria, shared the same way;
  * the candidate's own material is shared across that candidate's turns only;
  * the per-turn material is shared with nothing.

Read downwards, each boundary is a place where a cache can still hit for a
wider population than the one below it. Read upwards, putting the per-turn
material first (which is what the interviewer payload did) means the prefix
diverges on byte one of the JSON body and no two calls in the product ever
share one.

THIS MODULE INVENTS NO CONTENT
--------------------------------
`assemble` places the segments a caller SUPPLIES into the canonical order and
omits the rest. It never fabricates a segment, and in particular it does not
put Drishti or Bodha into a prompt that was not already carrying them: adding
material to a prompt that decides what a candidate is graded on is a product
change, and this is an ordering change. A caller that has nothing for a segment
passes nothing and the prefix is simply shorter.

NOTHING HERE READS OR WRITES A CACHE
--------------------------------------
There is no store behind this. `identity` exists so that the platform can
RECOGNISE two calls that ought to share a prefix, for measurement and for the
day something does cache locally; it is not a lookup key for a cache this
module owns. It carries the tenant for the reason every key in this codebase
does (`tests/test_cache_tenant_keying.py`), and the job for a narrower one: two
jobs inside one tenant have different criteria, and a prefix identity that
could not tell them apart would claim a shared prefix between two prompts that
grade different things.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping

from app.core import cache

#: Bumped whenever the ASSEMBLY changes shape: a new segment, a reordering, a
#: different separator. Two calls assembled by different versions do not share
#: a prefix even when every segment matches, so an identity that ignored this
#: would assert a shared prefix across a deploy boundary that broke it.
PREFIX_VERSION = "2026.09.22"

#: The static, most-shared segment first; the volatile, least-shared segment
#: last. DATA rather than an ordered set of function calls, so the ordering is
#: reviewable in one place and testable without running an assembly.
SEGMENT_ORDER: tuple[str, ...] = (
    "system",
    "job_description",
    "drishti",
    "bodha",
    "matrix",
    "candidate",
    "turn",
)

#: `system` is first in the order above and is NOT a body segment: it is a
#: message of its own, passed to `assemble` as `system=` and placed by
#: `llm_router.build_payload` at the head of the array. It is listed in
#: `SEGMENT_ORDER` anyway because the order there is the order of the REQUEST,
#: and a reader looking for where the system prompt sits in the prefix should
#: find the answer in the one table rather than in two.
_BODY_SEGMENTS: tuple[str, ...] = tuple(
    name for name in SEGMENT_ORDER if name != "system"
)

#: The body segments that are expected to be byte-identical across calls that
#: share a job. `identity` digests exactly these and `ordered_fields` writes
#: them ahead of everything else. `candidate` is deliberately NOT here: it is
#: stable within one candidate's conversation and differs between candidates,
#: so counting it as part of the job-level prefix would make two candidates'
#: identities differ for a reason the job-level prefix does not care about.
STATIC_SEGMENTS: frozenset[str] = frozenset(
    {"job_description", "drishti", "bodha", "matrix"}
)


class SegmentOrderError(ValueError):
    """A segment name that is not in `SEGMENT_ORDER`.

    Raised rather than dropped. A dropped segment is content silently missing
    from a prompt that decides a grade, and it would look exactly like a caller
    that had nothing to supply.
    """


def ordered_fields(segments: Mapping[str, Mapping[str, Any] | None]) -> dict[str, Any]:
    """Every segment's fields merged into ONE flat dict, in segment order.

    FLAT, NOT NESTED, AND THAT IS THE POINT FOR AN EXISTING CALLER. A caller
    that already sends one JSON user message keeps sending exactly the same
    field names and the same values; all that moves is the order they are
    written in. `json.dumps` preserves insertion order, so the serialised body
    now opens with the stable half and ends with the volatile half, and no
    prompt gained or lost a single character of content in the process.

    Re-nesting the fields under their segment names would have been tidier and
    would have been a different change: a model reading a restructured payload
    is being asked something new, and this is an ordering change to a prompt
    that decides what a candidate is graded on.

    A segment set to None is omitted. A FIELD set to None is kept, because a
    caller that deliberately sends an empty field is making a statement about
    the record and rewriting that to an absence changes what was asked.
    """
    _reject_unknown(segments)
    merged: dict[str, Any] = {}
    for name in _BODY_SEGMENTS:
        fields = segments.get(name)
        if fields is None:
            continue
        for field_name, value in fields.items():
            if field_name in merged:
                raise SegmentOrderError(
                    f"field {field_name!r} appears in more than one segment; a "
                    "field belongs to exactly one, because its segment is what "
                    "decides where in the prefix it is written"
                )
            merged[field_name] = value
    return merged


def assemble(
    segments: Mapping[str, Mapping[str, Any] | None], *, system: str | None = None
) -> list[dict[str, Any]]:
    """Canonical message list: one system message, then the ordered fields.

    The system prompt is a MESSAGE at the head of the array, which is where
    Chat Completions takes it and where `llm_router.build_payload` puts it, so
    the two agree about what the first bytes of a request are.

    Everything else becomes ONE user message, rather than one per segment.
    Fewer messages means fewer separators between the segments, and every
    separator is a place where an unrelated change to the envelope breaks a
    prefix that the content itself did not break.
    """
    body = ordered_fields(
        {name: value for name, value in segments.items() if name != "system"}
    )
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": str(system)})
    if body:
        messages.append(
            {"role": "user", "content": json.dumps(body, ensure_ascii=False)}
        )
    return messages


def static_prefix_text(
    segments: Mapping[str, Mapping[str, Any] | None]
) -> str:
    """Exactly the bytes a cache could share, for measurement and for tests.

    This is the assertion that makes the ordering falsifiable: two turns of one
    conversation must produce an IDENTICAL string here while their assembled
    bodies differ, and the static text must be a literal PREFIX of the full
    body rather than merely a subset of its keys. Without it, "the static
    context is a stable prefix" is a claim in a docstring rather than a
    property anything checks.
    """
    static = ordered_fields(
        {
            name: fields
            for name, fields in segments.items()
            if name in STATIC_SEGMENTS
        }
    )
    return json.dumps(static, ensure_ascii=False)


def identity(
    *,
    tenant_id: uuid.UUID | str,
    job_id: uuid.UUID | str | None,
    task_type: str,
    segments: Mapping[str, Any],
) -> str:
    """A handle for "these two calls should share a cached prefix".

    Four things go in and all four are load bearing. The TENANT, because a key
    without one is how multi-tenant isolation disappears in this codebase and
    `tests/test_cache_tenant_keying.py` is what makes that a build failure
    rather than an incident. The JOB, because one tenant's two jobs are graded
    against different criteria. The TASK TYPE, because the system prompt is
    what a prefix mostly consists of and two task types do not share one. And a
    DIGEST OF THE STATIC CONTENT, because a job whose description was edited is
    a different prefix even though nothing about its identity changed -- a
    version number alone would claim a shared prefix with the text it replaced.

    `PREFIX_VERSION` is in the digest input rather than beside it so that an
    assembly change invalidates every identity at once, which is the behaviour
    a shape change needs.
    """
    digest = hashlib.blake2b(
        "\x1f".join(
            [PREFIX_VERSION, task_type, static_prefix_text(segments)]
        ).encode("utf-8"),
        digest_size=16,
    ).hexdigest()
    return cache.key(
        "promptprefix",
        PREFIX_VERSION,
        "tenant",
        str(tenant_id),
        "job",
        str(job_id) if job_id is not None else "none",
        task_type,
        digest,
    )


def _reject_unknown(segments: Mapping[str, Any]) -> None:
    unknown = sorted(set(segments) - set(_BODY_SEGMENTS))
    if unknown:
        raise SegmentOrderError(
            "unknown prompt segment(s) "
            f"{unknown}; add them to SEGMENT_ORDER in the position their "
            "stability warrants rather than passing them through. The system "
            "prompt is not a body segment: pass it to assemble(system=...)"
        )
