"""The assertion grammar: small, closed, and deliberately not Python.

WHY A GRAMMAR AND NOT `eval`
------------------------------
HARNESS.md section 2 asks for scenarios that are "declarative, versioned,
composable, machine evaluable". `eval` would deliver the last of those and
destroy the other three: a scenario whose expectation was arbitrary Python
could reach the session, the client and the product, so validating the
directory would require running it, and a reader could no longer tell what a
scenario asserts by reading it.

So the grammar is eleven operators over a closed registry of probes, and
anything it cannot express is a probe somebody adds to `probes.py` with a name
and one definition. That is the same trade `runbook_data/` makes: the values
are data, and adding one means naming it.

EVERY REFUSAL NAMES THE EXPRESSION AND WHAT WAS WRONG WITH IT
---------------------------------------------------------------
A malformed assertion is the author's problem and is fixed by editing a file,
which is exactly the distinction `scenario.ScenarioError` already draws. An
expression that could not be parsed is never treated as a failing assertion:
reporting it as a failure would send somebody looking for a product defect
behind a missing bracket.

WHY A COMPARISON IS TYPE TOLERANT IN EXACTLY ONE DIRECTION
------------------------------------------------------------
A probe that counts rows returns an `int`; one that reads a uuid column returns
a `UUID`; one that reads a status returns a `str`. A scenario writes literals.
So `==` compares a `UUID` against a string by rendering the probe's value, and
compares numbers numerically, and does NOT coerce a string into a number: an
expectation of `"5"` matching a count of `5` would make a typo in a probe name
indistinguishable from a passing assertion.

Provenance: docs/spec/HARNESS.md sections 2 and 3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness.context import ScenarioContext, StateReader
from harness.probes import ProbeError, read_output, read_prohibited, read_state
from harness.run import Check
from harness.world import World

__all__ = ["AssertionError_", "evaluate_dispatched", "evaluate_output",
           "evaluate_prohibited", "evaluate_state", "evaluate_trajectory", "parse"]


class AssertionError_(ValueError):
    """An expression cannot be parsed, with the expression and the reason.

    Named with a trailing underscore so it cannot shadow the builtin, which a
    module that also raises real assertion failures would otherwise do in a way
    nobody notices until a `try` block swallows the wrong thing.
    """


#: Longest first, so `>=` is never parsed as `>` followed by a stray `=`, and
#: `is not null` is never parsed as `is null` with a word in front.
_OPERATORS: tuple[str, ...] = (
    "is not null",
    "is null",
    "not contains",
    "contains",
    "not in",
    "in",
    "matches",
    "==",
    "!=",
    ">=",
    "<=",
    ">",
    "<",
)

_QUOTED = re.compile(r"^(['\"])(.*)\1$", re.DOTALL)


@dataclass(frozen=True)
class Expression:
    """One parsed assertion: what to ask, how to compare, and against what."""

    raw: str
    selector: str
    operator: str
    literal: Any


def _literal(text: str, *, raw: str) -> Any:
    text = text.strip()
    if not text:
        raise AssertionError_(f"{raw!r}: the right-hand side is empty")
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_literal(part, raw=raw) for part in _split_list(inner)]
    match = _QUOTED.match(text)
    if match:
        return match.group(2)
    lowered = text.lower()
    if lowered == "null":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    # A bare word. Permitted because `job_candidate_links.status == screening`
    # reads better than the quoted form, and a status is a closed vocabulary
    # rather than free text.
    return text


def _split_list(inner: str) -> list[str]:
    """Split on commas that are not inside quotes.

    Hand written rather than `csv` or a regex, because the only nesting this
    grammar has is quotes and the failure mode of a clever splitter here is a
    scenario that parses into the wrong list and passes.
    """
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in inner:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if quote is not None:
        raise AssertionError_(f"unterminated quote in list [{inner}]")
    parts.append("".join(current))
    return [part for part in (item.strip() for item in parts) if part]


def parse(raw: str) -> Expression:
    """Turn one assertion string into an `Expression`, or refuse it by name."""
    text = " ".join(raw.split())
    if not text:
        raise AssertionError_("an empty assertion cannot be judged")
    for operator in _OPERATORS:
        if operator in ("is null", "is not null"):
            if text.endswith(" " + operator):
                return Expression(
                    raw, text[: -(len(operator) + 1)].strip(), operator, None
                )
            continue
        # Spaced first, so a probe name containing `in` is never split on it.
        marker = f" {operator} "
        if marker in text:
            selector, _, literal = text.partition(marker)
            return Expression(
                raw, selector.strip(), operator, _literal(literal, raw=raw)
            )
        if operator in ("==", "!=", ">=", "<=", ">", "<") and operator in text:
            selector, _, literal = text.partition(operator)
            return Expression(
                raw, selector.strip(), operator, _literal(literal, raw=raw)
            )
    raise AssertionError_(
        f"{raw!r}: no operator found. Use one of {', '.join(_OPERATORS)}."
    )


def _comparable(value: Any) -> Any:
    """A probe value in a form the literals can be compared against.

    A `UUID`, a `Decimal` and a `datetime` all arrive from the database and
    none of them is writable in a scenario. Rendering them rather than trying
    to parse the literal into their type keeps the coercion in ONE direction,
    which is what stops `"5" == 5` becoming true.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_comparable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _comparable(item) for key, item in value.items()}
    return str(value)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _compare(expression: Expression, observed: Any) -> bool:
    actual = _comparable(observed)
    expected = expression.literal
    operator = expression.operator

    if operator == "is null":
        return actual is None
    if operator == "is not null":
        return actual is not None
    if operator == "==":
        return actual == expected
    if operator == "!=":
        return actual != expected
    if operator in (">", "<", ">=", "<="):
        left, right = _numeric(actual), _numeric(expected)
        if left is None or right is None:
            raise AssertionError_(
                f"{expression.raw!r}: {operator} needs two numbers, got "
                f"{actual!r} and {expected!r}"
            )
        if operator == ">":
            return left > right
        if operator == "<":
            return left < right
        if operator == ">=":
            return left >= right
        return left <= right
    if operator in ("in", "not in"):
        if not isinstance(expected, (list, str)):
            raise AssertionError_(
                f"{expression.raw!r}: `in` needs a list or a string on the right"
            )
        found = actual in expected
        return found if operator == "in" else not found
    if operator in ("contains", "not contains"):
        if actual is None:
            found = False
        elif isinstance(actual, (list, dict)):
            found = expected in actual
        else:
            found = str(expected) in str(actual)
        return found if operator == "contains" else not found
    if operator == "matches":
        return bool(re.search(str(expected), str(actual)))
    raise AssertionError_(f"{expression.raw!r}: unknown operator {operator!r}")


async def evaluate_state(
    expressions: Sequence[str], reader: StateReader, world: World
) -> list[Check]:
    """Judge the state assertions, from the second connection, after the fact."""
    checks: list[Check] = []
    for raw in expressions:
        expression = parse(raw)
        try:
            observed = await read_state(expression.selector, reader, world)
        except ProbeError as exc:
            raise AssertionError_(f"{raw!r}: {exc}") from exc
        checks.append(
            Check(
                kind="state",
                expression=raw,
                expected=f"{expression.operator} {expression.literal!r}".strip(),
                actual=_comparable(observed),
                passed=_compare(expression, observed),
            )
        )
    return checks


def evaluate_output(expressions: Sequence[str], ctx: ScenarioContext) -> list[Check]:
    """Judge the output assertions over what the client actually received."""
    checks: list[Check] = []
    for raw in expressions:
        expression = parse(raw)
        try:
            observed = read_output(expression.selector, ctx)
        except ProbeError as exc:
            raise AssertionError_(f"{raw!r}: {exc}") from exc
        checks.append(
            Check(
                kind="output",
                expression=raw,
                expected=f"{expression.operator} {expression.literal!r}".strip(),
                actual=_comparable(observed),
                passed=_compare(expression, observed),
            )
        )
    return checks


def evaluate_prohibited(names: Sequence[str], ctx: ScenarioContext) -> list[Check]:
    """Judge the prohibited outcomes, which are as load bearing as the rest.

    A prohibited outcome passes when it did NOT occur, so `passed` is the
    negation of the probe's answer. The probe's detail becomes the note either
    way: on a pass it says what was swept and found clean, which is what keeps
    a passing prohibition from reading as a probe that did nothing.
    """
    checks: list[Check] = []
    for name in names:
        occurred, detail = read_prohibited(name, ctx)
        checks.append(
            Check(
                kind="prohibited",
                expression=name,
                expected="never occurs",
                actual="occurred" if occurred else "did not occur",
                passed=not occurred,
                note=detail,
            )
        )
    return checks


def evaluate_trajectory(
    expected: Sequence[str], ctx: ScenarioContext
) -> list[Check]:
    """Judge that the named stages were reached, IN ORDER.

    A subsequence rather than an equality, deliberately. A scenario asserting
    "invited then scored" is asserting an ORDERING, and demanding the exact
    stage list would make every scenario break the day an unrelated stage is
    added to a shared workload step. A subsequence still catches the failure
    that matters, which is a stage reached out of order or not at all.
    """
    if not expected:
        return []
    remaining = list(expected)
    reached: list[str] = []
    for stage in ctx.trajectory:
        if remaining and stage == remaining[0]:
            reached.append(remaining.pop(0))
    return [
        Check(
            kind="trajectory",
            expression=" -> ".join(expected),
            expected=list(expected),
            actual=list(ctx.trajectory),
            passed=not remaining,
            note=(
                ""
                if not remaining
                else f"never reached, in order: {', '.join(remaining)}"
            ),
        )
    ]


def evaluate_dispatched(expected: Mapping[str, int] | None) -> list[Check]:
    """Judge what WOULD have been dispatched, over the `record` backend.

    EXACT COUNTS, because "at least once" is the property every dispatcher
    gives for free and asserting it proves nothing. The counts that matter are
    the ones a retry would change, which is the whole idempotency question.

    Read from `dispatch.recorded()` rather than from a patched `send_task`: a
    monkeypatched transport proves the call site ran, while this proves it
    named a task that the real registry resolves.
    """
    if not expected:
        return []
    from app.workers import dispatch

    observed = dispatch.recorded_names()
    counts: dict[str, int] = {}
    for name in observed:
        counts[name] = counts.get(name, 0) + 1
    return [
        Check(
            kind="dispatched",
            expression=f"{task} dispatched exactly {count} time(s)",
            expected=count,
            actual=counts.get(task, 0),
            passed=counts.get(task, 0) == count,
            note=(
                "everything dispatched: "
                + (", ".join(sorted(counts)) or "nothing")
            ),
        )
        for task, count in sorted(expected.items())
    ]


#: Exported for the engine test, which asserts the grammar refuses what it
#: should rather than that a mock was called.
OPERATORS: tuple[str, ...] = _OPERATORS
