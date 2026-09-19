"""The BGV Agent: it writes ONE email, and it decides nothing.

WHERE THE LINE IS
------------------
This module composes the verification request a recruiter reviews and sends.
It does not decide whether an employer confirmed anything, it does not set a
verification status, and it cannot reach the offer gate: `bgv_workflow` counts
rows and imports no router, and `test_bgv_workflow` asserts that by AST. An
LLM that could answer "is this candidate verified" would be an LLM that can
authorise an offer.

GROUNDING IS A DETERMINISTIC GATE, NOT AN INSTRUCTION
-------------------------------------------------------
The prompt says "use only the fact block", and a prompt instruction is a
request. `verify_grounding` is the guarantee: every employer name, job title,
HR name and date in the draft has to appear in the fact block, and a draft
carrying a YEAR the block does not contain is rejected outright. A verification
email that invents a date asks a stranger to confirm something nobody claimed,
and the stranger's answer would then be filed as evidence about a candidate.

The rejection is fed back verbatim as an instruction, which is what
`agent_loop` is for: "you wrote 2018, the stated period is 2019 to 2021" is a
defect a model fixes when told, and a one-shot call would have shipped it.

DEGRADATION IS RECORDED, NEVER SILENT
---------------------------------------
When the model is unavailable or every attempt fails grounding, the
deterministic template is used and the draft is returned with
`generated_by_ai = False`. The recruiter sees which one they are editing.
Presenting template output as generation is the failure this codebase names
explicitly.

THREE KINDS OF FACT NEVER BLUR
--------------------------------
Candidate-provided (the employment claim), recruiter-entered (nothing, at this
stage) and HR-confirmed (nothing yet). The email says the candidate STATED the
details and asks whether they are accurate; it never asserts them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from app.prompts import registry
from app.services import agent_loop, llm_router

#: Reused, not invented. Composing an email is what `email_composition` is, and
#: its routing policy (Terra, 15s per attempt, 30s total) already exists. A new
#: task type for the same act would be a second answer to one question.
TASK_TYPE = "email_composition"

PROMPT_NAME = "bgv_verification_request"

#: Any four-digit year. The grounding check rejects a draft containing one that
#: is not in the fact block, which is the single most damaging hallucination
#: this email can carry: a plausible wrong date produces a confident "no, that
#: is not correct" from an employer who is right.
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

_EM_DASH = chr(8212)


@dataclass(frozen=True)
class FactBlock:
    """Everything the agent is allowed to know. Frozen, and built from rows.

    A free-form context dict is deliberately absent, the same reason Miti's
    `EvaluatorInput` has no `notes` field: a future caller would put something
    in it, and the grounding check cannot verify what it cannot enumerate.
    """

    candidate_name: str
    employer_name: str
    designation: str
    started_on: date
    ended_on: date
    hr_name: str
    recruiter_team: str

    def period(self) -> tuple[str, str]:
        """The dates as the email will print them, so the grounding check and
        the prompt agree on one spelling."""
        return (
            self.started_on.strftime("%d %B %Y"),
            self.ended_on.strftime("%d %B %Y"),
        )

    def as_prompt_block(self) -> str:
        start, end = self.period()
        return (
            "FACTS (candidate-provided, unconfirmed):\n"
            f"  candidate_name: {self.candidate_name}\n"
            f"  employer_name: {self.employer_name}\n"
            f"  designation: {self.designation}\n"
            f"  employment_start: {start}\n"
            f"  employment_end: {end}\n"
            f"  hr_contact_name: {self.hr_name}\n"
            f"  requesting_team: {self.recruiter_team}\n"
        )

    def allowed_years(self) -> set[str]:
        return {str(self.started_on.year), str(self.ended_on.year)}


def subject_for(facts: FactBlock) -> str:
    """Composed, never model-written.

    A subject line is what a busy HR contact reads in a list, and it is also
    the one part of the email a model has the most incentive to make
    interesting. It names the candidate and the company and stops.
    """
    return (
        f"Employment verification request: {facts.candidate_name}, "
        f"{facts.employer_name}"
    )


def deterministic_body(facts: FactBlock) -> str:
    """The template. Correct, plain, and used whenever generation cannot be
    trusted, which is what makes the honest `generated_by_ai = False` possible."""
    start, end = facts.period()
    return (
        f"Hello {facts.hr_name},\n\n"
        f"{facts.candidate_name} has stated that they were employed at "
        f"{facts.employer_name} as a {facts.designation} from {start} to {end}. "
        "We are carrying out a background verification and would be grateful if "
        "you could confirm whether those details are accurate.\n\n"
        "We would also appreciate confirmation that they completed their "
        "employment with the organisation appropriately, including the "
        "applicable notice period and formal relieving where that applies.\n\n"
        "If any of the details above do not match your records, please tell us "
        "what differs. Simply replying to this email is enough; your response "
        "reaches our recruitment team directly.\n\n"
        f"Thank you for your time.\n\n{facts.recruiter_team}"
    )


def verify_grounding(body: str, facts: FactBlock) -> agent_loop.Critique:
    """Deterministic. Every fact present, no invented year, no em dash.

    Runs as `agent_loop`'s verify stage, so a failure is fed back verbatim and
    the model gets a bounded chance to fix exactly what was wrong.
    """
    defects: list[str] = []
    text = body or ""

    start, end = facts.period()
    for label, value in (
        ("the HR contact's name", facts.hr_name),
        ("the candidate's name", facts.candidate_name),
        ("the employer name", facts.employer_name),
        ("the job title", facts.designation),
        ("the employment start date", start),
        ("the employment end date", end),
    ):
        if value and value not in text:
            defects.append(
                f"{label} is missing from the email. It must appear exactly as "
                f"{value!r}."
            )

    invented = {
        match.group(0) for match in _YEAR_RE.finditer(text)
    } - facts.allowed_years()
    if invented:
        defects.append(
            "the email contains "
            + ", ".join(sorted(invented))
            + ", which is not in the fact block. The only years you may write "
            f"are {', '.join(sorted(facts.allowed_years()))}."
        )

    if _EM_DASH in text:
        defects.append("the email contains an em dash, which this product never uses.")

    # A verification request that ASSERTS rather than ASKS turns an unconfirmed
    # claim into a statement the employer is invited to agree with.
    if "?" not in text and "confirm" not in text.lower():
        defects.append("the email never actually asks the employer to confirm anything.")

    if len(text.strip()) < 200:
        defects.append("the email is too short to contain the request.")

    if defects:
        # `reasons` is what the next attempt is TOLD, verbatim, so each entry is
        # phrased as a correction rather than a complaint. `defects` is the
        # typed copy that reaches telemetry, which is how a gate that rejects
        # everything becomes visible instead of looking like a provider outage.
        return agent_loop.Critique(
            ok=False,
            reasons=tuple(defects),
            defects=tuple(
                agent_loop.Defect("grounding", "body", detail) for detail in defects
            ),
        )
    return agent_loop.Critique(ok=True)


async def _generate(instruction: str, facts: FactBlock) -> str:
    system = registry.render(PROMPT_NAME)
    user = facts.as_prompt_block()
    if instruction:
        user = f"{user}\nCORRECT THESE DEFECTS:\n{instruction}\n"
    raw = await llm_router.invoke_llm(
        task_type=TASK_TYPE,
        system=system,
        user=user,
        json_mode=True,
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("bgv email generation returned a non-object")
    return str(payload.get("body") or "")


async def draft_verification_email(facts: FactBlock) -> tuple[str, str, bool]:
    """Return (subject, body, generated_by_ai).

    Never raises. A provider outage, a malformed response or a draft that
    cannot be grounded all end at the deterministic template, and the caller is
    told which one it got so the recruiter is never shown template output
    labelled as generation.
    """
    result = await agent_loop.run_loop(
        name="bgv_verification_email",
        execute=lambda instruction: _generate(instruction, facts),
        evaluate=lambda body: verify_grounding(body, facts),
        verify=lambda body: verify_grounding(body, facts),
        fallback=deterministic_body(facts),
    )
    return subject_for(facts), result.value, not result.degraded
