"""The transcript line for a structured answer, and time spent as a phrase.

THE TRANSCRIPT STAYS A CONVERSATION. `assessment_messages` is what every
scorer, the gap analysis and the recruiter's Q&A view read, and a structured
answer has to appear in it as something a person can read: the option the
candidate chose, the sentence with the blanks filled in, the code they wrote.
The SERVER renders that line from the validated answer, never the client, so
a client can never disagree with its own structured submission.

TIME IS A PHRASE. `assessment_answers.time_spent_seconds` is measured by the
server and stays on the row; what a recruiter reads is "about two minutes".
The standing no-numbers rule covers the recruiter's view like every other
boundary, and a figure of seconds beside an answer is a number a reader
starts comparing between candidates.
"""
from __future__ import annotations

from typing import Any

from app.services.assessment_formats import types

__all__ = ["time_spent_phrase", "transcript_line"]

#: The one unit conversion this module makes.
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60

_ONES: tuple[str, ...] = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS: tuple[str, ...] = ("", "", "twenty", "thirty", "forty", "fifty")


def _minutes_in_words(minutes: int) -> str:
    if minutes < len(_ONES):
        return _ONES[minutes]
    tens, ones = divmod(minutes, len(_ONES) // 2)
    word = _TENS[tens]
    return word if ones == 0 else f"{word}-{_ONES[ones]}"


def time_spent_phrase(seconds: int | None) -> str | None:
    """"under a minute", "about a minute", "about seven minutes", "about an
    hour", "over an hour". No digit ever."""
    if seconds is None:
        return None
    minutes = round(max(0, int(seconds)) / SECONDS_PER_MINUTE)
    if minutes == 0:
        return "under a minute"
    if minutes == 1:
        return "about a minute"
    if minutes < MINUTES_PER_HOUR:
        return f"about {_minutes_in_words(minutes)} minutes"
    if minutes < MINUTES_PER_HOUR + MINUTES_PER_HOUR // 2:
        return "about an hour"
    return "over an hour"


def _mcq_lines(payload: dict[str, Any], selected: list[str]) -> str:
    by_id = {option["id"]: option["text"] for option in payload.get("options", [])}
    if not selected:
        return "Selected nothing."
    return "Selected: " + "; ".join(by_id.get(option_id, option_id) for option_id in selected)


def transcript_line(question_type: str, payload: dict[str, Any] | None, answer: dict[str, Any]) -> str:
    """The candidate's structured answer as one readable transcript line."""
    payload = payload or {}
    if question_type == types.MCQ_SINGLE:
        return _mcq_lines(payload, [str(answer.get("selected_option_id") or "")])
    if question_type == types.MCQ_MULTI:
        return _mcq_lines(payload, [str(item) for item in answer.get("selected_option_ids") or []])
    if question_type == types.FILL_BLANK:
        template = str(payload.get("template") or "")
        values = [str(value) for value in answer.get("values") or []]
        parts = template.split(types.BLANK_MARKER)
        filled = parts[0]
        for index, part in enumerate(parts[1:]):
            value = values[index].strip() if index < len(values) else ""
            filled += f"[{value or 'blank'}]" + part
        return filled
    if question_type == types.CODING:
        return f"Language: {answer.get('language', '')}\n{answer.get('code', '')}"
    return str(answer.get("text") or "")
