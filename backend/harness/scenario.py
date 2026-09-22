"""A scenario: declarative, versioned, and refused outright when it cannot be judged.

WHY THE LOADER IS STRICT RATHER THAN FORGIVING
------------------------------------------------
HARNESS.md section 2 states the rule this module exists to enforce: "`expect`
is ground truth and is mandatory. A scenario without it merely executes." A
loader that accepted a scenario with no `expect` would produce a green run for
a file that asserted nothing, which is the most expensive shape of false
confidence this repository knows. `test_seed.py` and the 2026-08-06 timestamp
finding are both instances of the same class: a stamp, a status, or a passing
dot standing in for work that never happened.

So every refusal below is loud, names the FILE and the FIELD, and has a reason
behind it rather than a preference:

  * no `expect`, or an `expect` with nothing in it: nothing to judge against.
  * an unknown tier: tier decides where a scenario runs (section 9), so a typo
    would silently move a safety scenario out of the set CI fails on.
  * no `id` or no `version`: replay resolves a scenario by both (section 6),
    and a run that cannot be resolved back to its scenario is an anecdote.
  * a duplicate id across the directory: two files answering to one name means
    `--scenario <id>` picks one of them and nobody knows which.
  * an unknown key anywhere: a misspelled `expects:` is a scenario that loads
    happily and asserts nothing, which is the first refusal in this list
    arriving through the back door.

WHAT IS PARSED HERE AND WHAT IS RESOLVED ELSEWHERE
----------------------------------------------------
This module turns YAML into a frozen dataclass and nothing more. A `world` name
is not resolved to a builder, a fault name is not resolved to a context
manager, and an expectation string is not evaluated. Those belong to
`harness/world.py`, `harness/faults.py` and the evaluators, and keeping the
parse separate is what lets the whole scenario directory be validated by CI
without a database, a stack or a model credential.

Provenance: docs/spec/HARNESS.md sections 2, 3, 4 and 9.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

import yaml


class ScenarioError(ValueError):
    """A scenario file cannot be loaded, with the file and field named.

    A named class rather than a bare `ValueError` because the CLI distinguishes
    "your scenario is malformed", which is the author's problem and is fixed by
    editing a file, from "the run failed", which is the product's problem. The
    two deserve different exit paths and a caller cannot tell them apart from a
    message alone.
    """


class Tier(StrEnum):
    """Where a scenario runs and what budget it is held to (HARNESS.md section 9).

    A `StrEnum` so the value serialises into a manifest as its own name with no
    conversion step. The conversion step is where a manifest and a scenario
    drift apart.
    """

    SMOKE = "smoke"
    REGRESSION = "regression"
    INTEGRATION = "integration"
    ADVERSARIAL = "adversarial"
    SAFETY = "safety"
    PERFORMANCE = "performance"


#: Every tier except `performance`, which section 9 runs manually and nightly
#: because its budget is unbounded. Enumerated rather than derived by
#: subtraction so that a seventh tier added later is not silently swept into
#: the default set: joining the every-commit run is a decision somebody makes.
DEFAULT_TIERS: tuple[Tier, ...] = (
    Tier.SMOKE,
    Tier.REGRESSION,
    Tier.INTEGRATION,
    Tier.ADVERSARIAL,
    Tier.SAFETY,
)

_TOP_LEVEL_KEYS = frozenset(
    {"id", "version", "tier", "description", "tags", "given", "workload", "faults", "expect"}
)
_GIVEN_KEYS = frozenset({"world", "overrides"})
_EXPECT_KEYS = frozenset(
    {"state", "dispatched", "output", "trajectory", "prohibited", "evaluators"}
)


@dataclass(frozen=True)
class FaultRequest:
    """What a scenario ASKED the fault layer for, before anything resolves it.

    Held as a name plus parameters rather than as a resolved context manager so
    that this module stays parse only, and so the request serialises verbatim
    into the run manifest. HARNESS.md section 4 requires every injected fault to
    be recorded so a replay reproduces it, and a record of "what was requested"
    survives a rename in the fault layer in a way that a record of "what object
    was constructed" does not.
    """

    name: str
    params: Mapping[str, Any]


@dataclass(frozen=True)
class Given:
    """The initial state, named rather than inlined.

    HARNESS.md section 2: a scenario names a world builder, it does not inline
    SQL, because a scenario that builds its own state is a scenario nobody can
    compose. `overrides` is the escape hatch that keeps that rule affordable: a
    scenario that needs one field different from a shared world adjusts it here
    instead of forking the builder.
    """

    world: str
    overrides: Mapping[str, Any]


@dataclass(frozen=True)
class Expectation:
    """Ground truth. At least one of these is populated or the scenario is refused.

    The five assertion kinds are HARNESS.md section 3, in its order. `prohibited`
    is as load bearing as the rest and is listed as its own kind for that
    reason: "no number reached a client" and "no tenant boundary was crossed"
    are outcomes a scenario asserts the ABSENCE of, and an absence expressed as
    an ordinary state assertion reads as an afterthought.

    `evaluators` names which of section 7's layered evaluators judge this run.
    It is separate from the assertions because an evaluator answers a dimension
    ("did it degrade honestly") while an assertion answers a fact.
    """

    state: tuple[str, ...] = ()
    dispatched: Mapping[str, int] | None = None
    output: tuple[str, ...] = ()
    trajectory: tuple[str, ...] = ()
    prohibited: tuple[str, ...] = ()
    evaluators: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when nothing here can be judged.

        Note that `evaluators` alone does NOT make an expectation non-empty in
        the eyes of the loader's refusal, but it is counted here: an evaluator
        is a judgement, so a scenario naming one has ground truth even with no
        literal assertion. What is refused is a scenario with neither.
        """
        return not (
            self.state
            or self.dispatched
            or self.output
            or self.trajectory
            or self.prohibited
            or self.evaluators
        )


@dataclass(frozen=True)
class Scenario:
    """One scenario, parsed. Frozen because a run manifest quotes it.

    `source_path` travels with it so every refusal downstream, and every report,
    can name the file a reader has to open. A scenario id is not a path and a
    reader given only an id goes hunting.
    """

    id: str
    version: int
    tier: Tier
    description: str
    tags: tuple[str, ...]
    given: Given
    workload: tuple[str, ...]
    faults: tuple[FaultRequest, ...]
    expect: Expectation
    source_path: pathlib.Path

    @property
    def qualified_id(self) -> str:
        """`<id>@v<version>`, which is what replay actually resolves.

        A run pinned to an id alone would replay against whatever the scenario
        says today, and a scenario that changed between the failure and the
        replay is the one case where a replay is most misleading.
        """
        return f"{self.id}@v{self.version}"


def _require_mapping(value: Any, *, path: pathlib.Path, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScenarioError(
            f"{path}: field {field!r} must be a mapping, got {type(value).__name__}"
        )
    return value


def _reject_unknown_keys(
    body: Mapping[str, Any], allowed: frozenset[str], *, path: pathlib.Path, field: str
) -> None:
    """An unknown key is a refusal, never an ignored key.

    A misspelled `expects:` parses as a perfectly valid mapping with one
    unrecognised key, and a forgiving loader would then refuse the file for
    having no `expect` while the author stares at the `expect` block they can
    see. Naming the key is what turns a puzzling refusal into a one second fix.
    """
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ScenarioError(
            f"{path}: unknown key(s) in {field}: {', '.join(unknown)}. "
            f"Permitted: {', '.join(sorted(allowed))}"
        )


def _string_tuple(value: Any, *, path: pathlib.Path, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ScenarioError(
            f"{path}: field {field!r} must be a list of strings, got "
            f"{type(value).__name__}"
        )
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ScenarioError(
                f"{path}: {field}[{index}] must be a non-empty string, got {item!r}"
            )
        items.append(item.strip())
    return tuple(items)


def _parse_tier(value: Any, *, path: pathlib.Path) -> Tier:
    if not isinstance(value, str):
        raise ScenarioError(
            f"{path}: field 'tier' must be a string, got {type(value).__name__}"
        )
    try:
        return Tier(value.strip())
    except ValueError as exc:
        permitted = ", ".join(tier.value for tier in Tier)
        raise ScenarioError(
            f"{path}: unknown tier {value!r}. Tier decides where a scenario "
            f"runs and what CI fails on, so an unrecognised one is refused "
            f"rather than defaulted. Permitted: {permitted}"
        ) from exc


def _parse_faults(value: Any, *, path: pathlib.Path) -> tuple[FaultRequest, ...]:
    """Two spellings, because both are legitimate and neither is ambiguous.

    A parameterless fault is a bare string (`- redis_down`); a parameterised one
    is a single-key mapping (`- model_error: {status: 429}`). Accepting both is
    not a dual code path: they produce the same `FaultRequest` and the branch
    ends here.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ScenarioError(
            f"{path}: field 'faults' must be a list, got {type(value).__name__}"
        )
    requests: list[FaultRequest] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            if not item.strip():
                raise ScenarioError(f"{path}: faults[{index}] is an empty fault name")
            requests.append(FaultRequest(item.strip(), {}))
            continue
        if isinstance(item, Mapping):
            if len(item) != 1:
                raise ScenarioError(
                    f"{path}: faults[{index}] must name exactly one fault, got "
                    f"{len(item)} keys. Two faults in one entry cannot be "
                    "ordered, and the fault layer applies them in order."
                )
            (name, params), = item.items()
            if not isinstance(name, str) or not name.strip():
                raise ScenarioError(f"{path}: faults[{index}] has a non-string name")
            if params is None:
                params = {}
            requests.append(
                FaultRequest(
                    name.strip(),
                    dict(_require_mapping(params, path=path, field=f"faults[{index}]")),
                )
            )
            continue
        raise ScenarioError(
            f"{path}: faults[{index}] must be a fault name or a single-key "
            f"mapping of name to parameters, got {type(item).__name__}"
        )
    return tuple(requests)


def _parse_dispatched(value: Any, *, path: pathlib.Path) -> Mapping[str, int] | None:
    """`{task name: exact count}`, and the count is exact on purpose.

    "At least once" is the property every dispatcher already gives for free, so
    asserting it proves nothing. The counts that matter here are the ones a
    retry would change, which is exactly the idempotency question the worked
    example in HARNESS.md section 2 is asking.
    """
    if value is None:
        return None
    expectations: dict[str, int] = {}
    if isinstance(value, Mapping):
        entries: list[tuple[Any, Any]] = list(value.items())
    elif isinstance(value, (list, tuple)):
        entries = []
        for index, item in enumerate(value):
            mapping = _require_mapping(item, path=path, field=f"expect.dispatched[{index}]")
            entries.extend(mapping.items())
    else:
        raise ScenarioError(
            f"{path}: 'expect.dispatched' must be a mapping or a list of "
            f"mappings, got {type(value).__name__}"
        )
    for name, count in entries:
        if not isinstance(name, str) or not name.strip():
            raise ScenarioError(f"{path}: 'expect.dispatched' has a non-string task name")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ScenarioError(
                f"{path}: 'expect.dispatched[{name}]' must be a count of zero or "
                f"more, got {count!r}"
            )
        expectations[name.strip()] = count
    return expectations


def _parse_expect(value: Any, *, path: pathlib.Path) -> Expectation:
    if value is None:
        raise ScenarioError(
            f"{path}: 'expect' is missing. A scenario without ground truth "
            "merely executes, and an execution that asserts nothing reports a "
            "pass it never earned (HARNESS.md section 2)."
        )
    body = _require_mapping(value, path=path, field="expect")
    _reject_unknown_keys(body, _EXPECT_KEYS, path=path, field="expect")
    expectation = Expectation(
        state=_string_tuple(body.get("state"), path=path, field="expect.state"),
        dispatched=_parse_dispatched(body.get("dispatched"), path=path),
        output=_string_tuple(body.get("output"), path=path, field="expect.output"),
        trajectory=_string_tuple(body.get("trajectory"), path=path, field="expect.trajectory"),
        prohibited=_string_tuple(body.get("prohibited"), path=path, field="expect.prohibited"),
        evaluators=_string_tuple(body.get("evaluators"), path=path, field="expect.evaluators"),
    )
    if expectation.is_empty:
        raise ScenarioError(
            f"{path}: 'expect' is present but empty. It must carry at least one "
            "of state, dispatched, output, trajectory, prohibited or evaluators "
            "(HARNESS.md section 3)."
        )
    return expectation


def load_scenario(path: str | pathlib.Path) -> Scenario:
    """Parse one scenario file, refusing anything that cannot be judged."""
    path = pathlib.Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError(f"{path}: cannot be read: {exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path}: is not valid YAML: {exc}") from exc

    if document is None:
        raise ScenarioError(f"{path}: is empty")
    body = _require_mapping(document, path=path, field="<document>")
    _reject_unknown_keys(body, _TOP_LEVEL_KEYS, path=path, field="<document>")

    scenario_id = body.get("id")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ScenarioError(
            f"{path}: field 'id' is missing or empty. Replay and "
            "`--scenario` both resolve by id, so an unnamed scenario is one "
            "nobody can re-run."
        )

    version = body.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ScenarioError(
            f"{path}: field 'version' must be an integer of one or more, got "
            f"{version!r}. A replay pins the version so that a scenario edited "
            "between the failure and the replay is detected rather than "
            "silently re-run."
        )

    given_body = _require_mapping(body.get("given") or {}, path=path, field="given")
    _reject_unknown_keys(given_body, _GIVEN_KEYS, path=path, field="given")
    world = given_body.get("world")
    if not isinstance(world, str) or not world.strip():
        raise ScenarioError(
            f"{path}: 'given.world' must name a world builder. A scenario that "
            "builds its own state is a scenario nobody can compose "
            "(HARNESS.md section 2)."
        )
    overrides = dict(
        _require_mapping(given_body.get("overrides") or {}, path=path, field="given.overrides")
    )

    description = body.get("description") or ""
    if not isinstance(description, str):
        raise ScenarioError(
            f"{path}: field 'description' must be a string, got "
            f"{type(description).__name__}"
        )

    return Scenario(
        id=scenario_id.strip(),
        version=version,
        tier=_parse_tier(body.get("tier"), path=path),
        description=" ".join(description.split()),
        tags=_string_tuple(body.get("tags"), path=path, field="tags"),
        given=Given(world=world.strip(), overrides=overrides),
        workload=_string_tuple(body.get("workload"), path=path, field="workload"),
        faults=_parse_faults(body.get("faults"), path=path),
        expect=_parse_expect(body.get("expect"), path=path),
        source_path=path,
    )


def load_all(directory: str | pathlib.Path) -> tuple[Scenario, ...]:
    """Every scenario under `directory`, sorted by id, with duplicates refused.

    The whole directory is parsed even when the caller wants one scenario,
    because the duplicate-id check is a property of the SET and a loader that
    only looked at the requested file could never see it. Parsing is pure text
    work with no stack behind it, so the cost is a few milliseconds.
    """
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        raise ScenarioError(
            f"{directory}: is not a directory. Scenarios live under "
            "backend/harness/scenarios/ (HARNESS.md section 2)."
        )
    seen: dict[str, pathlib.Path] = {}
    scenarios: list[Scenario] = []
    paths = sorted(
        path
        for pattern in ("*.yaml", "*.yml")
        for path in directory.rglob(pattern)
    )
    for path in paths:
        scenario = load_scenario(path)
        first = seen.get(scenario.id)
        if first is not None:
            raise ScenarioError(
                f"{path}: duplicate scenario id {scenario.id!r}, already "
                f"declared by {first}. Two files answering to one name means "
                "`--scenario` picks one of them and the report names the other."
            )
        seen[scenario.id] = path
        scenarios.append(scenario)
    return tuple(sorted(scenarios, key=lambda item: item.id))


def select(
    scenarios: tuple[Scenario, ...],
    *,
    tier: Tier | None = None,
    scenario_id: str | None = None,
) -> tuple[Scenario, ...]:
    """The selection `harness run` makes, in one place so the CLI cannot drift.

    An explicit `--scenario` that matches nothing RAISES rather than returning
    an empty set, because a run over zero scenarios exits zero and reads as a
    pass. That is the same failure `scripts/test.sh` guards against when its
    integration selector matches no files.
    """
    chosen = scenarios
    if scenario_id is not None:
        chosen = tuple(item for item in chosen if item.id == scenario_id)
        if not chosen:
            known = ", ".join(item.id for item in scenarios) or "none loaded"
            raise ScenarioError(
                f"no scenario with id {scenario_id!r}. Known ids: {known}"
            )
    if tier is not None:
        chosen = tuple(item for item in chosen if item.tier is tier)
        if not chosen:
            raise ScenarioError(
                f"no scenario in tier {tier.value!r}. A run over zero scenarios "
                "exits zero and reads as a pass, so it is refused."
            )
    elif scenario_id is None:
        chosen = tuple(item for item in chosen if item.tier in DEFAULT_TIERS)
    return chosen
