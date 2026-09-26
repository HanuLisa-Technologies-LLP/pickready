"""Context budgets by PURPOSE, and the rot discipline that goes with them.

RPN-AI-UP-001 W4.4 and W4.5. Provenance for every number and every rule in this
module is that specification; the reasoning is reproduced rather than cited so a
reader does not have to fetch it.

WHY NOT ONE `max_context`
-------------------------
A single ceiling says nothing about what is allowed to spend it. In practice one
slot always eats the others: retrieval returns twenty chunks, the evidence fills
the window, and the system policy that decides how a grade is written is the
thing that gets squeezed. A budget PER SLOT is what makes the trade explicit,
and it is what lets a caller be told which purpose overran rather than being
told that the prompt was too long.

    system policy        1k
    application state    2k     (tenant, job, candidate, stage, versions)
    retrieved evidence   4k
    memory               2k
    tool output          3k
    working scratch      2k

DO NOT ASK THE MODEL WHAT THE APPLICATION ALREADY KNOWS
-------------------------------------------------------
`authoritative_state` exists to make that concrete. A tenant id, a job id, a
candidate id, a role, a scorecard version, a permission and a workflow stage are
facts the application holds; a model asked to derive them from a transcript will
sometimes derive them wrong, and nothing downstream can tell a derived id from a
real one. They are INJECTED, as a labelled block, and the model is never asked
to infer one.

CONTEXT ROT: FOUR RELEVANT CHUNKS BEAT TWENTY ADEQUATE ONES
-------------------------------------------------------------
The measurement W4.5 rests on is that performance degrades non uniformly with
input length at constant task difficulty, and that a SINGLE topically related
distractor measurably reduces accuracy. The operational consequence here is
`EVIDENCE_PREFERRED_UNITS`: the evidence slot is capped by unit count as well as
by tokens, so a retriever that returns twenty adequate chunks cannot spend the
whole slot proving it.

COMPRESS THE STATE SUMMARY, NEVER THE EVIDENCE
-----------------------------------------------
This is the load-bearing asymmetry and it is why `fit_evidence` and
`fit_summary` are different functions rather than one function with a flag.
Evidence is the source of truth: a summary of an answer is not evidence of what
someone said, and the transcript route in this product already turns on that
distinction. So evidence is fitted by DROPPING WHOLE UNITS and recording each
drop, and a single evidence unit that does not fit on its own is REFUSED rather
than shortened. A state summary is derived text by construction and may be
compressed at sentence boundaries.

NOTHING IS EVER CUT MID SENTENCE
---------------------------------
Cutting the assembled string hands a model half a sentence, and a model handed
half a sentence completes it from its own priors, into text a grade is written
from. Every operation in this module removes WHOLE units: a whole chunk, a whole
message, a whole sentence. When no whole unit can be removed the answer is a
raised `ContextOverBudget`, never a slice.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "SLOT_SYSTEM_POLICY",
    "SLOT_APPLICATION_STATE",
    "SLOT_RETRIEVED_EVIDENCE",
    "SLOT_MEMORY",
    "SLOT_TOOL_OUTPUT",
    "SLOT_WORKING_SCRATCH",
    "SLOT_TOKEN_BUDGETS",
    "COMPRESSIBLE_SLOTS",
    "TOTAL_CONTEXT_BUDGET_TOKENS",
    "EVIDENCE_PREFERRED_UNITS",
    "OMISSION_MARKER",
    "ContextOverBudget",
    "Fitted",
    "Assembled",
    "Compression",
    "estimate_tokens",
    "budget_for",
    "sentences",
    "fit_evidence",
    "fit_summary",
    "authoritative_state",
    "assemble",
    "compress_messages",
]


# ── The slots ────────────────────────────────────────────────────────────────

SLOT_SYSTEM_POLICY = "system_policy"
SLOT_APPLICATION_STATE = "application_state"
SLOT_RETRIEVED_EVIDENCE = "retrieved_evidence"
SLOT_MEMORY = "memory"
SLOT_TOOL_OUTPUT = "tool_output"
SLOT_WORKING_SCRATCH = "working_scratch"

#: W4.4's table, in tokens. Written as multiples of 1024 because that is what
#: "1k" means for a context window, and because a round decimal thousand here
#: would be a different number pretending to be the same one.
SLOT_TOKEN_BUDGETS: dict[str, int] = {
    SLOT_SYSTEM_POLICY: 1 * 1024,
    SLOT_APPLICATION_STATE: 2 * 1024,
    SLOT_RETRIEVED_EVIDENCE: 4 * 1024,
    SLOT_MEMORY: 2 * 1024,
    SLOT_TOOL_OUTPUT: 3 * 1024,
    SLOT_WORKING_SCRATCH: 2 * 1024,
}

#: What a fully budgeted prompt costs. Read by the router's cost ceiling as the
#: prompt allowance a legitimate maximal call is entitled to, so the two
#: ceilings are derived from one number rather than from two guesses.
TOTAL_CONTEXT_BUDGET_TOKENS = sum(SLOT_TOKEN_BUDGETS.values())

#: The slots whose content is DERIVED and may therefore be compressed.
#: `SLOT_RETRIEVED_EVIDENCE` is deliberately absent, and so is
#: `SLOT_SYSTEM_POLICY`: compressing a policy silently removes a rule, and the
#: failure mode is a rule nobody can see was dropped.
COMPRESSIBLE_SLOTS: frozenset[str] = frozenset(
    {
        SLOT_APPLICATION_STATE,
        SLOT_MEMORY,
        SLOT_TOOL_OUTPUT,
        SLOT_WORKING_SCRATCH,
    }
)

#: Four, from W4.5. A ceiling on the NUMBER of evidence units, independent of
#: the token ceiling, because twenty adequate chunks can fit inside four
#: thousand tokens and still cost accuracy through the distractor effect.
EVIDENCE_PREFERRED_UNITS = 4

#: Four serialized characters per token: the same conservative estimate
#: `agent_loop._estimated_tokens` already uses. Kept identical on purpose, so
#: the loop's generated-output ceiling and this module's input ceilings are
#: expressed in the same unit and can be reasoned about together.
CHARS_PER_TOKEN = 4

#: What a compression pass aims to leave behind. Halving rather than trimming to
#: exactly the budget: a retry that comes back one sentence under the limit will
#: overflow again on the next token the model adds, and the whole point of the
#: retry is that it should not need a third.
COMPRESSION_KEEP_RATIO = 0.5

#: The sentence spliced in where units were removed. It is IN the prompt, and
#: that is the point: a model told that material was omitted behaves differently
#: from a model handed a gap it cannot see. No em dash, because this string can
#: reach a generated artifact.
#:
#: The full stop is OUTSIDE the bracket deliberately. `sentences` splits on a
#: terminator followed by whitespace, so a marker ending in `.]` would fuse with
#: whatever followed it and stop being a whole unit of its own, which would make
#: the marker the one thing in a compressed prompt that is not a whole sentence.
OMISSION_MARKER = (
    "[Some earlier context was omitted here to fit the model context window]."
)


class ContextOverBudget(ValueError):
    """A slot could not be brought inside its budget by removing whole units.

    Raised rather than resolved, because the alternatives are all worse: cutting
    mid sentence hands a model half a sentence, and dropping the last surviving
    unit of evidence removes the thing the answer was supposed to be grounded
    in. Carries the slot, the budget and the estimate so the caller's message
    can say which purpose overran rather than "the prompt was too long".
    """

    def __init__(
        self, slot: str, *, budget_tokens: int, tokens: int, detail: str
    ) -> None:
        self.slot = slot
        self.budget_tokens = budget_tokens
        self.tokens = tokens
        self.detail = detail
        super().__init__(
            f"the {slot} slot needs {tokens} tokens against a budget of "
            f"{budget_tokens}: {detail}"
        )


@dataclass(frozen=True)
class Fitted:
    """One slot, after fitting. `dropped` is the record that it happened."""

    slot: str
    text: str
    tokens: int
    kept_units: int
    dropped_units: int
    #: One sentence per drop, phrased for a log line rather than for a user.
    dropped: tuple[str, ...] = ()

    @property
    def was_reduced(self) -> bool:
        return self.dropped_units > 0


@dataclass(frozen=True)
class Assembled:
    """Every slot, fitted, plus the joined text and what it cost."""

    text: str
    slots: tuple[Fitted, ...]
    tokens: int

    @property
    def dropped(self) -> tuple[str, ...]:
        return tuple(reason for slot in self.slots for reason in slot.dropped)


@dataclass(frozen=True)
class Compression:
    """The result of compressing a message list for a retry.

    `compressed` False means nothing could be removed without cutting inside a
    sentence, which is the case where the router must NOT retry: an identical
    request reproduces the same overflow, and a shortened one would be a
    fabrication.
    """

    messages: list[dict[str, Any]]
    compressed: bool
    note: str


def estimate_tokens(text: str) -> int:
    """A conservative, vendor-independent token estimate.

    Deliberately not a tokenizer. A tokenizer would be exact for one vendor's
    encoding and would add a dependency to the one module that has to keep
    working when the vendor is unreachable; the ceilings here are budgets rather
    than hard API limits, so an estimate that never UNDER-counts by much is what
    they need.
    """
    if not text:
        return 0
    return max(1, -(-len(text) // CHARS_PER_TOKEN))


def budget_for(slot: str) -> int:
    """The token budget for one slot. Raises for a slot nobody declared."""
    try:
        return SLOT_TOKEN_BUDGETS[slot]
    except KeyError as exc:
        raise ValueError(
            f"Unknown context slot {slot!r}; expected one of "
            f"{sorted(SLOT_TOKEN_BUDGETS)}"
        ) from exc


# ── Whole units ──────────────────────────────────────────────────────────────

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    """Split into whole sentences, the smallest unit anything here may drop.

    A newline is a boundary too: several callers build a block as one bullet per
    line with no terminal punctuation, and treating that as a single unbreakable
    sentence would make every such block either fit whole or be refused whole.
    """
    parts: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts.extend(
            piece.strip()
            for piece in _SENTENCE_BOUNDARY.split(stripped)
            if piece.strip()
        )
    return parts


def fit_evidence(
    units: Sequence[str],
    *,
    slot: str = SLOT_RETRIEVED_EVIDENCE,
    max_units: int = EVIDENCE_PREFERRED_UNITS,
) -> Fitted:
    """Fit retrieved evidence by dropping WHOLE units, never by shortening one.

    Two ceilings, and both are W4.5: the token budget, and `max_units`. Units
    are assumed to arrive in relevance order, which is what every retriever in
    this codebase returns, so the drops come off the tail.

    A single unit that does not fit on its own RAISES. Shortening it would be
    compressing evidence, and dropping it would leave a prompt whose evidence
    slot is empty while the caller believes it supplied some.
    """
    budget = budget_for(slot)
    kept: list[str] = []
    dropped: list[str] = []
    used = 0
    for index, raw in enumerate(units):
        unit = str(raw or "").strip()
        if not unit:
            continue
        cost = estimate_tokens(unit)
        if not kept and cost > budget:
            raise ContextOverBudget(
                slot,
                budget_tokens=budget,
                tokens=cost,
                detail=(
                    f"unit {index} needs {cost} tokens on its own; evidence is "
                    f"never shortened, so the retriever must return smaller "
                    f"chunks rather than this one being cut"
                ),
            )
        if len(kept) >= max_units:
            dropped.append(
                f"{slot}: dropped unit {index}, the slot already holds "
                f"{max_units} units and four relevant chunks beat twenty "
                f"adequate ones"
            )
            continue
        if used + cost > budget:
            dropped.append(
                f"{slot}: dropped unit {index}, {cost} tokens would exceed the "
                f"{budget}-token budget with {used} already used"
            )
            continue
        kept.append(unit)
        used += cost
    text = "\n\n".join(kept)
    return Fitted(
        slot=slot,
        text=text,
        tokens=estimate_tokens(text),
        kept_units=len(kept),
        dropped_units=len(dropped),
        dropped=tuple(dropped),
    )


def fit_summary(text: str, *, slot: str) -> Fitted:
    """Fit DERIVED text by dropping whole sentences from the oldest end.

    Only a slot in `COMPRESSIBLE_SLOTS` may be fitted this way. Asking for the
    evidence slot here raises rather than obliging, because the whole asymmetry
    this module exists to hold is that evidence is not compressible, and a
    parameter that let a caller opt out of it would be the way that rule gets
    broken by somebody in a hurry.
    """
    if slot not in COMPRESSIBLE_SLOTS:
        raise ValueError(
            f"the {slot!r} slot is not compressible; compress the state "
            f"summary, never the evidence and never the policy"
        )
    budget = budget_for(slot)
    units = sentences(text)
    if not units:
        return Fitted(slot=slot, text="", tokens=0, kept_units=0, dropped_units=0)

    def _rendered(surviving: list[str], dropped: int) -> str:
        # The marker is inside the measurement, not added afterwards. Fitting
        # the survivors and then prepending it is how a fitter comes back over
        # its own budget by exactly the length of the thing it added.
        return " ".join(([OMISSION_MARKER] if dropped else []) + surviving)

    kept = list(units)
    dropped_count = 0
    # Oldest first: a state summary's most recent sentences are the ones that
    # describe where the workflow actually is.
    while kept and estimate_tokens(_rendered(kept, dropped_count)) > budget:
        kept.pop(0)
        dropped_count += 1
    if not kept:
        raise ContextOverBudget(
            slot,
            budget_tokens=budget,
            tokens=estimate_tokens(units[-1]),
            detail=(
                "not even the most recent sentence fits, and a sentence is the "
                "smallest whole unit this module will drop"
            ),
        )
    rendered = _rendered(kept, dropped_count)
    return Fitted(
        slot=slot,
        text=rendered,
        tokens=estimate_tokens(rendered),
        kept_units=len(kept),
        dropped_units=dropped_count,
        dropped=(
            (
                f"{slot}: compressed the state summary, dropping "
                f"{dropped_count} of {len(units)} sentences from the oldest end",
            )
            if dropped_count
            else ()
        ),
    )


# ── Authoritative state ──────────────────────────────────────────────────────

#: The keys the application is authoritative for, in the order they are
#: rendered. A CLOSED tuple: a caller that could pass any key could pass
#: "grade", and an authoritative-looking block asserting a grade is exactly the
#: thing this product must never put in front of a model that is about to write
#: one.
AUTHORITATIVE_KEYS: tuple[str, ...] = (
    "tenant_id",
    "job_id",
    "candidate_id",
    "role",
    "scorecard_version",
    "permissions",
    "workflow_stage",
)


def authoritative_state(**facts: Any) -> str:
    """Render the state block a prompt is given rather than asked to infer.

    Every key must be in `AUTHORITATIVE_KEYS`; an unknown one raises. A value of
    None is rendered as the word `unknown` rather than omitted, because an
    absent line reads as a fact nobody had, and a model filling in an absent
    line from a transcript is the failure this block exists to prevent.
    """
    unknown_keys = sorted(set(facts) - set(AUTHORITATIVE_KEYS))
    if unknown_keys:
        raise ValueError(
            f"{unknown_keys} are not facts the application is authoritative "
            f"for; expected a subset of {list(AUTHORITATIVE_KEYS)}"
        )
    lines = [
        "Authoritative application state. Treat these as given, never infer them."
    ]
    for name in AUTHORITATIVE_KEYS:
        if name not in facts:
            continue
        value = facts[name]
        if isinstance(value, (list, tuple, set, frozenset)):
            rendered = ", ".join(sorted(str(item) for item in value)) or "none"
        else:
            rendered = "unknown" if value is None else str(value)
        lines.append(f"{name}: {rendered}")
    return "\n".join(lines)


# ── Assembly ─────────────────────────────────────────────────────────────────


def assemble(sections: Mapping[str, Sequence[str]]) -> Assembled:
    """Fit every supplied slot and join what survives.

    `sections` maps a slot name to its ordered units. Each slot is fitted by the
    rule its own purpose earns: evidence loses whole units, a derived summary is
    compressed at sentence boundaries, and the system policy is fitted by
    neither and simply has to fit.
    """
    fitted: list[Fitted] = []
    for slot, units in sections.items():
        budget = budget_for(slot)
        if slot == SLOT_RETRIEVED_EVIDENCE:
            fitted.append(fit_evidence(units, slot=slot))
            continue
        present = [str(unit or "").strip() for unit in units if str(unit or "").strip()]
        joined = "\n".join(present)
        if slot in COMPRESSIBLE_SLOTS:
            fitted.append(fit_summary(joined, slot=slot))
            continue
        tokens = estimate_tokens(joined)
        if tokens > budget:
            raise ContextOverBudget(
                slot,
                budget_tokens=budget,
                tokens=tokens,
                detail=(
                    "the system policy is neither compressed nor dropped, "
                    "because a silently shortened policy is a rule nobody can "
                    "see was removed; shorten the instruction at its source"
                ),
            )
        fitted.append(
            Fitted(
                slot=slot,
                text=joined,
                tokens=tokens,
                kept_units=len(present),
                dropped_units=0,
            )
        )
    text = "\n\n".join(slot.text for slot in fitted if slot.text)
    return Assembled(text=text, slots=tuple(fitted), tokens=estimate_tokens(text))


# ── Compression for a context-overflow retry ─────────────────────────────────


def compress_messages(messages: Sequence[Mapping[str, Any]]) -> Compression:
    """Halve a message list by removing WHOLE units, for one overflow retry.

    Called by `llm_router` when the vendor answered `context_length_exceeded`,
    which is the one failure class whose recovery is to send LESS. It is not a
    general-purpose prompt builder: `assemble` is that, and it runs before the
    call rather than after a 400.

    Two granularities, one rule. Every system message survives verbatim, because
    the system prompt is the policy slot and dropping part of it would remove a
    rule rather than a fact. Then:

      * more than one conversation turn: drop WHOLE turns from the oldest end,
        which is where a transcript's least decision-relevant material sits;
      * exactly one turn: drop WHOLE SENTENCES from its MIDDLE, keeping the head
        and the tail. The instruction is at the head and the question is at the
        tail of every single-turn prompt this codebase builds, so trimming from
        either end would remove the part that says what to do.

    `compressed` is False when nothing whole can be removed. The router must
    then stop rather than retry: an identical request reproduces the same 400,
    and a request shortened past a sentence boundary is a fabrication.
    """
    system = [dict(m) for m in messages if m.get("role") == "system"]
    turns = [dict(m) for m in messages if m.get("role") != "system"]

    if len(turns) > 1:
        target = max(1, int(len(turns) * COMPRESSION_KEEP_RATIO))
        dropped = len(turns) - target
        if dropped <= 0:
            return Compression(
                messages=[dict(m) for m in messages],
                compressed=False,
                note="nothing could be dropped without cutting inside a turn",
            )
        kept = turns[-target:]
        marker = {"role": "user", "content": OMISSION_MARKER}
        return Compression(
            messages=system + [marker] + kept,
            compressed=True,
            note=(
                f"dropped the {dropped} oldest of {len(turns)} conversation "
                f"turns whole"
            ),
        )

    if len(turns) == 1:
        units = sentences(str(turns[0].get("content") or ""))
        if len(units) < 3:
            return Compression(
                messages=[dict(m) for m in messages],
                compressed=False,
                note=(
                    f"the only turn holds {len(units)} whole sentences, and "
                    f"removing one would leave nothing between the instruction "
                    f"and the question"
                ),
            )
        target = max(2, int(len(units) * COMPRESSION_KEEP_RATIO))
        head = max(1, target // 2)
        tail = target - head
        kept = units[:head] + [OMISSION_MARKER] + units[len(units) - tail :]
        return Compression(
            messages=system + [{"role": "user", "content": " ".join(kept)}],
            compressed=True,
            note=(
                f"kept the first {head} and last {tail} of {len(units)} whole "
                f"sentences and marked the omission"
            ),
        )

    return Compression(
        messages=[dict(m) for m in messages],
        compressed=False,
        note="there is no conversation turn to compress",
    )
