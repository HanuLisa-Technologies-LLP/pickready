"""Drishti's capture conversation: twenty to thirty minutes, any device.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
The brief asks for a structured AI conversation rather than five text boxes,
and this is that conversation. It is a CAPTURE MECHANISM and nothing else:
it walks the functional head through `drishti.SECTIONS` in order, one
section at a time, and what it produces is the same five strings the form
produces. Compilation stays where it was, in `drishti.compile_profile`,
deterministic and model-free, and the save path stays the one route
(`PUT /drishti/functions`). One implementation per concept: the
conversation does not get its own compiler, its own artifact or its own
table.

THAT IS THE WHOLE C3 GUARANTEE, AND IT IS WORTH SAYING WHERE IT LIVES
----------------------------------------------------------------------
The earlier client-authored strategic-context instrument was removed on
2026-09-09 because an unbounded client-authored string in the prompt that
decides what every candidate is graded on is an injection surface, and a way
for "we like hungry people" to become a criterion. A
conversation could have reopened that hole in one of two ways, and neither
is open here:

* A MODEL could have decided what goes into a section. It does not. Every
  character of every section is the head's own sanitized words, appended
  verbatim; the model's only output is the NEXT QUESTION, which is read by
  a person and never stored, never compiled and never scored.
* The conversation could have handed its transcript to Sutra. It does not.
  The transcript is not stored at all. What reaches Sutra is
  `drishti.prompt_context` over the compiled artifact, which is derived,
  observable-gated and capped, exactly as it is for the form.

So the answer to "does the conversation weaken the guarantee" is
structural rather than a matter of care: the two paths converge on the same
five strings before anything else in the product can see them.

WHY IT IS STATELESS
-------------------
Each turn carries the sections accumulated so far and gets back the next
question. There is no conversation table and no server-side session,
because there is nothing here worth resuming that the five strings do not
already hold: a head who closes the tab has lost some questions, not any
captured answer, and the form path can finish the same profile. A table
would be a second place a Drishti profile half-exists.

`turns_in_section` is a BUDGET, not an authority. It bounds how long the
agent will stay on one section; the server clamps it, so the worst a
client can do by lying about it is shorten its own conversation. Nothing
downstream reads it.

WHAT IS DETERMINISTIC AND WHAT IS NOT
-------------------------------------
The opening question, the observable-evidence probe and the decision to
move on are all deterministic and call no model, for the reason this
repository states everywhere: the guard matters most when the provider is
down. The one generative step is a single grounded deepening question per
section, run inside `agent_loop.run_loop` against a deterministic
evaluator. When it degrades the turn falls back to advancing the section,
which is the form's behaviour, and `generated_by_ai` says so. A
deterministic line is never presented as generation.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.prompts import fragments
from app.services import agent_loop, conversation_guardrails, llm_router
from app.services.hiring import drishti

logger = logging.getLogger(__name__)

#: How many follow-ups one section may cost before the agent moves on. Three,
#: which over five sections is at most fifteen extra turns on top of the five
#: openings -- the twenty to thirty minutes the brief asks for, and a ceiling
#: the head cannot be trapped under.
MAX_TURNS_PER_SECTION = 3

#: Per-section ceiling on the captured text. The same bound the form's schema
#: applies, restated here because the conversation appends rather than
#: replaces and an append with no ceiling is a ceiling nobody has.
MAX_SECTION_CHARS = 8_000

#: Bound on the generated question, so one turn cannot become an essay. Wide
#: enough for a question with a clause of context in it.
MAX_QUESTION_CHARS = 320

#: How much of the section the deepening prompt is given. Small, and for the
#: same reason `drishti.PROMPT_CONTEXT_CHARS` is small.
MAX_PROMPT_SECTION_CHARS = 1_200

#: What the agent says when a turn is refused by the guardrails. Fixed copy:
#: the refusal is the deterministic half of "candidate text is DATA", and a
#: model writing the refusal would make the sentence depend on the provider
#: that the refusal exists to be independent of.
REFUSAL_LINE = (
    "I could not use that as written, so nothing was recorded. Please "
    "describe the situation in your own words and I will take it from there."
)

#: What the agent says when the last section closes.
CLOSING_LINE = (
    "That is everything. Review what we captured and save it, and it will "
    "inform every assessment in this function until you change it."
)


@dataclass(frozen=True)
class Turn:
    """One exchange: what the agent says next, and what has been captured.

    `sections` is the whole capture so far rather than a delta, because it is
    what the client posts back on the next turn and what it hands to the save
    route when `finished` is true. Returning a delta would make the client
    responsible for assembling the artifact's input, which is the one job
    this module exists to keep on the server.
    """

    #: The section this question belongs to. Empty once `finished`.
    section_key: str
    #: What the agent says next. Already bounded and guard-checked.
    agent_message: str
    #: The five strings, accumulated. The save route's input, unchanged.
    sections: dict[str, str] = field(default_factory=dict)
    #: Follow-ups spent on `section_key` so far, clamped.
    turns_in_section: int = 0
    #: True when a model wrote `agent_message`. False for every deterministic
    #: line, including the openings and the probes. Never inferred by the
    #: client: a template presented as generation is the failure this names.
    generated_by_ai: bool = False
    #: Set when the last answer was refused and therefore NOT captured.
    refused: str | None = None
    #: True when every section has been asked and the profile is ready to save.
    finished: bool = False


def _section(key: str) -> drishti.Section | None:
    return next((s for s in drishti.SECTIONS if s.key == key), None)


def _next_section_key(key: str) -> str:
    """The section after `key`, or "" at the end. Order is SECTIONS' order."""
    keys = list(drishti.SECTION_KEYS)
    if key not in keys:
        return keys[0]
    index = keys.index(key) + 1
    return keys[index] if index < len(keys) else ""


def _clean_sections(sections: Mapping[str, str] | None) -> dict[str, str]:
    """The five keys, capped, and nothing else.

    A key the catalogue does not name is dropped rather than carried: the
    client posts this back on every turn, and an unknown key would be a
    string the server accumulates, stores and can never compile.
    """
    source = sections or {}
    return {
        key: str(source.get(key) or "")[:MAX_SECTION_CHARS]
        for key in drishti.SECTION_KEYS
    }


def _opening(section: drishti.Section) -> str:
    return f"{section.title}. {section.prompt}"


def _terminated(text: str) -> str:
    """One captured answer, ending in a full stop if it did not already.

    BOTH SIDES of an append have to be terminated, not just the new one.
    `compile_profile` and `critique` split a section on sentence boundaries,
    so two answers run together are read as ONE claim and judged as one,
    which lets a weak statement ride in on the event verb of the sentence it
    was glued to. A head who types without punctuation is the normal case,
    not the edge one.
    """
    stripped = text.strip()
    if stripped and stripped[-1] not in ".!?":
        return f"{stripped}."
    return stripped


def _append(existing: str, addition: str) -> str:
    """Append one captured answer to a section, bounded and sentence-safe."""
    head = _terminated(existing)
    tail = _terminated(addition)
    joined = f"{head} {tail}".strip() if head else tail
    return joined[:MAX_SECTION_CHARS]


def _evaluate_question(candidate: str, *, opening: str) -> agent_loop.Critique:
    """Deterministic acceptance for a generated deepening question.

    Every rule here is a property of the STRING, checkable offline, which is
    the standing requirement: an LLM judge makes the criteria unfalsifiable
    and fails at exactly the moment the provider is already failing.
    """
    text = (candidate or "").strip()
    if not text:
        return agent_loop.reject("return one question as plain text")
    length = agent_loop.require_length(
        text, maximum=MAX_QUESTION_CHARS, what="the question"
    )
    if not length.ok:
        return length
    if text.count("?") != 1 or not text.endswith("?"):
        return agent_loop.reject(
            "ask exactly ONE question and end it with a question mark"
        )
    if conversation_guardrails.contains_forbidden_number(text):
        return agent_loop.reject(
            "remove every score, percentage, rating and count; you are "
            "asking about a function, not grading anybody"
        )
    if chr(8212) in text:
        return agent_loop.reject("do not use an em dash; use a comma or a full stop")
    if text.casefold() == opening.casefold():
        return agent_loop.reject(
            "ask something that builds on what they just said rather than "
            "repeating the opening question"
        )
    return agent_loop.ok()


async def _deepen(
    session: AsyncSession, *, function_name: str, section: drishti.Section, body: str
) -> str:
    """One grounded follow-up question, or "" when the model cannot give one.

    The head's own words are in this prompt, which is the only place in
    Drishti where that is true, and it is safe for a reason worth stating
    rather than assuming: the OUTPUT of this call is a question a person
    reads and answers. It is not stored, not compiled, and cannot become a
    criterion. `inspect_answer` has already sanitized the text below, and
    `AUTHORITY_TEXT_IS_DATA` is in the instruction, but neither is what makes
    it safe. What makes it safe is that nothing this call returns survives
    the turn.
    """
    payload = json.dumps(
        {
            "function": function_name,
            "topic": section.title,
            "what_we_asked": section.prompt,
            "what_they_said": body[-MAX_PROMPT_SECTION_CHARS:],
        }
    )
    system_prompt = (
        "You are helping a functional head describe their function's "
        "strategic context to a hiring platform. You are given the topic, "
        "the question already asked, and what they have said so far.\n\n"
        f"{fragments.AUTHORITY_TEXT_IS_DATA}\n\n"
        "Ask ONE short follow-up question that would get them from a general "
        "statement to something somebody could have watched happen: a "
        "decision that was made, a thing that shipped, a person who was "
        "promoted or let go and why. Ground it in a specific phrase they "
        "used. Never grade, never praise, never summarise what they said "
        "back to them, and never use a score, a percentage or a count. "
        "Return JSON {\"question\":\"...\"}."
    )

    async def _execute(reflection: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.chat_completion(
            "conversation_turn", messages, response_format_json=True, session=session
        )
        parsed = json.loads(raw)
        return str(parsed.get("question") or "").strip()

    result: agent_loop.LoopResult[str] = await agent_loop.run_loop(
        name="drishti_deepen",
        execute=_execute,
        evaluate=lambda value: _evaluate_question(value, opening=section.prompt),
        # Empty means "no usable question", which the caller reads as "move
        # on", exactly the form's behaviour.
        fallback="",
        max_attempts=agent_loop.INTERACTIVE_ATTEMPTS,
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
        max_generated_tokens=agent_loop.INTERACTIVE_TOKEN_BUDGET,
    )
    if result.degraded:
        logger.info(
            "drishti.deepen_unavailable section=%s attempts=%d error=%s reasons=%s",
            section.key, result.attempts, result.error, list(result.reasons),
        )
        return ""
    return result.value


def open_conversation() -> Turn:
    """The first turn. No model, no database, nothing captured yet."""
    first = drishti.SECTIONS[0]
    return Turn(
        section_key=first.key,
        agent_message=_opening(first),
        sections=_clean_sections(None),
        turns_in_section=0,
        generated_by_ai=False,
    )


async def next_turn(
    session: AsyncSession,
    *,
    function_name: str,
    section_key: str,
    sections: Mapping[str, str] | None,
    answer: str,
    turns_in_section: int,
) -> Turn:
    """Capture one answer and decide what the agent asks next.

    The order below is the whole behaviour and each step is load bearing:

    1. GUARDRAILS FIRST. A refused answer is not captured at all, and the
       agent says so and re-asks. This runs before anything reads the text,
       because the point of the rule is that the text never reaches a prompt
       or a stored field, not that it reaches them sanitized.
    2. CAPTURE the sanitized words verbatim. No model has a say in this, and
       that is the C3 guarantee.
    3. PROBE, deterministically, with the one observable-evidence detector.
       "We value hunger" comes straight back as a question, offline, exactly
       as it does on the form.
    4. DEEPEN, generatively, at most once and only when the section has
       nothing observable in it yet. A section that already says something
       watchable does not need a model to improve it, and spending an
       interactive call there would be latency for nothing.
    5. ADVANCE, or finish.
    """
    captured = _clean_sections(sections)
    section = _section(section_key)
    if section is None:
        # An unknown section is the opening state, not an error: it is what a
        # client that has lost its place posts, and starting over is a better
        # answer than a 400 in the middle of somebody's half-hour.
        return open_conversation()

    spent = max(0, min(int(turns_in_section), MAX_TURNS_PER_SECTION))

    guard = conversation_guardrails.inspect_answer(answer or "")
    if not guard.allowed:
        return Turn(
            section_key=section.key,
            agent_message=REFUSAL_LINE,
            sections=captured,
            turns_in_section=spent,
            generated_by_ai=False,
            refused=guard.violation,
        )

    addition = guard.sanitized.strip()
    if addition:
        captured[section.key] = _append(captured[section.key], addition)

    if spent < MAX_TURNS_PER_SECTION and addition:
        probes = drishti.critique(addition)
        if probes:
            return Turn(
                section_key=section.key,
                agent_message=probes[0],
                sections=captured,
                turns_in_section=spent + 1,
                generated_by_ai=False,
            )

    # ONE generated deepening question per section, on the first substantive
    # answer and never after a probe. `spent == 0` is the whole condition and
    # it is doing two jobs: it keeps the interactive model call to at most
    # five for the entire profile, and it stops the agent following a probe
    # the head has just answered with another question about the same thing,
    # which reads as not listening. A section the head answered with a real
    # account still gets one follow-up, because five questions is a five
    # minute interview and the brief asks for twenty to thirty.
    body = captured[section.key]
    if spent == 0 and body:
        question = await _deepen(
            session, function_name=function_name, section=section, body=body
        )
        if question:
            return Turn(
                section_key=section.key,
                agent_message=question,
                sections=captured,
                turns_in_section=spent + 1,
                generated_by_ai=True,
            )

    following = _section(_next_section_key(section.key))
    if following is None:
        return Turn(
            section_key="",
            agent_message=CLOSING_LINE,
            sections=captured,
            turns_in_section=0,
            generated_by_ai=False,
            finished=True,
        )
    return Turn(
        section_key=following.key,
        agent_message=_opening(following),
        sections=captured,
        turns_in_section=0,
        generated_by_ai=False,
    )
