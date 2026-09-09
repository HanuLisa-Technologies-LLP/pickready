"""Cached facts about one entity, and the TTLs that keep them honest.

Every TTL here is chosen against how the underlying row actually changes:

  candidate   short. A profile is rewritten by an async parse that can land at
              any moment after upload.
  job         medium. Edited by a recruiter during setup, rarely afterwards.
  framework   long. Frozen once anyone has been assessed against it.

There is no entry for a transcript, and that absence is deliberate: a live
conversation grows between two reads, so an agent scoring a cached one is
scoring the wrong assessment.

THE KEY CARRIES THE TENANT (RPN-AI-UP-001 W3.5)
------------------------------------------------
`remember` and `recall` both require the tenant, and it is the first segment of
the key. Entity ids are UUIDs, so this is not about collisions: it is that a
cache is a place a value can be read from by a caller that never proved it may
see it, and a key nobody can construct without naming a tenant is a cache no
caller can read across one by accident. It also makes the failure mode of a
future bulk invalidation bounded: one customer's prefix, not everybody's.

WHAT IS STORED IS AN ENVELOPE, NOT A BARE VALUE
-------------------------------------------------
Every entry carries its provenance beside its value, so a cached fact can
answer where it came from and when it stops being believable, exactly as a
stored learning can. `recall` refuses an envelope whose tenant does not match
the caller's, which is defence in depth over the key: a key is a string, and a
string can be built wrong.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.core import cache
from app.services.memory.provenance import Provenance

TTL_CANDIDATE = 120
TTL_JOB = 300
TTL_FRAMEWORK = 3600


@dataclass(frozen=True)
class Remembered:
    """A cached fact and where it came from."""

    value: Any
    tenant_id: str
    source: str
    source_version: str | None
    trust_level: str


def key_for(tenant_id: uuid.UUID | str, kind: str, entity_id: Any) -> str:
    """The cache key. Tenant first, so one customer's entries share a prefix."""
    return cache.key("semantic", str(tenant_id), kind, str(entity_id))


async def remember(
    kind: str,
    entity_id: Any,
    value: Any,
    *,
    ttl: int,
    provenance: Provenance,
) -> bool:
    """Cache one fact with its provenance. The tenant comes from `provenance`.

    Taking the tenant from the provenance rather than as a separate argument
    means the key and the stored envelope cannot name different tenants, which
    is the one way this could be got wrong at a call site.
    """
    envelope = {
        "value": value,
        "tenant_id": str(provenance.tenant_id),
        "source": provenance.source,
        "source_version": provenance.source_version,
        "trust_level": provenance.trust_level,
    }
    return await cache.set(
        key_for(provenance.tenant_id, kind, entity_id), envelope, ttl=ttl
    )


async def recall(
    tenant_id: uuid.UUID | str, kind: str, entity_id: Any
) -> Remembered | None:
    """Read a cached fact for this tenant, or None.

    An envelope whose stored tenant disagrees with the caller's is treated as a
    miss and dropped, not returned and not raised. Returning it would be the
    cross-tenant read; raising would turn a poisoned or stale cache entry into
    an outage for a request that can perfectly well take the slow path.
    """
    key = key_for(tenant_id, kind, entity_id)
    stored = await cache.get(key)
    if not isinstance(stored, dict) or "value" not in stored:
        return None
    if str(stored.get("tenant_id")) != str(tenant_id):
        await cache.invalidate(key)
        return None
    return Remembered(
        value=stored["value"],
        tenant_id=str(stored["tenant_id"]),
        source=str(stored.get("source", "")),
        source_version=stored.get("source_version"),
        trust_level=str(stored.get("trust_level", "")),
    )


async def forget(tenant_id: uuid.UUID | str, kind: str, entity_id: Any) -> None:
    """Called by the WRITE path. A cache the writer does not invalidate is a
    cache that serves the value the writer just replaced."""
    await cache.invalidate(key_for(tenant_id, kind, entity_id))
