"""The one place a prompt is loaded from, and the one place it is versioned.

WHY
---
Section 1's engineering constraint says every LLM prompt lives in `/prompts`,
not inlined in application code. Before this, fourteen email templates and the
JD document did; every system prompt for every generative agent did not. They
were Python string literals in `services/`, which has three costs:

  1. **A prompt edit is invisible in a diff full of code.** The thing most
     likely to change output quality was the thing least likely to be reviewed
     as a change to output quality.
  2. **There was no version.** When a rate in `eval_interview` or `eval_report`
     moves, the first question is "which prompt changed?", and nothing could
     answer it.
  3. **Two prompt directories had already appeared** -- `backend/prompts/` with
     one file and `backend/app/prompts/` with fourteen -- because there was no
     loader to be consistent with. Both happened to reach the image via
     `COPY . .`; the next one would not have.

WHAT A VERSION IS
-----------------
Two parts, and both are load-bearing:

  * a declared `# version: N` header, which the author bumps when they mean to
    change behaviour; and
  * a short digest of the body, which changes whether or not anyone remembered
    to bump the header.

So `ppi_framework_system@3+7f2a1c9d` names the prompt, the author's intent, and
the exact bytes. That string is what the router logs, which is what makes a
quality regression traceable to a specific prompt change rather than to "the
model was different that afternoon".

SUBSTITUTION USES `$name`, NOT `{name}`
---------------------------------------
Every one of these prompts ends by specifying a JSON return shape, so the text
is full of literal `{` and `}`. `str.format` would raise on the first one, and
escaping them all would make the files unreadable in exactly the place they
most need to be read. `string.Template` leaves braces alone and only touches
`$name`, so the JSON examples pass through as written.

`safe_substitute` is deliberately NOT used: an unresolved `$placeholder` would
be sent to the model verbatim and produce plausible-looking nonsense. A missing
value is a programming error and should fail at the call, loudly.
"""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from string import Template
from typing import Any

__all__ = ["PROMPT_DIR", "load", "render", "version", "names", "PromptError"]

#: The ONE directory. `backend/prompts/` was folded into this on 2026-08-11.
PROMPT_DIR = Path(__file__).resolve().parent

_VERSION_HEADER = re.compile(r"^#\s*version:\s*(\d+)\s*$", re.IGNORECASE)

#: Loaded prompts, keyed by name. Prompt files do not change at runtime, and a
#: file read on every model call would be a syscall in the latency path of an
#: interactive request for no benefit.
_CACHE: dict[str, "Prompt"] = {}
_LOCK = threading.Lock()


class PromptError(RuntimeError):
    """A prompt is missing, malformed, or was rendered without a value.

    Its own type because the three callers that can hit it (a typo in a name, a
    file that did not reach the image, a renamed placeholder) all want to fail
    at startup or in a test rather than degrade -- unlike a provider outage,
    which every agent is built to survive.
    """


class Prompt:
    """One prompt file: its text, its declared version, its digest."""

    __slots__ = ("name", "text", "declared_version", "digest")

    def __init__(self, name: str, text: str, declared_version: int, digest: str) -> None:
        self.name = name
        self.text = text
        self.declared_version = declared_version
        self.digest = digest

    @property
    def version(self) -> str:
        """`<declared>+<digest>`, e.g. `3+7f2a1c9d`.

        Both halves, because either alone lies in a way that matters: the
        declared number misses an edit nobody stamped, and the digest alone
        cannot express "this change was deliberate".
        """
        return f"{self.declared_version}+{self.digest}"

    @property
    def label(self) -> str:
        """`<name>@<version>`. What the router logs."""
        return f"{self.name}@{self.version}"

    def render(self, **values: Any) -> str:
        try:
            return Template(self.text).substitute(**values)
        except KeyError as exc:
            raise PromptError(
                f"prompt {self.name!r} needs a value for ${exc.args[0]}; "
                f"got {sorted(values)}"
            ) from exc
        except ValueError as exc:
            # A bare `$` in the body, usually a currency symbol someone typed.
            raise PromptError(
                f"prompt {self.name!r} has an invalid $ placeholder: {exc}. "
                "Write a literal dollar sign as $$."
            ) from exc


def _read(name: str) -> Prompt:
    path = PROMPT_DIR / f"{name}.txt"
    if not path.is_file():
        available = ", ".join(names()) or "none"
        raise PromptError(
            f"no prompt file {path.name!r} in {PROMPT_DIR}; available: {available}"
        )
    raw = path.read_text(encoding="utf-8")

    declared = 1
    body_lines: list[str] = []
    for line in raw.splitlines():
        header = _VERSION_HEADER.match(line)
        if header is not None:
            declared = int(header.group(1))
            continue
        body_lines.append(line)
    # Leading comment lines (`#` at column 0) are documentation for the reader
    # and are NOT sent to the model. Anything indented, or containing a `#`
    # mid-line, is prompt text: a rule about markdown headings would otherwise
    # be silently deleted from the prompt that states it.
    body = "\n".join(
        line for line in body_lines if not line.startswith("#")
    ).strip()
    if not body:
        raise PromptError(f"prompt {name!r} is empty once its header is removed")

    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
    return Prompt(name, body, declared, digest)


def load(name: str) -> Prompt:
    """The prompt, cached. Raises `PromptError` if it is missing or empty."""
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get(name)
        if cached is None:
            cached = _read(name)
            _CACHE[name] = cached
    return cached


def render(name: str, **values: Any) -> str:
    """The prompt with its `$placeholders` filled in.

    Call with no values for a prompt that has none; a prompt with no
    placeholders renders to itself.
    """
    return load(name).render(**values)


def version(name: str) -> str:
    """`<declared>+<digest>`, for logging and for traceability of a rate move."""
    return load(name).version


def names() -> list[str]:
    """Every prompt file in the directory, sorted. Used by the audit tests."""
    return sorted(path.stem for path in PROMPT_DIR.glob("*.txt"))


