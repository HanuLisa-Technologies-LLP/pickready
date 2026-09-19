"""Prompt template registry.

Every prompt the platform sends to an LLM lives here as a `.txt` file — one
file per task — so prompt wording can be reviewed, diffed, and tuned without
touching Python. Nothing in this package performs I/O at import time; templates
are read on first use and cached.

Placeholders use Python `str.format` syntax (`{candidate_name}`). Because JSON
examples inside a prompt contain literal braces, those must be doubled (`{{`
and `}}`) exactly as they are in `email_generation.txt`.

THE LEADING COMMENT BLOCK IS DOCUMENTATION AND IS NOT SENT (2026-09-09)
----------------------------------------------------------------------
`registry.py` has always dropped a `# version: N` header and the file's leading
comment lines; this loader did not, so `email_databank_invitation.txt` was
shipping its own version header to the model as the first line of a system
prompt. Two loaders disagreeing about what a prompt file IS meant a version
header could not be added to the twelve files this loader owns without changing
what the model reads, which is exactly the change a version header exists to
make visible.

Only the CONTIGUOUS LEADING block is stripped, not every `#` line anywhere.
`registry.py` strips them wherever they appear and documents the trade; here a
mid-file `#` is prompt text, and one of these prompts could legitimately need to
show a model a Markdown heading.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


def _body(raw: str) -> str:
    """The prompt text, without its leading documentation comment block."""
    lines = raw.splitlines()
    start = 0
    while start < len(lines) and lines[start].startswith("#"):
        start += 1
    return "\n".join(lines[start:]).lstrip("\n")


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
    return _body(path.read_text(encoding="utf-8"))


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
