"""Live capacity registry for the LLM router, keyed by QUOTA DOMAIN.

WHY A DOMAIN AND NOT A KEY
-------------------------
The router has always balanced across CREDENTIALS, on the unexamined assumption
that three keys are three pools. Measured on the live accounts 2026-08-24, that
assumption is false in one direction and true in the other:

  * all three Groq keys bill the SAME organisation (`org_...62bj`) and share one
    8000 tokens-per-minute ceiling. Round-robining them yields no extra
    throughput at all -- it yields three ways to hit the same wall, and the
    413 storm that took every realistic extraction down was exactly that;
  * all three OpenRouter keys report their OWN usage counter, so they really are
    three capacity domains, and that capacity is currently barely routed to.

Asserting three independent pools where one exists is the expensive error: the
router believes it has 3x the throughput it has, spends its whole retry budget
inside one exhausted pool, and never reaches the provider that could have served
the request. So the DEFAULT here is the conservative one -- every credential on
a provider starts in ONE shared, explicitly `unverified` domain -- and
independence has to be EARNED by observation.

HOW MEMBERSHIP IS LEARNED (never asserted)
------------------------------------------
Three signals, in descending order of strength:

  1. The provider NAMES the organisation. Groq's HTTP 413 body carries
     "in organization `org_...`" verbatim. That is the provider telling us the
     pool is shared, and it promotes the domain to `verified_shared` with a real
     identifier.
  2. A credential SUCCEEDS while a sibling's domain is rate-limited. If key A is
     throttled and key B answers within the observation window, they cannot be
     drawing on the same per-minute pool, and B is split into its own
     `verified_independent` domain.
  3. Nothing yet. The domain stays `unverified` and is reported as such. A
     caller that wants to know whether it has one pool or three must be able to
     see that we do not know, rather than reading a confident wrong answer.

Signal 2 needs an experiment to ever fire, and a cooling domain that suppressed
every one of its credentials would make that experiment impossible: B is never
tried, so independence is never learned, so B is never tried. `eligible`
therefore always permits ONE probe from a credential OTHER than the one that
caused the cooldown. That single attempt is what resolves the question.

WHAT IS RECORDED, AND WHAT IS NOT
---------------------------------
Per route: status, quota domain, observed input/output ceilings, recent latency,
SEPARATE 429 / 413 / 5xx / timeout counters, success rate, estimated cost, task
capabilities, last health check, last realistic probe, and cooldown. The failure
kinds are counted separately because the remedies are unrelated -- a 429 wants a
different domain, a 413 wants a bigger one, a 5xx wants backoff, and averaging
them into one "error rate" is how a capacity problem gets misdiagnosed as an
outage for an afternoon.

NEVER recorded: prompt text, completion text, or key material. A route is filed
under its FINGERPRINT (`db:<uuid>` / `env:<name>`), the same non-secret handle
the breaker and every log line already use, so `snapshot()` is safe to serve to
an operator console. All of this is operator telemetry and none of it may reach
a client.

NOTHING HERE CALLS A MODEL. Scoring is arithmetic over recorded observations
with weights that live in `config/llm_providers.ROUTE_SCORE_WEIGHTS`, so the
same inputs pick the same route every time. A scheduler that sampled would make
a latency regression indistinguishable from the scheduler having a different
opinion this morning, and that is the one property an operator needs when a
graph moves.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

import httpx

from app.config.llm_providers import (
    CAPACITY_INDEPENDENCE_WINDOW_S,
    DOMAIN_COOLDOWN_SECONDS,
    LATENCY_REFERENCE_MS,
    LOAD_SHARE_WINDOW,
    MIN_ROUTE_OBSERVATIONS,
    RECENT_LATENCY_SAMPLES,
    ROUTE_DISABLE_SECONDS,
    ROUTE_SCORE_WEIGHTS,
    ROUTING_DECISION_HISTORY,
    WORKLOAD_PROFILES,
    WorkloadClass,
    declared_context_limit,
    estimate_cost_usd,
    provider_order,
    workload_for_task,
)

logger = logging.getLogger(__name__)


# ── Vocabulary ───────────────────────────────────────────────────────────────


class DomainState(str, Enum):
    """How much we actually KNOW about a quota domain's membership."""

    #: Assumed shared because assuming shared is the safe direction. Not proven.
    UNVERIFIED = "unverified"
    #: The provider named an organisation, or two credentials were observed
    #: throttling together.
    VERIFIED_SHARED = "verified_shared"
    #: A credential succeeded while a sibling's domain was rate-limited.
    VERIFIED_INDEPENDENT = "verified_independent"


class RouteStatus(str, Enum):
    UNKNOWN = "unknown"
    UP = "up"
    DEGRADED = "degraded"
    #: The model id no longer resolves (404), or the credential was refused.
    #: A disabled route is surfaced, never silently dropped.
    DISABLED = "disabled"


class Classification(str, Enum):
    """What a route can actually be TRUSTED with, from realistic probing."""

    #: Production capable: it carried a realistic payload.
    GREEN = "green"
    #: Answers a toy prompt and refuses a real one. Usable for small tasks only.
    #: This is the state Groq was in while every health check reported it OK.
    YELLOW = "yellow"
    #: Unusable.
    RED = "red"
    #: Never probed. Deliberately distinct from GREEN -- an unprobed route
    #: reported as production-capable is the green tick this whole module
    #: exists to stop.
    UNPROBED = "unprobed"


class FailureKind(str, Enum):
    """The taxonomy the router acts on. One kind, one remedy."""

    MODEL_RETIRED = "model_retired"      # 404: the id is gone, no retry helps
    CAPACITY = "capacity"                # 413: too large for this domain
    THROTTLED = "throttled"              # 429: cool the domain, pick another
    CREDENTIAL = "credential"            # 401/403: disable and surface
    PAYMENT = "payment"                  # 402: account cannot pay
    SERVER = "server"                    # 5xx: backoff and fall back
    TIMEOUT = "timeout"                  # careful retry
    TRANSPORT = "transport"              # connection level
    MALFORMED = "malformed"              # the body was not what we parse


#: Groq names the organisation in its 413 body, verbatim:
#:   "Request too large for model `openai/gpt-oss-120b` in organization
#:    `org_01ky7js4vee6nb3d2fb48k62bj` service tier `on_demand` on tokens per
#:    minute (TPM): Limit 8000, Requested 11900"
#: That sentence is the provider TELLING us the pool is shared, which is a far
#: better source of truth than anything we could infer, so it is parsed rather
#: than guessed at. Re-measured 2026-08-24: all three Groq credentials returned
#: that same organisation and that same 8000 ceiling.
_ORG_ID_RE = re.compile(r"in organization[`'\" ]+([A-Za-z0-9_\-]+)", re.IGNORECASE)

#: OpenRouter names the ACCOUNT rather than an organisation, in the envelope of
#: its 402 body: `"user_id":"user_3Gu..."`. Measured 2026-08-24, and the
#: measurement OVERTURNED the working assumption. The three OpenRouter
#: credentials report three different `usage` figures from
#: `GET /api/v1/key`, which looked like three independent pools -- but every one
#: of them reports the SAME `creator_user_id`, and the 402 that refuses a large
#: prompt names `"limit_source":"openrouter_credits"`, which is the account's
#: balance. So the per-key counters are ACCOUNTING, not CAPACITY: they say who
#: spent what out of one shared wallet.
#:
#: This is precisely why membership is learned from what the provider NAMES and
#: never from a plausible-looking inference. Splitting these three on their
#: differing usage counters would have manufactured 3x the throughput that
#: exists, which is the expensive error this module was written to avoid.
_ACCOUNT_ID_RE = re.compile(r"\"user_id\"\s*:\s*\"([A-Za-z0-9_\-]+)\"")

#: "…on tokens per minute (TPM): Limit 8000, Requested 11900" -- the domain's
#: real per-minute input ceiling, stated by the provider. This is the number
#: that stops the next 12k request from being sent into an 8k pool.
_TPM_LIMIT_RE = re.compile(r"Limit (\d+), Requested (\d+)", re.IGNORECASE)

#: OpenRouter states the same fact in its own words, under HTTP 402:
#:   "Prompt tokens limit exceeded: 15330 > 2623"
#: The second number is the ceiling. It MOVES between calls (2623, 2517, 3398,
#: 326 were all observed within a minute) because it is derived from a live
#: shared balance rather than from a fixed per-minute allowance, which is why
#: the domain keeps the smallest one seen and lets an observed success raise it
#: back. A ceiling that only ever ratcheted down would pin the tier at its worst
#: moment forever.
_PROMPT_LIMIT_RE = re.compile(
    r"Prompt tokens limit exceeded:\s*(\d+)\s*>\s*(\d+)", re.IGNORECASE
)


def extract_organisation_id(body: str) -> str | None:
    """The quota-owning account a provider named, or None. Pure; unit-tested.

    Two spellings because two providers name the same thing differently. Groq
    calls it an organisation and OpenRouter calls it a user id; both identify
    the account whose limit was hit, which is the only thing a quota domain is.
    """
    if not body:
        return None
    match = _ORG_ID_RE.search(body)
    if match:
        return match.group(1)
    match = _ACCOUNT_ID_RE.search(body)
    return match.group(1) if match else None


def extract_request_ceiling(body: str) -> int | None:
    """The per-request input ceiling a provider named, or None. Pure."""
    if not body:
        return None
    match = _TPM_LIMIT_RE.search(body)
    if match:
        return int(match.group(1))
    match = _PROMPT_LIMIT_RE.search(body)
    return int(match.group(2)) if match else None


def response_body(exc: Exception) -> str:
    """The response text behind an httpx error, or "". Never raises.

    A body we cannot read tells us nothing, and a diagnostic that raises while
    diagnosing is strictly worse than one that returns nothing.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return ""
    try:
        return exc.response.text or ""
    except Exception:  # noqa: BLE001 -- diagnostics only
        return ""


def classify_failure(exc: Exception) -> FailureKind:
    """Map one exception to the remedy it implies. Pure; unit-tested.

    The split is by WHAT THE OPERATOR OR THE ROUTER MUST DO NEXT, not by HTTP
    family. 413 and 429 are both "rate_limit_exceeded" to Groq and mean opposite
    things to us: one wants a LARGER domain, the other wants a DIFFERENT one.
    """
    if isinstance(exc, httpx.TimeoutException):
        return FailureKind.TIMEOUT
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return FailureKind.MODEL_RETIRED
        if status == 413:
            return FailureKind.CAPACITY
        if status == 429:
            return FailureKind.THROTTLED
        if status == 402:
            # A 402 that names a PROMPT ceiling is a size refusal wearing a
            # payment status code, and the difference is fifteen minutes of a
            # working tier. Measured 2026-08-24: OpenRouter answered
            # "Prompt tokens limit exceeded: 15330 > 2623" to a realistic
            # extraction on all three credentials, while the SAME credentials
            # answered an ordinary prompt in 1.7 seconds. Read as PAYMENT the
            # router writes the account off (`trip_provider`) and every small
            # call that would have succeeded is refused for the cooldown, which
            # is a self-inflicted outage on the only tier still answering.
            #
            # The remedy hint in that body says "lower max_tokens / prompt
            # size" and only the second half is true here: the ceiling is on the
            # PROMPT, so reducing the output ceiling changes nothing.
            if extract_request_ceiling(response_body(exc)) is not None:
                return FailureKind.CAPACITY
            return FailureKind.PAYMENT
        if status in (401, 403):
            return FailureKind.CREDENTIAL
        if status >= 500:
            return FailureKind.SERVER
        return FailureKind.MALFORMED
    if isinstance(exc, httpx.HTTPError):
        return FailureKind.TRANSPORT
    if isinstance(exc, (KeyError, IndexError, ValueError)):
        return FailureKind.MALFORMED
    return FailureKind.TRANSPORT


def estimate_tokens(messages: Sequence[dict]) -> int:
    """Rough input size of a message list, in tokens.

    Four characters per token, which is close enough for the only decision it
    feeds: whether a request is plausibly larger than a domain's PROVEN ceiling.
    Deliberately arithmetic and deliberately not a tokeniser call -- the routing
    decision has to be reproducible offline and must not add a dependency that
    can fail while the providers are already failing.
    """
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
    return total // 4


# ── The registry ─────────────────────────────────────────────────────────────


@dataclass
class QuotaDomain:
    """One capacity pool. The unit a rate limit actually applies to."""

    provider: str
    domain_id: str
    state: DomainState = DomainState.UNVERIFIED
    #: Fingerprints observed billing this pool.
    members: set[str] = field(default_factory=set)
    #: The largest request the provider has SAID it will accept, learned from a
    #: 413 body. None means unknown, and unknown never blocks a route -- a
    #: ceiling we invented would be indistinguishable from one we measured.
    observed_request_ceiling: int | None = None
    #: The largest request actually OBSERVED to succeed. Raises the ceiling back
    #: up when an account is upgraded, so a one-off 413 cannot pin the domain
    #: small forever.
    observed_largest_success: int = 0
    cooldown_until: float = 0.0
    #: Which credential put the domain into cooldown. The OTHERS are what the
    #: independence experiment is run on.
    cooled_by: str | None = None
    throttle_events: int = 0

    def is_cooling(self) -> bool:
        return time.monotonic() < self.cooldown_until

    def request_ceiling(self) -> int | None:
        """The ceiling to route against: the stated one, raised by evidence."""
        if self.observed_request_ceiling is None:
            return None
        return max(self.observed_request_ceiling, self.observed_largest_success)


@dataclass
class RouteHealth:
    """One (provider, credential, model) route, as OBSERVED."""

    provider: str
    fingerprint: str
    model: str
    domain_id: str
    status: RouteStatus = RouteStatus.UNKNOWN
    classification: Classification = Classification.UNPROBED

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    #: Separate, on purpose. See the module docstring.
    rate_limited: int = 0        # 429
    too_large: int = 0           # 413
    server_errors: int = 0       # 5xx
    timeouts: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    recent_latency_ms: deque[float] = field(
        default_factory=lambda: deque(maxlen=RECENT_LATENCY_SAMPLES)
    )
    estimated_cost_usd: float = 0.0
    #: Task types this route has actually served. Capability is OBSERVED, so a
    #: route that has never produced valid JSON does not get to claim it can.
    task_capabilities: set[str] = field(default_factory=set)
    #: WorkloadClass -> bool, from `probe_llm_models`.
    probe_results: dict[str, bool] = field(default_factory=dict)
    last_health_check: float | None = None
    last_realistic_probe: float | None = None
    #: Observed OUTPUT ceiling, from an adaptive max_tokens reduction.
    observed_output_ceiling: int | None = None
    disabled_reason: str | None = None
    #: When the exclusion lapses. A route is condemned for a WINDOW, never
    #: forever: see `ROUTE_DISABLE_SECONDS`. The status stays DISABLED after it
    #: lapses so an operator can still see what happened; only the exclusion
    #: expires, which is the half-open probe.
    disabled_until: float = 0.0

    def outcomes(self) -> int:
        """Attempts that actually RESOLVED, one way or the other.

        Deliberately not `attempts`. That counter is incremented when a route is
        CHOSEN, which is what the load-share term needs, and it is not
        incremented by callers that only report results -- the hourly probe
        being one. Dividing a success count by a dispatch count that a probe
        never touched reported every measured route as having no success rate at
        all, which is the one column an operator reads first.
        """
        return self.successes + self.failures

    def success_rate(self) -> float | None:
        outcomes = self.outcomes()
        return self.successes / outcomes if outcomes else None

    def is_excluded(self) -> bool:
        """Is this route currently barred from a chain?

        Only while the window is still running. A route that could never come
        back would be dropped from every chain, so it could never earn the
        success that clears it, which is the permanently-wedged key all over
        again one layer up.
        """
        return (
            self.status is RouteStatus.DISABLED
            and time.monotonic() < self.disabled_until
        )

    def mean_latency_ms(self) -> float | None:
        if not self.recent_latency_ms:
            return None
        return sum(self.recent_latency_ms) / len(self.recent_latency_ms)


@dataclass
class _Registry:
    domains: dict[str, QuotaDomain] = field(default_factory=dict)
    routes: dict[str, RouteHealth] = field(default_factory=dict)
    #: Rolling window of the domain ids most recently ATTEMPTED. This is what
    #: makes distribution measurable rather than hoped for: a domain that has
    #: taken most of the recent traffic scores lower until the others catch up.
    recent_domains: deque[str] = field(
        default_factory=lambda: deque(maxlen=LOAD_SHARE_WINDOW)
    )
    #: fingerprint -> monotonic time of its last observed success. The other
    #: half of the independence experiment.
    last_success_at: dict[str, float] = field(default_factory=dict)
    #: The last routing decisions, newest last. Operator data.
    decisions: deque["RoutingDecision"] = field(
        default_factory=lambda: deque(maxlen=ROUTING_DECISION_HISTORY)
    )
    #: fingerprint -> the provider's own usage counter for that credential, as a
    #: string. RECORDED AND DELIBERATELY NOT ROUTED ON. See `_ACCOUNT_ID_RE`:
    #: OpenRouter's three keys carry three different counters and one shared
    #: balance, so a differing counter is evidence about BILLING and no evidence
    #: at all about capacity. It is kept because an operator comparing a bill
    #: against a route needs it, and it is kept out of `score_route` because
    #: routing on it would invent throughput that does not exist.
    usage_readings: dict[str, str] = field(default_factory=dict)


_registry = _Registry()


def reset() -> None:
    """Drop every observation. Used by tests and by an operator reset."""
    _registry.domains.clear()
    _registry.routes.clear()
    _registry.recent_domains.clear()
    _registry.last_success_at.clear()
    _registry.decisions.clear()
    _registry.usage_readings.clear()


def _shared_domain_id(provider: str) -> str:
    """The pessimistic default: ONE pool per provider, until proven otherwise."""
    return f"{provider}:shared"


def _independent_domain_id(provider: str, fingerprint: str) -> str:
    return f"{provider}:credential:{fingerprint}"


def _domain(provider: str, domain_id: str) -> QuotaDomain:
    domain = _registry.domains.get(domain_id)
    if domain is None:
        domain = QuotaDomain(provider=provider, domain_id=domain_id)
        _registry.domains[domain_id] = domain
    return domain


def route(provider: str, fingerprint: str, model: str = "") -> RouteHealth:
    """Get or create the route record for one credential."""
    existing = _registry.routes.get(fingerprint)
    if existing is not None:
        if model and not existing.model:
            existing.model = model
        return existing
    domain_id = _shared_domain_id(provider)
    _domain(provider, domain_id).members.add(fingerprint)
    created = RouteHealth(
        provider=provider, fingerprint=fingerprint, model=model, domain_id=domain_id
    )
    _registry.routes[fingerprint] = created
    return created


def domain_for(fingerprint: str) -> QuotaDomain | None:
    entry = _registry.routes.get(fingerprint)
    return _registry.domains.get(entry.domain_id) if entry else None


# ── Learning domain membership ───────────────────────────────────────────────


def observe_organisation(provider: str, fingerprint: str, org_id: str) -> None:
    """The provider named the organisation this credential bills.

    The strongest signal available, and the only one that is not an inference:
    every credential that reports the same organisation is in the same pool, so
    the domain is promoted to `verified_shared` and carries the real identifier
    rather than our placeholder.
    """
    entry = route(provider, fingerprint)
    domain_id = f"{provider}:org:{org_id}"
    target = _domain(provider, domain_id)
    target.state = DomainState.VERIFIED_SHARED
    previous = _registry.domains.get(entry.domain_id)
    if previous is not None and previous.domain_id != domain_id:
        previous.members.discard(fingerprint)
        # Carry the evidence across rather than relearning it. A ceiling
        # measured under the placeholder id was measured against this same pool.
        if target.observed_request_ceiling is None:
            target.observed_request_ceiling = previous.observed_request_ceiling
        target.observed_largest_success = max(
            target.observed_largest_success, previous.observed_largest_success
        )
    target.members.add(fingerprint)
    entry.domain_id = domain_id
    _drop_if_empty(previous)
    logger.info(
        "llm_capacity.domain_verified_shared provider=%s fingerprint=%s domain=%s "
        "members=%d",
        provider, fingerprint, domain_id, len(target.members),
    )


def _drop_if_empty(domain: QuotaDomain | None) -> None:
    """Forget a placeholder pool once its last member has moved out.

    Only the PLACEHOLDER, and only when it is empty. A `provider:shared` domain
    that every credential has left is a pool we assumed and then disproved;
    leaving it in the table reads as a fourth pool with nothing in it, and an
    operator counting domains would over-count the capacity. Any evidence it
    held was carried to the real domain before this runs.
    """
    if domain is None or domain.members:
        return
    if domain.domain_id == _shared_domain_id(domain.provider):
        _registry.domains.pop(domain.domain_id, None)


def _split_independent(provider: str, fingerprint: str, evidence: str) -> None:
    """Promote one credential out of a shared pool, on observed evidence."""
    entry = route(provider, fingerprint)
    domain_id = _independent_domain_id(provider, fingerprint)
    if entry.domain_id == domain_id:
        return
    previous = _registry.domains.get(entry.domain_id)
    if previous is not None:
        previous.members.discard(fingerprint)
    own = _domain(provider, domain_id)
    own.state = DomainState.VERIFIED_INDEPENDENT
    own.members.add(fingerprint)
    entry.domain_id = domain_id
    _drop_if_empty(previous)
    logger.info(
        "llm_capacity.domain_verified_independent provider=%s fingerprint=%s "
        "domain=%s evidence=%s",
        provider, fingerprint, domain_id, evidence,
    )


# ── Recording observations ───────────────────────────────────────────────────


def observe_attempt(provider: str, fingerprint: str, model: str = "") -> None:
    """One route was CHOSEN. Recorded before the outcome is known, because the
    load-share term is about where traffic was sent, not where it worked."""
    entry = route(provider, fingerprint, model)
    entry.attempts += 1
    _registry.recent_domains.append(entry.domain_id)


def observe_success(
    provider: str,
    fingerprint: str,
    *,
    latency_ms: float,
    task_type: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str = "",
) -> None:
    """A route answered. Also the moment independence can be proven."""
    entry = route(provider, fingerprint, model)
    entry.successes += 1
    entry.status = RouteStatus.UP
    entry.disabled_reason = None
    entry.disabled_until = 0.0
    entry.recent_latency_ms.append(latency_ms)
    entry.task_capabilities.add(task_type)
    entry.last_health_check = time.monotonic()
    entry.estimated_cost_usd += estimate_cost_usd(
        provider, prompt_tokens, completion_tokens
    )

    domain = _domain(provider, entry.domain_id)
    if prompt_tokens:
        # Evidence the pool is at least this big. Without it a single 413 would
        # pin a domain at a ceiling it has since outgrown, and no observation
        # could ever raise it again.
        domain.observed_largest_success = max(
            domain.observed_largest_success, prompt_tokens
        )
    now = time.monotonic()
    _registry.last_success_at[fingerprint] = now

    # SIGNAL 2. This credential answered while its pool was supposedly rate
    # limited by a DIFFERENT credential. Two credentials cannot draw on one
    # per-minute pool and have one of them throttled while the other serves, so
    # they are independent and the pessimistic default was wrong for this
    # provider. This is the only path that ever claims independence.
    if (
        domain.is_cooling()
        and domain.cooled_by is not None
        and domain.cooled_by != fingerprint
        and now - (domain.cooldown_until - DOMAIN_COOLDOWN_SECONDS)
        <= CAPACITY_INDEPENDENCE_WINDOW_S
    ):
        _split_independent(
            provider, fingerprint, evidence=f"succeeded while {domain.cooled_by} throttled"
        )


def observe_failure(
    provider: str,
    fingerprint: str,
    exc: Exception,
    *,
    latency_ms: float,
    task_type: str,
    requested_tokens: int = 0,
    model: str = "",
) -> FailureKind:
    """A route failed. Records the KIND, and acts on what the kind implies."""
    entry = route(provider, fingerprint, model)
    kind = classify_failure(exc)
    entry.failures += 1
    entry.recent_latency_ms.append(latency_ms)
    entry.last_health_check = time.monotonic()
    entry.by_kind[kind.value] = entry.by_kind.get(kind.value, 0) + 1
    body = response_body(exc)
    domain = _domain(provider, entry.domain_id)

    org_id = extract_organisation_id(body)
    if org_id:
        observe_organisation(provider, fingerprint, org_id)
        domain = _domain(provider, entry.domain_id)

    if kind is FailureKind.THROTTLED:
        entry.rate_limited += 1
        entry.status = RouteStatus.DEGRADED
        domain.throttle_events += 1
        domain.cooldown_until = time.monotonic() + DOMAIN_COOLDOWN_SECONDS
        domain.cooled_by = fingerprint
    elif kind is FailureKind.CAPACITY:
        entry.too_large += 1
        entry.status = RouteStatus.DEGRADED
        ceiling = extract_request_ceiling(body)
        if ceiling is None and requested_tokens:
            # The provider refused this size and named no number. The only thing
            # we have actually learned is that this size does not fit, so the
            # ceiling is recorded as strictly BELOW it rather than invented.
            ceiling = max(requested_tokens - 1, 0)
        if ceiling is not None:
            previous = domain.observed_request_ceiling
            domain.observed_request_ceiling = (
                ceiling if previous is None else min(previous, ceiling)
            )
        # A 413 naming an organisation is the provider saying the ceiling is the
        # POOL's. Nothing here is asserted: if it named no organisation the
        # domain keeps whatever state it already had.
    elif kind is FailureKind.SERVER:
        entry.server_errors += 1
        entry.status = RouteStatus.DEGRADED
    elif kind is FailureKind.TIMEOUT:
        entry.timeouts += 1
        entry.status = RouteStatus.DEGRADED
    elif kind is FailureKind.MODEL_RETIRED:
        # A retired id is not a transient failure and no amount of retrying or
        # key rotation reaches it. Three tiers have gone dark this way, each time
        # looking like a slow degradation rather than a configuration fact.
        entry.status = RouteStatus.DISABLED
        entry.classification = Classification.RED
        entry.disabled_reason = f"model {entry.model or 'id'} returned 404"
        entry.disabled_until = time.monotonic() + ROUTE_DISABLE_SECONDS
    elif kind in (FailureKind.CREDENTIAL, FailureKind.PAYMENT):
        entry.status = RouteStatus.DISABLED
        entry.disabled_reason = f"account level {kind.value}"
        entry.disabled_until = time.monotonic() + ROUTE_DISABLE_SECONDS

    logger.warning(
        "llm_capacity.failure task_type=%s provider=%s fingerprint=%s domain=%s "
        "kind=%s latency_ms=%.0f requested_tokens=%d ceiling=%s",
        task_type, provider, fingerprint, entry.domain_id, kind.value, latency_ms,
        requested_tokens, domain.request_ceiling(),
    )
    return kind


def observe_usage_reading(provider: str, fingerprint: str, reading: str) -> None:
    """Record a credential's own usage counter. NEVER changes a domain.

    It is recorded and not acted on, and the restraint is the point. Three
    OpenRouter credentials reporting three different `usage` figures is exactly
    what three independent pools would look like, and it is what one shared
    wallet with per-key accounting looks like too. Measured 2026-08-24, it is
    the second: all three name the same `creator_user_id`, and the 402 that
    refuses a large prompt names `openrouter_credits` as the limit source.

    Independence is claimed from ONE signal only -- a credential serving while a
    sibling's domain is throttled (`observe_success`) -- because that is the
    only observation a shared pool cannot produce.
    """
    route(provider, fingerprint)
    _registry.usage_readings[fingerprint] = str(reading)


def observe_output_ceiling(provider: str, fingerprint: str, ceiling: int) -> None:
    """The provider stated a smaller output ceiling than we asked for."""
    entry = route(provider, fingerprint)
    entry.observed_output_ceiling = (
        ceiling
        if entry.observed_output_ceiling is None
        else min(entry.observed_output_ceiling, ceiling)
    )


def observe_probe(
    provider: str,
    fingerprint: str,
    model: str,
    results: dict[WorkloadClass, bool],
) -> Classification:
    """Record a realistic capability probe and classify the route.

    GREEN requires the route to have carried a payload of the SIZE and SHAPE the
    product actually sends. The distinction between GREEN and YELLOW is the
    whole point of the exercise: Groq answered every toy health check in 580ms
    while returning 413 to every real extraction, and a probe that passes on
    input unlike the input the product sends is not a probe, it is a green tick.
    """
    entry = route(provider, fingerprint, model)
    entry.probe_results = {w.value: ok for w, ok in results.items()}
    entry.last_health_check = time.monotonic()
    realistic = [
        WorkloadClass.LARGE,
        WorkloadClass.LONG_CONTEXT,
        WorkloadClass.RESUME_EXTRACTION,
    ]
    if any(w in results for w in realistic):
        entry.last_realistic_probe = time.monotonic()

    if not any(results.values()):
        # RED, and deliberately NOT disabled. A probe measures CAPABILITY; only
        # the provider gets to condemn a credential, and `observe_failure`
        # already does that from what it actually said (404, 401, 402 payment).
        #
        # The distinction was found the moment this ran: every Gemini key failed
        # all three classes with HTTP 429 free-tier quota, which clears. Letting
        # a probe disable on that would bar the whole tier for fifteen minutes
        # over a per-minute limit -- a write-off outliving its problem, which
        # this product has already been bitten by once at the key breaker. RED
        # costs the route the entire capability weight, which deprioritises it
        # hard without removing it from a chain it may be able to serve again in
        # a minute.
        entry.classification = Classification.RED
    elif all(results.get(w, False) for w in realistic if w in results) and results.get(
        WorkloadClass.STRUCTURED_JSON, True
    ):
        entry.classification = Classification.GREEN
        entry.status = RouteStatus.UP
    else:
        entry.classification = Classification.YELLOW
        entry.status = RouteStatus.UP

    # A route that carried a workload class has DEMONSTRATED that capability,
    # which is a stronger claim than a table asserting it.
    for workload, ok in results.items():
        if ok:
            entry.task_capabilities.add(f"workload:{workload.value}")
            profile = WORKLOAD_PROFILES[workload]
            domain = _domain(provider, entry.domain_id)
            domain.observed_largest_success = max(
                domain.observed_largest_success, int(profile["approx_input_tokens"])
            )
    return entry.classification


# ── Scoring and selection ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RouteScore:
    """Why a route was ranked where it was. Structured telemetry, never text."""

    fingerprint: str
    provider: str
    domain_id: str
    score: float
    terms: dict[str, float]
    eligible: bool
    reason: str


#: The terms `score_route` ADDS, and the terms it SUBTRACTS. Named once, here,
#: because two readers need them: the arithmetic that produces the score and the
#: explanation that says which term decided it. Written twice they would drift,
#: and a decision log that named the wrong reason is worse than none.
POSITIVE_TERMS = (
    "preference", "capability", "capacity_headroom", "success_rate", "latency",
)
NEGATIVE_TERMS = (
    "quota_pressure", "cooldown", "load_share", "cost", "unverified_domain",
)


def _load_share(domain_id: str) -> float:
    window = _registry.recent_domains
    if not window:
        return 0.0
    return sum(1 for d in window if d == domain_id) / len(window)


def _fits(domain: QuotaDomain, input_tokens: int) -> bool:
    """Never send a request larger than a domain has PROVEN it can take.

    This is the 413 storm, stated as a predicate. The router used to route on
    hope: the chain said Groq first, so a 12k extraction went to an 8k pool on
    every single call, failed, and burned an attempt out of a budget the
    provider that could have served it needed. An UNKNOWN ceiling is not a
    refusal -- a limit we never observed cannot be enforced without inventing
    it, and inventing one is the same error in the other direction.
    """
    ceiling = domain.request_ceiling()
    if ceiling is None or input_tokens <= 0:
        return True
    return input_tokens <= ceiling


def _domain_siblings(entry: "RouteHealth") -> list["RouteHealth"]:
    """Every route sharing this one's quota domain, itself included.

    Falls back to the route alone when the domain is unknown, which is the
    honest answer for a credential nothing has observed yet.
    """
    domain = _registry.domains.get(entry.domain_id)
    if domain is None:
        return [entry]
    siblings = [
        _registry.routes[fingerprint]
        for fingerprint in domain.members
        if fingerprint in _registry.routes
    ]
    return siblings or [entry]


def _domain_outcomes(entry: "RouteHealth") -> int:
    return sum(sibling.outcomes() for sibling in _domain_siblings(entry))


def _domain_success_rate(entry: "RouteHealth") -> float | None:
    """The pool's success rate. See the comment at its call site for why this is
    not read per credential."""
    siblings = _domain_siblings(entry)
    total = sum(sibling.outcomes() for sibling in siblings)
    if not total:
        return None
    successes = sum(sibling.successes for sibling in siblings)
    return successes / total


def _domain_latency_ms(entry: "RouteHealth") -> float | None:
    """Mean latency across the pool. See its call site for why not per key."""
    siblings = _domain_siblings(entry)
    measured = [
        (sibling.mean_latency_ms(), sibling.outcomes())
        for sibling in siblings
        if sibling.mean_latency_ms() is not None
    ]
    if not measured:
        return None
    total = sum(count for _mean, count in measured) or len(measured)
    return sum(mean * max(count, 1) for mean, count in measured) / total


def score_route(
    entry: RouteHealth,
    *,
    task_type: str,
    input_tokens: int,
    needs_json: bool,
    preference_rank: int,
    preference_depth: int,
) -> RouteScore:
    """Rank one route for one request. Pure arithmetic over recorded state.

    The inputs are exactly the ones the brief names: task complexity and
    required context size (`input_tokens` against the domain's proven ceiling),
    structured-output need, latency, observed capability, current capacity,
    recent failure rate, quota pressure, cost, and cooldown. Every weight is
    DATA in `config/llm_providers.ROUTE_SCORE_WEIGHTS`, so a tuning change is a
    reviewable diff in the policy table rather than a literal buried in a
    scheduler.
    """
    weights = ROUTE_SCORE_WEIGHTS
    domain = _domain(entry.provider, entry.domain_id)
    terms: dict[str, float] = {}

    # Provider preference, from the task's MEASURED route order. Normalised so
    # the first tier scores 1.0 and the last 0.0.
    span = max(preference_depth - 1, 1)
    terms["preference"] = max(0.0, 1.0 - (preference_rank / span))

    # Capability. Unknown sits at the neutral midpoint deliberately: a route we
    # have never exercised must not outrank one that has proven itself, and must
    # not be condemned for never having been tried either.
    if entry.classification is Classification.GREEN:
        terms["capability"] = 1.0
    elif entry.classification is Classification.YELLOW:
        # Fine for a small ask, wrong for a real one. The penalty is conditional
        # on the SIZE of this request rather than blanket.
        terms["capability"] = 0.25 if input_tokens > 2000 else 0.8
    elif entry.classification is Classification.RED:
        terms["capability"] = 0.0
    else:
        terms["capability"] = 0.5
    if needs_json and f"workload:{WorkloadClass.STRUCTURED_JSON.value}" in (
        entry.task_capabilities
    ):
        terms["capability"] = min(1.0, terms["capability"] + 0.1)

    # Capacity headroom against the PROVEN ceiling.
    ceiling = domain.request_ceiling()
    if ceiling is None or input_tokens <= 0:
        terms["capacity_headroom"] = 0.5
    elif input_tokens >= ceiling:
        terms["capacity_headroom"] = 0.0
    else:
        terms["capacity_headroom"] = 1.0 - (input_tokens / ceiling)

    # Success rate, BLENDED toward the neutral prior until there is enough of
    # it to be evidence. One failed call is not a 0% route, and treating it as
    # one moves a less-preferred provider to the front on a single sample --
    # which also robs the per-key breaker of the second CONSECUTIVE failure it
    # needs, so a genuinely dead credential hides behind a rerouted chain.
    # READ AT DOMAIN GRANULARITY, not per credential, and this is the fix for a
    # measured regression rather than a refinement.
    #
    # Per credential, the first key to be used records a success, its rate rises
    # above the neutral prior, and it therefore wins the next call too. Measured
    # 2026-08-24: nine consecutive calls all went to `env:groq:1` while its two
    # siblings sat idle. Rich-get-richer, and it silently deleted the
    # round-robin the router has always had so that no one key is hammered into
    # a 429. A shared TOKEN pool does not make per-key RATE limits shared.
    #
    # Siblings on one quota domain answer the same account with the same model,
    # so there is nothing about one of them that the others do not share: their
    # success history is genuinely the domain's, and reading it there makes them
    # tie exactly. `rank` sorts stably, so the caller's round-robin then decides,
    # and traffic spreads. Health still differentiates ACROSS domains, which is
    # the comparison that carries real information.
    rate = _domain_success_rate(entry)
    if rate is None:
        terms["success_rate"] = 0.5
    else:
        confidence = (
            min(_domain_outcomes(entry), MIN_ROUTE_OBSERVATIONS)
            / MIN_ROUTE_OBSERVATIONS
        )
        terms["success_rate"] = (0.5 * (1.0 - confidence)) + (rate * confidence)

    # DOMAIN granularity again, and for the same measured reason as the success
    # rate above. Latency is a property of the ACCOUNT and the MODEL -- the same
    # endpoint, the same tier, the same weights -- so a difference between two
    # credentials on one domain is noise. Read per credential it is worse than
    # noise: the first key ever called is the only one with a measurement, its
    # neighbours sit on the neutral prior, and a good measurement beats a
    # neutral one. So the first key wins every subsequent call and its siblings
    # are never measured, which is self-fulfilling.
    #
    # That is exactly what happened. With the success term already pooled, nine
    # consecutive calls STILL went to `env:groq:1`, on a latency edge of 0.75
    # against 0.5 worth about five points. Pooling both terms makes siblings tie
    # outright, and the stable sort hands the tie to the round-robin.
    mean = _domain_latency_ms(entry)
    terms["latency"] = (
        0.5 if mean is None else 1.0 / (1.0 + (mean / LATENCY_REFERENCE_MS))
    )

    # Quota pressure and cooldown are separate terms because they are separate
    # facts: pressure is how often this domain has been throttled at all, and
    # cooldown is whether it is throttled RIGHT NOW.
    terms["quota_pressure"] = min(1.0, domain.throttle_events / 5.0)
    terms["cooldown"] = 1.0 if domain.is_cooling() else 0.0
    terms["load_share"] = _load_share(entry.domain_id)
    # Cost is real but small: correctness and availability outrank a fraction of
    # a cent, and the weight in the table says so explicitly.
    terms["cost"] = min(1.0, entry.estimated_cost_usd)
    # An unverified domain is a mild penalty, never a refusal. It exists so that
    # a pool we have actually measured wins a tie against one we have only
    # assumed, which is the direction that gets membership learned faster.
    terms["unverified_domain"] = (
        1.0 if domain.state is DomainState.UNVERIFIED else 0.0
    )

    score = sum(weights[name] * terms[name] for name in POSITIVE_TERMS)
    score -= sum(weights[name] * terms[name] for name in NEGATIVE_TERMS)

    eligible = True
    reason = "ok"
    if entry.is_excluded():
        eligible, reason = False, entry.disabled_reason or "disabled"
    elif not _fits(domain, input_tokens):
        eligible, reason = (
            False,
            f"request {input_tokens} tokens exceeds proven ceiling {ceiling}",
        )

    return RouteScore(
        fingerprint=entry.fingerprint,
        provider=entry.provider,
        domain_id=entry.domain_id,
        # Rounded so two genuinely equal routes compare equal and the sort stays
        # stable. Float noise reordering a chain would make the router look
        # non-deterministic for no reason anybody could find.
        # ROUNDED TO A COARSE BUCKET, and the coarseness is the point.
        #
        # At six decimal places two credentials on the SAME quota domain never
        # tie: the first one used records a success, its `success_rate` term
        # rises above the neutral prior, and it therefore wins the next call as
        # well. Measured 2026-08-24 -- nine consecutive calls all went to
        # `env:groq:1` while its two siblings sat idle. That is rich-get-richer,
        # and it silently deleted the round-robin the router has always had to
        # keep one key from being hammered into a 429. A shared TOKEN pool does
        # not make per-key RATE limits shared.
        #
        # Rounding to whole points makes routes that differ only by a little
        # history tie, and `rank` sorts STABLY, so the caller's existing
        # round-robin order decides the tie. Determinism is preserved exactly:
        # the bucket is fixed data, not a sample, so the same registry state and
        # the same input list still produce the same output list. A route that
        # is genuinely better -- a different capability, a cooldown, a capacity
        # wall -- moves by far more than one point and still wins outright.
        score=round(score, 0),
        terms={name: round(value, 4) for name, value in terms.items()},
        eligible=eligible,
        reason=reason,
    )


def rank_routes(
    candidates: Sequence[tuple[str, str]],
    *,
    task_type: str,
    input_tokens: int,
    needs_json: bool = False,
    model_by_provider: dict[str, str] | None = None,
) -> list[RouteScore]:
    """Score `(provider, fingerprint)` pairs, best first. Deterministic.

    Ties keep the caller's order, which is what preserves the task's provider
    preference and the existing round-robin underneath the scoring. The sort is
    stable and the score is rounded, so the same registry state and the same
    input list always produce the same output list.
    """
    order = provider_order(task_type)
    depth = len(order)
    models = model_by_provider or {}
    scores: list[RouteScore] = []
    for provider, fingerprint in candidates:
        entry = route(provider, fingerprint, models.get(provider, ""))
        rank = order.index(provider) if provider in order else depth
        scores.append(
            score_route(
                entry,
                task_type=task_type,
                input_tokens=input_tokens,
                needs_json=needs_json,
                preference_rank=rank,
                preference_depth=depth,
            )
        )
    return sorted(scores, key=lambda s: -s.score)


def eligible(scores: Iterable[RouteScore]) -> list[RouteScore]:
    """Keep the routes that can actually serve the request.

    If NOTHING is eligible the whole list is returned rather than an empty one.
    An empty chain is an immediate `LLMUnavailableError` with no attempt made,
    which is a worse answer than one honest attempt against the largest pool we
    know of -- our ceilings are observations, and an observation can be stale.
    """
    ranked = list(scores)
    keep = [s for s in ranked if s.eligible]
    return keep or ranked


@dataclass(frozen=True)
class RoutingDecision:
    """One selection, as STRUCTURED DATA rather than a sentence in a log.

    Identifiers, counts and timings only, and the same rule as
    `agent_execution_traces`: no prompt text, no completion text, no key
    material. It is operator data and none of it may reach a client.

    It carries the alternatives and the exclusions on purpose. The outcome alone
    cannot answer the question anybody actually asks after an incident -- why
    this route and not that one -- and without the counterfactual the available
    answer is a guess about the scheduler, which is the doubt determinism was
    bought to remove.
    """

    task_type: str
    input_tokens: int
    workload: str
    chosen_fingerprint: str | None
    chosen_provider: str | None
    chosen_domain: str | None
    chosen_score: float | None
    #: The two terms that actually decided it, largest weighted contribution
    #: first. Derived arithmetically from the same weights the score used.
    why: tuple[str, ...]
    #: Domain ids in ranked order, deduplicated. This is what "provider
    #: diversity was preserved" looks like as a value rather than a claim.
    alternatives: tuple[str, ...]
    considered: int
    #: (fingerprint, measured reason) for every route dropped from the chain.
    excluded: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "input_tokens": self.input_tokens,
            "workload": self.workload,
            "chosen_fingerprint": self.chosen_fingerprint,
            "chosen_provider": self.chosen_provider,
            "chosen_domain": self.chosen_domain,
            "chosen_score": self.chosen_score,
            "why": list(self.why),
            "alternatives": list(self.alternatives),
            "considered": self.considered,
            "excluded": [
                {"fingerprint": fingerprint, "reason": reason}
                for fingerprint, reason in self.excluded
            ],
        }


def deciding_terms(terms: dict[str, float], limit: int = 2) -> tuple[str, ...]:
    """The terms that carried the most weight in one score. Pure; deterministic.

    Weighted contribution, not raw term value: a term at 1.0 with a weight of 3
    explains nothing and a term at 0.4 with a weight of 45 explains a lot.
    Negative terms are named with a leading minus so an operator can tell "chosen
    because it is capable" from "chosen because nothing else was cooling down".
    """
    contributions: list[tuple[float, str]] = []
    for name in POSITIVE_TERMS:
        contributions.append((ROUTE_SCORE_WEIGHTS[name] * terms.get(name, 0.0), name))
    for name in NEGATIVE_TERMS:
        contributions.append(
            (ROUTE_SCORE_WEIGHTS[name] * terms.get(name, 0.0), f"-{name}")
        )
    # Sorted by magnitude, then by name, so equal contributions never reorder
    # between two runs with identical inputs.
    contributions.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(name for value, name in contributions[:limit] if value > 0.0)


def plan(
    candidates: Sequence[tuple[str, str]],
    *,
    task_type: str,
    input_tokens: int,
    needs_json: bool = False,
    model_by_provider: dict[str, str] | None = None,
) -> list[RouteScore]:
    """The full selection: score, filter on proven capacity, record the decision.

    Three filters, in order, and each one keeps the whole list rather than
    returning nothing:

      1. `eligible` drops what the registry has PROVEN cannot serve this size.
      2. `probe_allowance` drops the one credential that just caused its
         domain's cooldown, while keeping its siblings so the independence
         experiment can still run.

    An empty chain would be an immediate unavailability with no attempt made,
    which is a worse answer than one honest attempt against a stale observation.
    """
    ranked = rank_routes(
        candidates,
        task_type=task_type,
        input_tokens=input_tokens,
        needs_json=needs_json,
        model_by_provider=model_by_provider,
    )
    excluded: list[tuple[str, str]] = [
        (entry.fingerprint, entry.reason) for entry in ranked if not entry.eligible
    ]
    chosen = eligible(ranked)

    permitted = [entry for entry in chosen if probe_allowance(entry.fingerprint)]
    if permitted and len(permitted) < len(chosen):
        excluded.extend(
            (entry.fingerprint, "caused the current domain cooldown")
            for entry in chosen
            if entry not in permitted
        )
        chosen = permitted

    head = chosen[0] if chosen else None
    decision = RoutingDecision(
        task_type=task_type,
        input_tokens=input_tokens,
        workload=workload_for_task(task_type).value,
        chosen_fingerprint=head.fingerprint if head else None,
        chosen_provider=head.provider if head else None,
        chosen_domain=head.domain_id if head else None,
        chosen_score=head.score if head else None,
        why=deciding_terms(head.terms) if head else (),
        alternatives=tuple(dict.fromkeys(entry.domain_id for entry in chosen)),
        considered=len(ranked),
        excluded=tuple(excluded),
    )
    _registry.decisions.append(decision)

    if head is not None:
        # The routing decision, as structured telemetry: identifiers, counts and
        # timings only. No prompt, no completion, no key.
        logger.info(
            "llm_capacity.route_selected task_type=%s provider=%s fingerprint=%s "
            "domain=%s score=%.4f input_tokens=%d workload=%s why=%s "
            "alternatives=%s eligible=%d of=%d terms=%s",
            task_type, head.provider, head.fingerprint, head.domain_id, head.score,
            input_tokens, decision.workload, ",".join(decision.why),
            ",".join(decision.alternatives), len(chosen), len(ranked), head.terms,
        )
    for fingerprint, reason in excluded:
        logger.info(
            "llm_capacity.route_excluded task_type=%s fingerprint=%s reason=%s",
            task_type, fingerprint, reason,
        )
    return chosen


def recent_decisions() -> list[dict[str, Any]]:
    """The routing decisions still in the ring, oldest first. Operator data."""
    return [decision.as_dict() for decision in _registry.decisions]


def last_decision() -> RoutingDecision | None:
    return _registry.decisions[-1] if _registry.decisions else None


def probe_allowance(fingerprint: str) -> bool:
    """May this credential be tried while its domain is cooling?

    Yes, if it is NOT the credential that caused the cooldown. That single
    attempt is the independence experiment, and without it the question can
    never be answered: a cooling domain that suppressed all of its members would
    leave a genuinely independent OpenRouter key untried forever, and the
    registry would keep reporting `unverified` while real capacity sat idle.
    """
    domain = domain_for(fingerprint)
    if domain is None or not domain.is_cooling():
        return True
    return domain.cooled_by != fingerprint


# ── Operator surface ─────────────────────────────────────────────────────────


def snapshot() -> dict[str, Any]:
    """Everything the registry knows, safe to serve to an operator console.

    Carries fingerprints, counts and timings. It carries NO key material and no
    prompt or completion text, and it is operator data: none of it may reach a
    client, exactly like `interview_telemetry.conversation_summary`.
    """
    now = time.monotonic()
    return {
        "domains": {
            domain_id: {
                "provider": domain.provider,
                "state": domain.state.value,
                "members": sorted(domain.members),
                "member_count": len(domain.members),
                "observed_request_ceiling": domain.observed_request_ceiling,
                "observed_largest_success": domain.observed_largest_success,
                "effective_request_ceiling": domain.request_ceiling(),
                "throttle_events": domain.throttle_events,
                "cooling": domain.is_cooling(),
                "cooldown_remaining_s": (
                    round(max(0.0, domain.cooldown_until - now), 1)
                ),
            }
            for domain_id, domain in sorted(_registry.domains.items())
        },
        "routes": {
            fingerprint: {
                "provider": entry.provider,
                "model": entry.model,
                "domain": entry.domain_id,
                "status": entry.status.value,
                "classification": entry.classification.value,
                "attempts": entry.attempts,
                "successes": entry.successes,
                "failures": entry.failures,
                "success_rate": (
                    round(entry.success_rate(), 3)
                    if entry.success_rate() is not None
                    else None
                ),
                "rate_limited_429": entry.rate_limited,
                "too_large_413": entry.too_large,
                "server_5xx": entry.server_errors,
                "timeouts": entry.timeouts,
                "by_kind": dict(sorted(entry.by_kind.items())),
                "mean_latency_ms": (
                    round(entry.mean_latency_ms(), 1)
                    if entry.mean_latency_ms() is not None
                    else None
                ),
                "estimated_cost_usd": round(entry.estimated_cost_usd, 6),
                "task_capabilities": sorted(entry.task_capabilities),
                "probe_results": dict(sorted(entry.probe_results.items())),
                # Reported BESIDE the measured ceiling and never used to route.
                # Groq publishes 131072 for gpt-oss-120b and refused 11900,
                # because the binding constraint was the organisation's
                # per-minute pool and not the model's context at all.
                "declared_context_limit": declared_context_limit(entry.provider),
                # Billing telemetry, not capacity. See `observe_usage_reading`.
                "usage_reading": _registry.usage_readings.get(fingerprint),
                "observed_output_ceiling": entry.observed_output_ceiling,
                "last_health_check_age_s": (
                    round(now - entry.last_health_check, 1)
                    if entry.last_health_check is not None
                    else None
                ),
                "last_realistic_probe_age_s": (
                    round(now - entry.last_realistic_probe, 1)
                    if entry.last_realistic_probe is not None
                    else None
                ),
                "disabled_reason": entry.disabled_reason,
                # Two fields, because they answer two questions: what happened
                # to this route, and is it barred right now. A route that was
                # condemned an hour ago is back in the chain and an operator
                # must be able to see both halves of that.
                "excluded_now": entry.is_excluded(),
                "exclusion_remaining_s": round(
                    max(0.0, entry.disabled_until - now), 1
                ),
            }
            for fingerprint, entry in sorted(_registry.routes.items())
        },
        "recent_domain_share": {
            domain_id: round(_load_share(domain_id), 3)
            for domain_id in sorted(set(_registry.recent_domains))
        },
        "recent_decisions": recent_decisions(),
    }


def workload_for(task_type: str) -> WorkloadClass:
    """The workload class a task type belongs to. Re-exported for the probe."""
    return workload_for_task(task_type)
