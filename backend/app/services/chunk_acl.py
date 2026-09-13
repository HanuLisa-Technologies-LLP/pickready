"""Chunk-level access control for the retrieval index (RPN-AI-UP-001 W9.5).

WHY THE ACL IS ON THE CHUNK AND CHECKED AT RETRIEVAL
------------------------------------------------------
`context_chunks` holds verbatim slices of resumes and assessment transcripts,
and every row carries a vector. An embedding is not a one-way hash: published
inversion work recovers 50 to 70% of the input words from popular sentence
embeddings, and because the embedding model is public and queryable, dictionary
attacks against stolen vectors are practical, structurally like cracking
password hashes. A chunk's vector is therefore the candidate's personal data,
and the question "who may read this" has to be answerable about the CHUNK
rather than about the document it was cut from, because retrieval reads chunks
and a join back to the document is a join a future query will forget to make.

It is checked at RETRIEVAL time, never only at write time, because permissions
change after storage. A recruiter loses `view_review_screen`, a person leaves
the team, a role is re-scoped; every chunk written before that moment is still
sitting in the index with the old answer baked into it.

WHAT THIS MODULE IS AND IS NOT
--------------------------------
It is the PREDICATE, in one place, so the retrieval query and the erasure
cascade cannot disagree about which rows belong to a candidate. It is not the
boundary: the Postgres policy on `context_chunks` is, exactly as claude.md rule
1 says, and this predicate rides on top of it inside the tenant the policy has
already restricted the read to.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

def may_read(acl_capability: str | None, capabilities: Iterable[str]) -> bool:
    """Whether a reader holding `capabilities` may see a chunk.

    A NULL `acl_capability` means tenant membership is the whole check: a JD is
    text the tenant itself wrote and published on a public application page, and
    there is no second permission to hold.

    The pure-Python twin of `visibility_clause`, so a caller that already has
    the row in hand does not rebuild a SQL predicate to ask one question, and so
    a test can pin the two against each other.
    """
    if acl_capability is None:
        return True
    return acl_capability in set(capabilities)


def visibility_clause(param: str = "acl_capabilities") -> str:
    """The SQL predicate a retrieval query ANDs into its WHERE.

    Returns a fragment naming one bind parameter, which the caller supplies as
    a list of the reader's capability strings. An EMPTY list is meaningful and
    correct: it admits only the chunks that require no capability, which is what
    a reader holding nothing should see.
    """
    return f"(acl_capability IS NULL OR acl_capability = ANY(:{param}))"


def visibility_params(
    capabilities: Iterable[str], param: str = "acl_capabilities"
) -> dict[str, Any]:
    """The bind values for `visibility_clause`. Sorted, so a query plan and a
    test both see the same list for the same reader."""
    return {param: sorted({str(value) for value in capabilities})}


def candidate_scope_clause(param: str = "acl_candidate_id") -> str:
    """The predicate selecting every chunk that is one candidate's own text.

    Used by `services/erasure.cascade_erasure`. It reads `acl_candidate_id`
    rather than joining `source_id` back to `profiles` and
    `job_candidate_links`, so an erasure does not have to know which source-id
    shape produced which chunk, and so a source type added later is erased by
    the same statement the day its trigger branch is written.
    """
    return f"acl_candidate_id = :{param}"


def candidate_scope_params(
    candidate_id: uuid.UUID | str, param: str = "acl_candidate_id"
) -> dict[str, Any]:
    return {param: str(candidate_id)}
