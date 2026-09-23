"""The coding languages a question may be written in.

One spec per language the product knows how to offer. A deployment chooses
which of them it offers through `CODE_EXECUTION_LANGUAGES`, and the settings
validator refuses a key this registry does not describe, so the two cannot
disagree about what "java" means.

What is NOT here: the sandbox's own language identifiers. Those are wire
details of one adapter and live beside it (`judge0_language_ids`), so the
domain never learns which sandbox runs the code.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import CODE_EXECUTION_LANGUAGE_KEYS, get_settings

__all__ = [
    "LanguageSpec",
    "LANGUAGE_SPECS",
    "configured_languages",
    "language_spec",
]


@dataclass(frozen=True)
class LanguageSpec:
    """What the product needs to know about one language.

    `key` is also the editor's language id. `source_hint` is the sentence a
    candidate reads about how their program is run, because stdin/stdout
    problems depend on it and a Java class name is a convention the sandbox
    enforces rather than a style choice.
    """

    key: str
    label: str
    source_hint: str


LANGUAGE_SPECS: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        key="python",
        label="Python 3",
        source_hint="Read input from standard input and print the answer to standard output.",
    ),
    "java": LanguageSpec(
        key="java",
        label="Java",
        source_hint=(
            "Declare a public class named Main with a main method. Read input "
            "from standard input and print the answer to standard output."
        ),
    ),
    "cpp": LanguageSpec(
        key="cpp",
        label="C++",
        source_hint="Write a main function. Read input from standard input and print the answer to standard output.",
    ),
    "javascript": LanguageSpec(
        key="javascript",
        label="JavaScript (Node.js)",
        source_hint="Read input from standard input and print the answer to standard output.",
    ),
}

if tuple(LANGUAGE_SPECS) != CODE_EXECUTION_LANGUAGE_KEYS:
    raise RuntimeError(
        "code_execution.languages.LANGUAGE_SPECS and "
        "config.CODE_EXECUTION_LANGUAGE_KEYS describe different languages"
    )


def configured_languages() -> list[LanguageSpec]:
    """The languages this deployment offers, in configured order."""
    return [LANGUAGE_SPECS[key] for key in get_settings().code_execution_language_list]


def language_spec(key: str) -> LanguageSpec:
    """The spec for one CONFIGURED language.

    Raises `KeyError` naming the key when it is unknown or not offered here:
    a question in a language this deployment cannot run is a bug upstream,
    never something to quietly run as another language.
    """
    if key not in get_settings().code_execution_language_list:
        raise KeyError(f"coding language {key!r} is not configured for this deployment")
    return LANGUAGE_SPECS[key]
