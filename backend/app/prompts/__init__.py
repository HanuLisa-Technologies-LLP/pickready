"""Prompt template registry.

Every prompt the platform sends to an LLM lives here as a `.txt` file — one
file per task — so prompt wording can be reviewed, diffed, and tuned without
touching Python. Nothing in this package performs I/O at import time; templates
are read on first use and cached.

Placeholders use Python `str.format` syntax (`{candidate_name}`). Because JSON
examples inside a prompt contain literal braces, those must be doubled (`{{`
and `}}`) exactly as they are in `email_generation.txt`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


class PromptNotFound(FileNotFoundError):
    """Raised when a prompt name has no corresponding .txt file."""


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Return the raw text of prompt `name` (without the .txt suffix).

    Cached: prompt files are static for the life of the process. The name is
    validated against path traversal — it must be a bare stem.
    """
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise PromptNotFound(f"invalid prompt name: {name!r}")
    path = _PROMPT_DIR / f"{name}.txt"
    if not path.is_file():
        raise PromptNotFound(
            f"No prompt template {name!r} in {_PROMPT_DIR} "
            f"(available: {sorted(p.stem for p in _PROMPT_DIR.glob('*.txt'))})"
        )
    return path.read_text(encoding="utf-8")


def render(name: str, /, **values: object) -> str:
    """Load prompt `name` and substitute `values` into its placeholders.

    A missing placeholder raises KeyError naming the field, so a prompt/caller
    mismatch fails loudly at the call site instead of shipping a literal
    "{candidate_name}" to the model.
    """
    template = load(name)
    try:
        return template.format(**values)
    except KeyError as exc:
        raise KeyError(
            f"prompt {name!r} needs a value for {exc.args[0]!r}; "
            f"got {sorted(values)}"
        ) from exc


def available() -> list[str]:
    """Names of every registered prompt template."""
    return sorted(p.stem for p in _PROMPT_DIR.glob("*.txt"))
