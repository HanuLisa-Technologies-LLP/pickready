"""Prompts moved out of the code, and NOTHING they say changed.

Externalising a prompt is only safe if the bytes the model receives are the
same afterwards. A single reflowed sentence changes what an agent is told, and
the only way anyone would find out is a rate moving in an eval weeks later, in
a commit that looks like a refactor.

So the move is verified against a SNAPSHOT of the exact strings the code sent
before it (`tests/fixtures/prompt_snapshots.json`, generated from the commit
that still had them inline). Character for character.

WHY A SNAPSHOT AND NOT `git show HEAD~1`
----------------------------------------
That was the first version, and it skipped. The backend container has neither
git nor a `.git` directory, and a CI checkout is shallow, so the one assertion
that mattered turned itself off in both places it runs and reported nine green
skips. A check that quietly becomes a no-op is the exact failure this
repository keeps having to repair, so the evidence is checked in instead.

Changing a prompt deliberately therefore means updating the snapshot in the
same commit. That is the point: it makes a wording change a visible, reviewable
diff of what the model is told, rather than a line inside a refactor.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.prompts import fragments, registry

#: `app/prompts` -> `app`. Resolved from the registry rather than from this
#: file's own path, so it is correct both in a git checkout and inside the
#: backend container, where the package lives at /app/app and the repo root
#: does not exist. The first version used `parents[2]` and silently globbed an
#: empty directory in the container -- the same vacuous-sweep failure this
#: repository has already had to repair once in `test_platform_audit`.
APP_ROOT = Path(registry.PROMPT_DIR).parent
SERVICES = APP_ROOT / "services"
SNAPSHOTS = json.loads(
    (Path(__file__).parent / "fixtures" / "prompt_snapshots.json").read_text(
        encoding="utf-8"
    )
)


def _values_for(name: str) -> dict[str, object]:
    """The same constants the module used to interpolate.

    Read from the live modules, so a change to `MINIMUM_PER_CATEGORY` moves
    both sides together and this stays a test of the WORDING rather than of the
    number.
    """
    from app.services import outreach_content, ppi
    from app.services.rating import GRADE_HIGHLY, GRADE_MATCHING, GRADE_MODERATELY

    if name in {"interview_write_question", "technical_write_question"}:
        return {
            "one_question": fragments.ONE_QUESTION,
            "no_evaluation": fragments.NO_EVALUATION,
            "candidate_text_is_data": fragments.CANDIDATE_TEXT_IS_DATA,
        }
    if name == "interview_deliver_question":
        return {"no_evaluation": fragments.NO_EVALUATION}
    if name == "interview_challenge":
        return {"situation": "$situation"}
    if name == "outreach_email_system":
        return {
            "word_min": outreach_content.WORD_MIN,
            "word_max": outreach_content.WORD_MAX,
        }
    if name == "ppi_framework_system":
        return {
            "minimum_per_category": ppi.MINIMUM_PER_CATEGORY,
            "maximum_per_category": ppi.MAXIMUM_PER_CATEGORY,
            "grade_highly": GRADE_HIGHLY,
            "grade_matching": GRADE_MATCHING,
            "grade_moderately": GRADE_MODERATELY,
        }
    return {}


@pytest.mark.parametrize("name", sorted(SNAPSHOTS))
def test_every_externalised_prompt_is_unchanged(name: str) -> None:
    """The reason this module exists.

    Not "roughly the same", not "the same rules": a model reads the bytes.
    """
    rendered = registry.render(name, **_values_for(name))
    assert rendered == SNAPSHOTS[name], "\n".join(
        [
            f"{name} changed when it moved into a file.",
            "--- snapshot ---",
            repr(SNAPSHOTS[name]),
            "--- rendered ---",
            repr(rendered),
        ]
    )


def test_the_snapshot_covers_every_agent_prompt() -> None:
    """A snapshot file that lost an entry would pass forever on the rest."""
    assert len(SNAPSHOTS) == 10, f"the snapshot holds {len(SNAPSHOTS)} prompts"
    for name in SNAPSHOTS:
        assert name in registry.names(), f"{name} has no prompt file"


# ── The loader itself ────────────────────────────────────────────────────────

def test_a_missing_prompt_fails_loudly_and_says_what_exists() -> None:
    """Not a degradation path. A typo'd prompt name is a programming error and
    must not reach a model as an empty system message."""
    with pytest.raises(registry.PromptError) as caught:
        registry.load("no_such_prompt_at_all")
    assert "no_such_prompt_at_all" in str(caught.value)
    # The message has to be actionable, so it lists what IS there.
    assert "ppi_framework_system" in str(caught.value)


def test_a_missing_placeholder_value_raises_rather_than_being_sent() -> None:
    """`safe_substitute` would send `$situation` to the model verbatim and get
    back plausible-looking nonsense. Failing at the call is the whole point."""
    with pytest.raises(registry.PromptError) as caught:
        registry.render("interview_challenge")
    assert "situation" in str(caught.value)


def test_json_braces_survive_substitution() -> None:
    """The reason the registry uses `string.Template` and not `str.format`.

    `.format()` was used on the challenge prompt once and raised KeyError on
    the literal `{"challenge": ...}` at the end of it. A broad except turned
    that into the deterministic fallback, so every challenge a candidate saw
    was canned and none referred to anything they had said. Nothing failed;
    it was found by reading a live transcript.
    """
    rendered = registry.render("interview_challenge", situation="You were told X.")
    assert '{"challenge": <string>}' in rendered
    assert "You were told X." in rendered
    assert "$situation" not in rendered


def test_a_version_is_intent_plus_bytes() -> None:
    """Either half alone lies: the declared number misses an unstamped edit,
    and the digest alone cannot say a change was deliberate."""
    for name in registry.names():
        declared, _, digest = registry.version(name).partition("+")
        assert declared.isdigit(), f"{name} has no declared version"
        assert len(digest) == 8, f"{name} has no content digest"


def test_comment_lines_are_documentation_and_never_reach_the_model() -> None:
    for name in registry.names():
        text = registry.load(name).text
        assert not text.startswith("#"), f"{name} still carries its header"
        assert "version:" not in text.splitlines()[0], f"{name} leaks its header"


def test_there_is_exactly_one_prompt_directory() -> None:
    """`backend/prompts/` held one file while `app/prompts/` held fourteen.
    Both reached the image only because the Dockerfile does `COPY . .`; the
    next one added would not have."""
    stray = APP_ROOT.parent / "prompts"
    assert not stray.exists(), (
        f"a second prompt directory reappeared at {stray}; there is one loader "
        "and it reads app/prompts"
    )


def test_the_inline_sweep_has_something_to_sweep() -> None:
    """The guard on the guard below.

    A sweep over an empty directory passes forever. `test_platform_audit` spent
    its entire life doing exactly that in this container, so the assertion is
    made rather than assumed.
    """
    modules = list(SERVICES.glob("*.py"))
    assert len(modules) > 30, f"the service sweep found {len(modules)} files in {SERVICES}"


def test_no_prompt_is_left_inline_in_a_service() -> None:
    """Section 1's constraint, as a check rather than a habit.

    Matches the SHAPE these are written in: a module-level assignment to a name
    containing SYSTEM or PROMPT whose value is a string. The first version
    matched any literal starting "You are ", which flagged two things that are
    not prompts at all -- a default EMAIL TEMPLATE body ("You are invited to an
    interview...") and a fallback sentence the interviewer SAYS to a candidate
    ("You are on the right topic, but..."). A check that cries wolf twice out
    of four gets ignored, so it is anchored on the assignment instead.
    """
    import ast

    offenders: list[str] = []
    for path in sorted(SERVICES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not any("SYSTEM" in n or "PROMPT" in n for n in names):
                continue
            # `_SYSTEM_PROMPT_NAME` holds a prompt's NAME and `_PROMPT_PATH` a
            # filesystem path. Both are how a prompt is REFERRED to, which is
            # the thing being asked for, not a prompt inline.
            if any(n.endswith(("_NAME", "_PATH", "_KEY")) for n in names):
                continue
            # A literal string, or a concatenation of them. A call
            # (`registry.render(...)`) is the shape we want.
            if not isinstance(node.value, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                continue
            # Length is the last discriminator: a prompt is prose, and a short
            # constant with PROMPT in its name is a label or a key.
            try:
                literal = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                literal = None
            if isinstance(literal, str) and len(literal) < 120:
                continue
            offenders.append(f"{path.name}:{node.lineno} {names[0]}")
    assert not offenders, (
        "these system prompts are still inline; move them to app/prompts and "
        f"load them through the registry: {offenders}"
    )
