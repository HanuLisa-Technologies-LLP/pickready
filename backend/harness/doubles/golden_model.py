"""The model, scripted at the ROUTER, for the golden journey (CONTRACT v4 item 1).

WHY THE ROUTER AND NOT THE VENDOR TRANSPORT
---------------------------------------------
`harness.faults.model_answers` serves an answer at the httpx seam, below the
router, and it is the right double for a scenario about ONE call. The golden
journey reaches a dozen task types in one run, and the vendor request carries
no task type: a transport double would have to guess which call it was
answering from the prompt text, and a guess that answers the wrong call with a
plausible shape is the silent fallback rule 6 forbids, moved into the harness.
`llm_router.invoke_llm(task_type, messages, ...)` is the ONE chokepoint every
product call reaches (claude.md, 2026-08-05) and it names the task, so the
script is keyed by the name the product itself uses.

WHAT IS REAL AROUND IT
------------------------
Everything the router's caller does with the answer: the agent loop, the
deterministic evaluators (Sutra's validator, the question rubric check, Miti's
band parser, Siddhi's citation chokepoint), and every write. The double only
decides what the model SAID.

AN UNSCRIPTED CALL IS RECORDED AND REFUSED, NEVER ANSWERED
------------------------------------------------------------
A call for a task this script does not know raises `LLMUnavailableError`, the
product's own outage signal, so the caller degrades exactly as its contract
says, AND the call is kept in `unscripted`. The journey asserts that list is
empty: a caller that degraded silently would otherwise read as a pass.

Every answer is built FROM the request (the skills it names, the question it
asks, the evidence ids it offers), never from the world, so an answer names
what the product actually sent.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Sequence

__all__ = ["GoldenModel", "UnscriptedTask"]


class UnscriptedTask(KeyError):
    """A task type this script has no answer for."""


Answer = Callable[["GoldenModel", Sequence[Mapping[str, Any]]], Any]


def _user_payload(messages: Sequence[Mapping[str, Any]]) -> Any:
    """The last user message, parsed as JSON when it is JSON."""
    for message in reversed(list(messages)):
        if message.get("role") == "user":
            content = str(message.get("content") or "")
            try:
                return json.loads(content)
            except ValueError:
                return content
    return None


def _all_text(messages: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def _first_sentence(text: str) -> str:
    match = re.search(r"[^.!?]+[.!?]", text or "")
    return (match.group(0) if match else (text or "")).strip()


# ── Job setup ────────────────────────────────────────────────────────────────


def _skills_draft(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    request = _user_payload(messages)
    if not isinstance(request, Mapping):
        raise UnscriptedTask("skills_drafting was asked without a JSON payload")
    weakness = _first_sentence(str((request.get("swot") or {}).get("weaknesses") or ""))
    must: list[dict[str, Any]] = [{"name": "Python", "source": "jd"}]
    if weakness:
        must.append(
            {"name": "Payments reconciliation", "source": "swot", "swot_quote": weakness}
        )
    return {
        "must_have": must,
        "nice_to_have": [{"name": "PostgreSQL tuning", "source": "jd"}],
        "behavioural": [{"name": "Ownership under pressure", "source": "company"}],
    }


def _assessment_context(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    request = _user_payload(messages)
    skills = request.get("skills") if isinstance(request, Mapping) else None
    if not isinstance(skills, list) or not skills:
        raise UnscriptedTask("assessment_context was asked for no skills")
    priorities: dict[str, int] = {}
    written = []
    for entry in skills:
        bucket, name = str(entry["bucket"]), str(entry["name"])
        priorities[bucket] = priorities.get(bucket, 0) + 1
        written.append(
            {
                "bucket": bucket,
                "name": name,
                "evidence_line": (
                    f"Has shipped production work that depended on {name} and "
                    "explained the decisions made along the way."
                ),
                "priority": priorities[bucket],
            }
        )
    return {
        "role_summary": (
            "Owns the payments platform and its services end to end, from "
            "design review to the on-call rota."
        ),
        "skills": written,
        "refused": [],
    }


def _extraction(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    return {
        "skills": ["Python", "PostgreSQL", "Payments reconciliation"],
        "total_experience_years": 6,
        "education": [],
        "employment_history": [],
    }


# ── Yukti (AI Match) ─────────────────────────────────────────────────────────


#: The phrase that opens the rival's resume. The Yukti reading keys on it, so
#: the journey's two applicants are read differently from what they sent.
RIVAL_MARKER = "Payments operations analyst"


def _yukti(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    """One reading per candidate, every quote lifted verbatim from the resume
    the request carried, so grounding keeps it rather than refusing it.

    THE RESUMES ARE READ SO THAT THE ASSESSMENT MUST MOVE THE ORDER. The rival
    reads as a partial match on everything; the candidate who is later
    assessed reads as a partial match on the skills and nothing else. So the
    rival ranks first on the resume alone, and only the assessment's overall,
    blended in by `yukti.ranking`, can put the assessed candidate above them.
    """
    request = _user_payload(messages)
    if not isinstance(request, Mapping):
        raise UnscriptedTask("yukti_matching was asked without a JSON payload")
    results = []
    for candidate in request.get("candidates") or []:
        resume = str(candidate.get("resume") or "")
        quote = _first_sentence(resume)
        rival = RIVAL_MARKER.casefold() in resume.casefold()
        wider = "some" if rival else "none"

        def _judged(verdict: str, tag: str) -> dict[str, Any]:
            if verdict == "none":
                return {"verdict": "none"}
            return {"verdict": verdict, "quote": quote, "tag": tag}

        results.append(
            {
                "candidate": candidate["ref"],
                "skills": [
                    {"skill": skill["ref"], "verdict": "some", "quote": quote}
                    for skill in request.get("skills") or []
                ],
                "experience_level": _judged(wider, "Settlement operations"),
                "role_fit": _judged(wider, "Reconciliation reporting"),
                "company_needs": [
                    {"need": need["ref"], **_judged(wider, "Reconciliation ownership")}
                    for need in request.get("needs") or []
                ],
            }
        )
    return {"results": results}


# ── Question writing ─────────────────────────────────────────────────────────

#: The prose angles, cycled by slot index, so every slot's question is distinct
#: and names the skill the slot was placed on.
_PROSE_ANGLES: tuple[str, ...] = (
    "Walk me through a settlement problem where {skill} decided the outcome. What did you do yourself, and why?",
    "Tell me about a time your approach to {skill} failed in production. What did you change afterwards?",
    "Describe the hardest trade off you made involving {skill}, and how you knew it was the right call.",
    "How would you explain your way of working with {skill} to a new engineer on the settlement team?",
    "Give me a concrete example of {skill} under deadline pressure, including what you decided first.",
    "What would you do differently about {skill} if you rebuilt the reconciliation service today?",
    "Tell me about a disagreement over {skill} with a colleague and how it was settled.",
    "Describe how you checked that your work on {skill} was correct before it reached production.",
    "Which part of {skill} do you find hardest to teach, and how do you teach it anyway?",
    "Tell me about a decision involving {skill} that you would defend to a regulator.",
    "Describe a time {skill} saved an incident from getting worse. What did you notice first?",
    "How do you decide when your work on {skill} is good enough to ship?",
    "Walk me through the last design review where {skill} was the main topic.",
    "Tell me about feedback you received on {skill} and what you changed because of it.",
    "Describe a shortcut involving {skill} that you refused to take, and why.",
)

#: The sub types an anchored evidence question may carry.
_SUB_TYPES = (
    "project_deep_dive",
    "decision_justification",
    "claim_substantiation",
    "failure_trade_off",
    "scope_clarification",
)


def _questions(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    request = _user_payload(messages)
    slots = request.get("slots") if isinstance(request, Mapping) else None
    if not isinstance(slots, list) or not slots:
        raise UnscriptedTask("question_generation was asked for no slots")
    if len(slots) > len(_PROSE_ANGLES):
        raise UnscriptedTask(f"question_generation was asked for {len(slots)} slots")
    return {
        "questions": [
            {
                "index": slot["index"],
                "prompt": _PROSE_ANGLES[position].format(skill=slot["skill"]),
            }
            for position, slot in enumerate(slots)
        ]
    }


def _resume_sentences(text: str, *, minimum: int = 20) -> list[str]:
    """Whole sentences of the resume the request carried, verbatim, so an
    anchor passes the product's own word-for-word check."""
    found = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text or "")]
    return [part for part in found if len(part) >= minimum]


def _anchored(request: Mapping[str, Any]) -> Any:
    sentences = _resume_sentences(str(request.get("candidate_resume") or ""))
    slots = list(request.get("slots") or [])
    if len(slots) > len(sentences):
        raise UnscriptedTask(
            f"evidence anchoring asked for {len(slots)} anchors and the resume "
            f"holds {len(sentences)} quotable sentences"
        )
    return {
        "questions": [
            {
                "index": slot["index"],
                "prompt": (
                    f"Your resume says: {sentences[position]} Take me through what "
                    f"{slot['skill']} demanded of you there and what you decided."
                ),
                "resume_anchor": sentences[position],
                "sub_type": str(slot.get("suggested_sub_type") or _SUB_TYPES[position % len(_SUB_TYPES)]),
                "anchor_source": "resume",
            }
            for position, slot in enumerate(slots)
        ]
    }


#: The objective answer keys. The candidate in `harness.golden_journey`
#: answers with exactly these.
MCQ_CORRECT = "b"
FILL_BLANK_ANSWER = "debit"


def _mcq_single() -> Any:
    return {
        "prompt": "Which control catches a settlement file that was applied to the ledger twice?",
        "payload": {
            "options": [
                {"id": "a", "text": "Comparing the file total with the bank's closing balance"},
                {"id": MCQ_CORRECT, "text": "An idempotency key on each settlement file, checked before posting"},
                {"id": "c", "text": "Retrying the posting job until it stops raising errors"},
                {"id": "d", "text": "Sorting the entries by value date before posting them"},
            ],
            "correct_option_id": MCQ_CORRECT,
        },
        "misconceptions": {
            "a": "Believes a matching total proves each entry was posted exactly once.",
            "c": "Treats retrying until quiet as the same thing as idempotent posting.",
            "d": "Confuses ordering the entries with detecting a duplicated file.",
        },
    }


def _fill_blank() -> Any:
    return {
        "prompt": "Complete the sentence.",
        "payload": {
            "template": "In double entry bookkeeping every ___ is matched by an equal and opposite credit.",
            "blanks": [{"index": 0, "accepted": [FILL_BLANK_ANSWER], "case_sensitive": False}],
        },
    }


def _composition(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    """`format_composition` serves two writers: the evidence anchoring batch
    (its request carries the slots) and one objective question (its system
    prompt names the format). Told apart by what the request carries, and
    refused when it is neither of the two formats the journey serves."""
    request = _user_payload(messages)
    if isinstance(request, Mapping) and isinstance(request.get("slots"), list):
        return _anchored(request)
    system = next(
        (str(m.get("content") or "") for m in messages if m.get("role") == "system"), ""
    )
    if "fill-in-the-blank" in system:
        return _fill_blank()
    if "exactly one correct option" in system:
        return _mcq_single()
    raise UnscriptedTask("format_composition was asked for a format this script does not write")


# ── Coding: the problem, its tests and every program the sandbox runs ───────


@dataclass(frozen=True)
class CodingProblem:
    """One executed coding question, and every program the journey runs.

    The model double returns it; `script_sandbox` scripts the code execution
    double with each program's output on each input, so the reference proves
    the tests, each starter runs and solves nothing, and the candidate's Run
    and Submit pass. Nothing executes: the double answers by lookup."""

    visible: tuple[tuple[str, str, str], ...]
    hidden: tuple[tuple[str, str], ...]
    reference: str
    candidate: str
    starters: Mapping[str, str]


CODING = CodingProblem(
    visible=(
        ("2\nHDFC 500\nHDFC -500\n", "BALANCED\n", "Both HDFC entries cancel out."),
        ("3\nAXIS 200\nSBI -50\nAXIS -20\n", "AXIS 180\nSBI -50\n", "AXIS and SBI are left open."),
    ),
    hidden=(
        ("0\n", "BALANCED\n"),
        ("1\nICICI 75\n", "ICICI 75\n"),
        ("2\nKOTAK -10\nKOTAK -15\n", "KOTAK -25\n"),
        ("3\nYES 5\nBOB 7\nYES -5\n", "BOB 7\n"),
        ("2\nPNB 1\nCANARA 2\n", "CANARA 2\nPNB 1\n"),
    ),
    reference=(
        "import sys\n"
        "from collections import defaultdict\n\n"
        "def main():\n"
        "    data = sys.stdin.read().split()\n"
        "    n = int(data[0])\n"
        "    net = defaultdict(int)\n"
        "    for i in range(n):\n"
        "        net[data[1 + 2 * i]] += int(data[2 + 2 * i])\n"
        "    open_banks = sorted(b for b, v in net.items() if v != 0)\n"
        "    if not open_banks:\n"
        "        print('BALANCED')\n"
        "        return\n"
        "    for bank in open_banks:\n"
        "        print(bank, net[bank])\n\n"
        "main()\n"
    ),
    candidate=(
        "import sys\n\n"
        "lines = sys.stdin.read().splitlines()\n"
        "count = int(lines[0])\n"
        "totals = {}\n"
        "for line in lines[1:count + 1]:\n"
        "    bank, amount = line.split()\n"
        "    totals[bank] = totals.get(bank, 0) + int(amount)\n"
        "unsettled = [bank for bank in sorted(totals) if totals[bank] != 0]\n"
        "print('\\n'.join(bank + ' ' + str(totals[bank]) for bank in unsettled) or 'BALANCED')\n"
    ),
    starters={
        "python": "import sys\n\ndef main():\n    data = sys.stdin.read().split()\n    # write your solution here\n    print()\n\nmain()\n",
        "java": (
            "import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n"
            "        Scanner in = new Scanner(System.in);\n        // write your solution here\n"
            "        System.out.println();\n    }\n}\n"
        ),
        "cpp": "#include <iostream>\nint main() {\n    // write your solution here\n    std::cout << std::endl;\n    return 0;\n}\n",
        "javascript": (
            "const data = require('fs').readFileSync(0, 'utf8').split(/\\s+/);\n"
            "// write your solution here\nconsole.log('');\n"
        ),
    },
)

CODING_TITLE = "Unsettled bank positions"
#: The fragment of the candidate's program the reviewer cites, verbatim.
CODING_CITATION = "totals[bank] = totals.get(bank, 0) + int(amount)"


def _coding_question(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    request = _user_payload(messages)
    if not isinstance(request, Mapping):
        raise UnscriptedTask("coding_question_generation was asked without a JSON payload")
    languages = [str(item["key"]) for item in request.get("languages") or []]
    missing = [key for key in languages if key not in CODING.starters]
    if missing:
        raise UnscriptedTask(f"coding_question_generation for unscripted languages {missing}")
    return {
        "title": CODING_TITLE,
        "statement": (
            "A settlement file lists one entry per line: a bank code and a signed amount in "
            "paise. Entries for the same bank net against each other. Print every bank whose "
            "entries do not net to zero, in alphabetical order, each with its net amount. If "
            "every bank nets to zero, print BALANCED."
        ),
        "input_format": (
            "The first line holds the number of entries. Each following line holds a bank "
            "code and an integer amount separated by one space."
        ),
        "output_format": (
            "One line per unsettled bank, the code, a space and the net amount, or the single "
            "word BALANCED."
        ),
        "constraints": "Bank codes are upper case letters and amounts fit in a signed integer.",
        "starter_code": {key: CODING.starters[key] for key in languages},
        "visible_tests": [
            {"stdin": stdin, "expected_stdout": out, "explanation": why}
            for stdin, out, why in CODING.visible
        ],
        "hidden_tests": [{"stdin": stdin, "expected_stdout": out} for stdin, out in CODING.hidden],
        "reference_solution": CODING.reference,
        "expected_approach": (
            "Accumulate a net amount per bank in one pass, keep the banks whose net is not "
            "zero and sort them. The edge cases are an empty file, a bank that nets to zero "
            "and several unsettled banks."
        ),
    }


def _coding_review(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    from app.services.coding_assessment.review import CRITERIA  # noqa: PLC0415

    if CODING_CITATION not in _all_text(messages):
        raise UnscriptedTask("coding_quality_review was asked about code this script did not submit")
    return {
        "score": 82,
        "criteria": {name: 0.8 for name in CRITERIA},
        "reasoning": (
            "The program nets every bank in a single pass with a dictionary and prints the "
            "unsettled banks in sorted order, which is exactly what the problem asks. It "
            "handles an empty file and a fully balanced file through the same fallback word. "
            "Reading the whole input once is idiomatic and cheap, although it assumes the "
            "input carries no blank lines between entries, which the problem never promises."
        ),
        "citations": [CODING_CITATION],
    }


def script_sandbox(provider: Any, languages: Sequence[str]) -> None:
    """Script the code execution double for every program the journey runs:
    the reference and the candidate on every test, and each starter on every
    hidden input (it runs cleanly and prints nothing, so it solves nothing)."""
    from app.services.code_execution.fake import ScriptedRun  # noqa: PLC0415

    every = [(stdin, out) for stdin, out, _why in CODING.visible] + list(CODING.hidden)
    for stdin, out in every:
        provider.script(CODING.reference, stdin, ScriptedRun(stdout=out))
        provider.script(CODING.candidate, stdin, ScriptedRun(stdout=out))
    for key in languages:
        for stdin, _out in CODING.hidden:
            provider.script(CODING.starters[key], stdin, ScriptedRun(stdout="\n"))


# ── The conversation (Vaada) ─────────────────────────────────────────────────

_WRITE_MARKER = "The skill you are probing now:"
_ITEM = re.compile(r"The skill you are probing now: (.+)")
_ANCHOR = re.compile(r'quoted exactly: "(.+?)"\. The question')

#: The angle a rewritten question takes, chosen by how many questions the
#: candidate has already been asked, so no two turns read alike.
_TURN_ANGLES: tuple[str, ...] = (
    "Tell me about the most recent piece of work where {item} decided the outcome, and what you personally did.",
    "Describe a time your approach to {item} went wrong in production and how you put it right.",
    "Walk me through a decision about {item} that you would still defend today, and why you made it.",
    "Tell me how you checked your own work on {item} before anybody else relied on it.",
    "Describe how you taught {item} to a newer engineer on your team, using one real example.",
    "Tell me about pressure on a deadline where {item} mattered, and what you chose to do first.",
    "Describe a disagreement about {item} with a colleague and how you settled it.",
    "Tell me about the part of {item} you improved most over the last year and how you know it improved.",
)

_RUBRIC = {
    "0_39": "Speaks only in general terms and cannot name a situation, a decision or an outcome of their own.",
    "40_59": "Names a situation but describes the team's work rather than their own part in it.",
    "60_74": "Describes their own actions in one real situation with a plausible outcome.",
    "75_89": "Explains their own decisions, the trade offs they weighed and a verifiable outcome.",
    "90_100": "Gives a precise account of their decisions, the alternatives rejected and what they changed afterwards.",
}

#: The one follow-up the interviewer asks, on the first answer it weighs.
FOLLOW_UP = "What would you have done if the partner bank had refused to resend the corrected file?"


def _write_turn(model: "GoldenModel", system: str, request: Mapping[str, Any]) -> Any:
    item_match = _ITEM.search(system)
    if item_match is None:
        raise UnscriptedTask("a question was asked to be written with no skill named")
    item = item_match.group(1).strip()
    anchor_match = _ANCHOR.search(system)
    asked = len(request.get("already_asked") or [])
    if anchor_match is not None:
        question = (
            f'Your resume says "{anchor_match.group(1)}" What did you personally '
            f"decide in that work on {item}, and what happened because of it?"
        )
    else:
        question = _TURN_ANGLES[asked % len(_TURN_ANGLES)].format(item=item)
    answer: dict[str, Any] = {"question": question}
    if '"0_39"' in system:
        answer["rubric"] = dict(_RUBRIC)
    return answer


def _conversation_turn(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    """`conversation_turn` serves three callers on the journey's path, told
    apart by what they send: the question writer (its system prompt names the
    skill being probed), the relevance classifier (it sends the candidate's
    reply) and the follow-up decision (it sends the current question and the
    answer). Anything else is refused."""
    system = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
    request = _user_payload(messages)
    if not isinstance(request, Mapping):
        raise UnscriptedTask("conversation_turn was asked without a JSON payload")
    if _WRITE_MARKER in system:
        return _write_turn(model, system, request)
    if "candidate_reply" in request:
        return {
            "label": "substantive",
            "confidence": "high",
            "reason": "The reply describes the candidate's own work on the question asked.",
        }
    if "candidate_answer" in request:
        if model.follow_ups:
            return {"follow_up": None}
        model.follow_ups += 1
        return {"follow_up": FOLLOW_UP}
    raise UnscriptedTask("conversation_turn was asked something this script does not answer")


# ── Grading (Miti) and the report (Siddhi) ───────────────────────────────────

#: The score every model-judged answer is given: a strong, specific answer.
#: Internal; it never leaves the server, and the journey asserts that.
JUDGED_SCORE = 91

_EVIDENCE_REF = re.compile(r"^\s+\[([^\]]+)\] \(", re.MULTILINE)
_COMPETENCY = re.compile(r"^\s+- (.+)$", re.MULTILINE)


def _answer_evaluation(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    """`answer_evaluation` serves two judges: the evidence-answer evaluation
    (its request carries the answer alone, and the reply cites it verbatim)
    and Miti's rubric score (its request carries the question beside it)."""
    from app.services.assessment_formats.evaluation import EVIDENCE_CRITERIA  # noqa: PLC0415

    request = _user_payload(messages)
    if not isinstance(request, Mapping) or not str(request.get("answer") or "").strip():
        raise UnscriptedTask("answer_evaluation was asked about no answer")
    if "question" in request:
        return {"score": JUDGED_SCORE, "band": "90_100"}
    words = str(request["answer"]).split()
    return {
        "score": JUDGED_SCORE,
        "rubric_scores": {name: 0.9 for name in EVIDENCE_CRITERIA},
        "reasoning": (
            "The answer gives a first person account of one real situation, names the "
            "decision the candidate took and the reason for it, and states what changed "
            "afterwards. It stays on the resume item the question was anchored to and "
            "describes the candidate's own part rather than the team's, which is the "
            "evidence this question was written to find."
        ),
        "citations": [" ".join(words[:6])],
    }


def _dimension(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    """One of Miti's five isolated evaluators: a band, cited by the evidence
    refs its own request listed, or an honest insufficient when it listed none."""
    text = next((str(m.get("content") or "") for m in messages if m.get("role") == "user"), "")
    refs = _EVIDENCE_REF.findall(text)
    competencies = _COMPETENCY.findall(text.split("EVIDENCE", 1)[0])
    if not refs:
        return {"band": "absent", "evidence_refs": [], "insufficient_evidence": True, "rationale": ""}
    return {
        "band": "solid",
        "evidence_refs": refs[:2],
        "rationale": "The cited answers describe the candidate's own decisions and their outcomes.",
        "insufficient_evidence": False,
        "per_competency": {name.strip(): "solid" for name in competencies},
    }


def _evidence_terms(evidence: str) -> list[str]:
    seen: list[str] = []
    for token in re.findall(r"[a-z]+", evidence.casefold()):
        if len(token) >= 7 and token not in seen:
            seen.append(token)
    return seen[:3]


_REMARK_EVIDENCE = re.compile(r"Evidence: (.*)$", re.DOTALL)
#: Stand-ins when the evidence carries fewer than three long terms, so every
#: remark has the same length.
_REMARK_FILLERS = ("ownership", "judgement", "follow through")


def _remark(system: str) -> str:
    """A remark inside the product's word range, anchored on terms from the
    evidence the request carried, naming nothing the evidence does not, and
    stating no figure."""
    from app.services.siddhi.remarks import SKILL_REMARK_WORDS, word_count  # noqa: PLC0415

    match = _REMARK_EVIDENCE.search(system)
    terms = (_evidence_terms(match.group(1) if match else "") + list(_REMARK_FILLERS))[:3]
    remark = (
        "The candidate gave a specific first person account that refers to "
        f"{terms[0]}, {terms[1]} and {terms[2]} in their own words. They explained the "
        "decision they took, the reason for it and what changed afterwards, which is "
        "the evidence this skill asks for. Nothing in the other answers "
        "contradicted it."
    )
    if not SKILL_REMARK_WORDS[0] <= word_count(remark) <= SKILL_REMARK_WORDS[1]:
        raise UnscriptedTask(f"the scripted remark is {word_count(remark)} words")
    return remark


def _report_synthesis(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    system = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
    if "Return only the remark." in _all_text(messages):
        return _remark(system)
    raise UnscriptedTask("report_synthesis was asked for something other than a remark")


def _context_prefix(model: "GoldenModel", messages: Sequence[Mapping[str, Any]]) -> Any:
    """The situating sentence the retrieval index stores beside a chunk."""
    text = _all_text(messages)
    chunk = text.split("<chunk>", 1)[-1].split("</chunk>", 1)[0]
    words = [word for word in re.findall(r"[A-Za-z]+", chunk)][:6]
    return (
        "This passage is taken from the document above and begins with the words "
        + " ".join(words)
        + ", which places it in the part of the document it describes."
    )


#: task type -> how the model answers it. Extended one entry per task the
#: journey reaches; a task missing here is refused and recorded.
SCRIPT: dict[str, Answer] = {
    "skills_drafting": _skills_draft,
    "assessment_context": _assessment_context,
    "yukti_matching": _yukti,
    "extraction": _extraction,
    "question_generation": _questions,
    "format_composition": _composition,
    "coding_question_generation": _coding_question,
    "coding_quality_review": _coding_review,
    "conversation_turn": _conversation_turn,
    "answer_evaluation": _answer_evaluation,
    "dimension_evaluation": _dimension,
    "report_synthesis": _report_synthesis,
    "context_prefix": _context_prefix,
}


@dataclass
class GoldenModel:
    """The scripted router. `calls` is every task answered, in order."""

    script: Mapping[str, Answer] = field(default_factory=lambda: dict(SCRIPT))
    calls: list[str] = field(default_factory=list)
    unscripted: list[str] = field(default_factory=list)
    #: How many follow-ups the scripted interviewer has asked.
    follow_ups: int = 0

    def answer(self, task_type: str, messages: Sequence[Mapping[str, Any]]) -> str:
        handler = self.script.get(task_type)
        if handler is None:
            raise UnscriptedTask(task_type)
        value = handler(self, messages)
        self.calls.append(task_type)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    @contextmanager
    def installed(self) -> Iterator["GoldenModel"]:
        """Swap `invoke_llm` at the router and at the two modules that bound
        the name at import, and put every one back on the way out."""
        from app.services import llm_router, web_research  # noqa: PLC0415
        from app.services.rag import contextual  # noqa: PLC0415

        async def _invoke(task_type: str, messages: list[dict[str, Any]], *args: Any, **kwargs: Any) -> str:
            try:
                raw = self.answer(task_type, messages)
            except UnscriptedTask as exc:
                self.unscripted.append(str(exc.args[0]) if exc.args else task_type)
                raise llm_router.LLMUnavailableError(
                    f"the golden journey scripts no answer for {task_type}"
                ) from exc
            validate = kwargs.get("validate")
            if validate is not None:
                validate(raw)
            return raw

        targets = (llm_router, web_research, contextual)
        previous = [(module, module.invoke_llm) for module in targets]
        for module in targets:
            module.invoke_llm = _invoke
        try:
            yield self
        finally:
            for module, original in previous:
                module.invoke_llm = original
