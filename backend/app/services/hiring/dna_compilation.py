"""LAYER 2, STORED: the compiled Company DNA artifact, versioned and read back.

PROVENANCE
----------
Runbook §15 (purpose, and the compilation rule), §16 (the twelve-section
Company DNA Intake Instrument), §17.1 (the compilation table the stored
document is shaped after) and Appendix A (the field form, A1 to A12).
spec-doc6 §4.2 is the activation brief.

WHAT THIS MODULE IS FOR, AND WHY IT IS NOT `company_dna.py`
------------------------------------------------------------
`services/hiring/company_dna` is the INSTRUMENT and the COMPILER: the twelve
sections verbatim, the observable-evidence detector, the prohibited-disqualifier
list, and `compile_artifact`, which turns a completed intake into §15's six
output kinds. It knows nothing about storage, versions, or the person on the
other side of the session.

This module is the half that does:

  1. SHAPE. `compile_document` wraps the compiler's output in the small amount
     §17.1 asks for that is a property of the STORED artifact rather than of the
     compilation: a schema version, the DNA version this is, and stable
     `CDNA-nn` identifiers on the behavioural competencies so a scorecard item
     can cite one and still mean the same thing after a recompilation.
  2. FINGERPRINT. `checksum` over a canonical serialisation. That is what makes
     a version diffable, what makes the determinism test meaningful, and what
     the client's explicit confirmation is bound to.
  3. PLAIN LANGUAGE. `plain_language` restates the artifact in sentences with no
     number in them. That is what Bodha reads back before the session closes and
     what the completed view shows afterwards.
  4. VALIDATION AT THE BOUNDARY. `validate_answer` refuses an answer of the
     wrong kind, which is where §16.2's forced scales and §16.3's observable
     evidence stop being a rendering convention and start being a rule.
  5. RETRIEVAL. `load_compiled` is the only path from a stored row to a
     downstream consumer, and it is narrow on purpose.

DETERMINISTIC, AND CALLS NO MODEL
----------------------------------
There is no import of `llm_router` here and the only `async def` is the
database read. That is not a convenience. A Company DNA artifact constrains
every job a client will ever post, so it has to be reproducible (two runs over
one intake produce the same configuration), diffable between versions (a client
asking what changed when they revised it needs an answer that is not a re-run),
and explainable with every provider down. A sampled artifact would make a
rubric disagreement indistinguishable from provider noise, which is the same
argument the Miti aggregator makes and the same one the gates make.

`tests/test_company_dna_compilation.py` proves both properties by an AST walk
over this file rather than by trusting this docstring, because a rule that
lives only in a comment is a rule the next person adding "one quick call to
tidy the phrasing" will not see.

SUTRA NEVER SEES THE RAW SESSION
---------------------------------
`load_compiled` selects three columns: the version, the compiled document and
the completion timestamp. It does not select the raw intake, it does not select
the session transcript, and neither column name appears anywhere in this file.
`CompiledDNA` is a frozen dataclass whose field set is asserted EXACTLY, the
same technique `miti.EvaluatorInput` uses and for the same reason: a later
field called `context` would pass a test that only checked for the absence of
particular names.

`CompiledDNA.engine_view()` narrows once more, to the keys the transformation
pipeline and the gates actually read. An unbounded client-authored string in a
prompt that decides what every candidate is graded on is both an injection
surface and the way "we like people who are hungry" becomes an evaluation
criterion.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hiring import CompanyDNA as CompanyDNARow
from app.services.hiring import company_dna

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "BEHAVIOURAL_ID_PREFIX",
    "AnswerRejected",
    "CompiledDNA",
    "ExamplePair",
    "RUNBOOK_DATA_FILE",
    "RUNBOOK_MARKDOWN",
    "RunbookDataUnavailable",
    "SUTRA_KEYS",
    "SectionProgress",
    "canonical_json",
    "checksum",
    "compile_document",
    "engine_object",
    "instrument_example_pairs",
    "load_compiled",
    "plain_language",
    "progress",
    "runbook_example_pairs",
    "runbook_source_example_pairs",
    "validate_answer",
]


#: Bumped when the SHAPE of the stored document changes. Carried on the document
#: so an artifact written by an older compiler is recognisable rather than
#: merely different, and so the golden file under `tests/fixtures/company_dna`
#: fails loudly on a structural change instead of drifting past review.
ARTIFACT_SCHEMA_VERSION = 1

#: Runbook §17.1 gives behavioural competencies stable ids (CDNA-01 onward).
#: Stable because a scorecard item traces back to one, and a renumbering would
#: silently repoint every trace that already exists.
BEHAVIOURAL_ID_PREFIX = "CDNA"

#: The keys the engine reads. Runbook §15's compilation rule, as code.
#:
#: `transformation.derive_weight` reads `weight_modifiers`;
#: `transformation.derive_threshold` reads `independence_required`,
#: `threshold_modifier` and `evidence_max_age_days`; stage 2 of the
#: transformation pipeline and Vaada's question generation read
#: `observable_signals` and `risk_probes`; the gates read `disqualifiers`.
#: Nothing else in the document is configuration, and nothing else crosses this
#: boundary. `recruiter_context` in particular is §15's explicitly labelled
#: leftover bucket and is exactly what must not reach a prompt.
SUTRA_KEYS: tuple[str, ...] = (
    "weight_modifiers",
    "independence_required",
    "evidence_max_age_days",
    "threshold_modifier",
    "disqualifiers",
    "observable_signals",
    "risk_probes",
)

#: Where the extracted Runbook data lives. Named here so an error can quote a
#: path somebody can go and look at.
RUNBOOK_DATA_FILE = "app/services/hiring/runbook_data/company_dna_instrument.yaml"

#: The Runbook itself, at the repository root. This module is
#: backend/app/services/hiring/dna_compilation.py, so the root is four parents
#: above the `hiring` package directory.
RUNBOOK_MARKDOWN = (
    pathlib.Path(__file__).resolve().parents[4] / "Readypick Hiring Philosophy.md"
)

_EXAMPLE_LINE = re.compile(r'^>\s*(Rejected|Accepted):\s*"?(.+?)"?\s*$')


class RunbookDataUnavailable(RuntimeError):
    """The extracted Runbook data this call site needs is not present."""


class AnswerRejected(ValueError):
    """An intake answer the instrument refuses, with what to say to the client.

    Carries the client-facing sentence rather than leaving the caller to invent
    one. A rejection that does not show the difference between what was given
    and what is wanted teaches nothing, and the client rephrases the same
    adjective.
    """

    def __init__(self, question_key: str, message: str) -> None:
        super().__init__(message)
        self.question_key = question_key
        self.message = message


# ── The Runbook's own accepted and rejected example pairs ────────────────────


@dataclass(frozen=True)
class ExamplePair:
    """One Runbook §16.3 pair. `rejected` must fail the detector and `accepted`
    must pass it. The pair, not either half alone, is the quality bar."""

    rejected: str
    accepted: str


def instrument_example_pairs() -> tuple[ExamplePair, ...]:
    """The pairs the INSTRUMENT carries, section by section.

    This is what the intake screen shows, and it is the instrument's own copy
    rather than a second read of the Runbook, so the question and the example
    beside it can never come from two different versions of §16.
    """
    return tuple(
        ExamplePair(rejected=example.rejected, accepted=example.accepted)
        for section in company_dna.SECTIONS
        for example in section.examples
    )


def runbook_example_pairs() -> tuple[ExamplePair, ...]:
    """The §16.3 pairs as extracted into `runbook_data/`.

    Imported LAZILY and deliberately not wrapped in a fallback: if the
    extraction has not been produced, this raises and names the file, because
    the alternative, a hardcoded default, is a quality bar that has silently
    stopped being the Runbook's.

    No production path calls this. It is one of the three sources the parity
    test in `tests/test_company_dna_runbook_examples.py` compares, alongside the
    Runbook document itself and the instrument's own copy.
    """
    try:
        from app.services.hiring import runbook_data
    except ImportError as exc:
        raise RunbookDataUnavailable(
            f"{RUNBOOK_DATA_FILE} has not been extracted from the Runbook yet, "
            "so the accepted and rejected example pairs cannot be loaded from it."
        ) from exc
    loader = getattr(runbook_data, "company_dna_instrument", None)
    if loader is None:
        raise RunbookDataUnavailable(
            "app.services.hiring.runbook_data exists but exposes no "
            f"company_dna_instrument() loader for {RUNBOOK_DATA_FILE}."
        )
    pairs = _pairs_from_extract(loader())
    if not pairs:
        raise RunbookDataUnavailable(
            f"{RUNBOOK_DATA_FILE} loaded but carries no accepted and rejected "
            "example pairs for the observable-evidence section."
        )
    return pairs


def _pairs_from_extract(document: Any) -> tuple[ExamplePair, ...]:
    """Pull pairs out of the extracted instrument, whatever nesting it uses.

    The extraction is another module's file and its nesting is not this
    module's to fix, so this walks for the two keys rather than asserting a
    path. A shape it cannot read yields no pairs, which `runbook_example_pairs`
    turns into a named error rather than into an empty success.
    """
    found: list[ExamplePair] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            rejected = node.get("rejected")
            accepted = node.get("accepted")
            if isinstance(rejected, str) and isinstance(accepted, str):
                found.append(
                    ExamplePair(rejected=rejected.strip(), accepted=accepted.strip())
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(document)
    return tuple(found)


def runbook_source_example_pairs() -> tuple[ExamplePair, ...]:
    """The §16.3 pairs read straight out of the Runbook document.

    Not a fallback for the extraction: this is the SOURCE the extraction is
    made from, and spec-doc6 §0.2 puts it above every other document.
    """
    if not RUNBOOK_MARKDOWN.exists():
        raise RunbookDataUnavailable(
            f"the Runbook is not at {RUNBOOK_MARKDOWN}, so the observable "
            "evidence example pairs have no source."
        )
    pairs: list[ExamplePair] = []
    pending_rejected: str | None = None
    for line in RUNBOOK_MARKDOWN.read_text(encoding="utf-8").splitlines():
        match = _EXAMPLE_LINE.match(line.strip())
        if match is None:
            continue
        label, text = match.group(1), match.group(2).strip()
        if label == "Rejected":
            pending_rejected = text
        elif pending_rejected is not None:
            pairs.append(ExamplePair(rejected=pending_rejected, accepted=text))
            pending_rejected = None
    if not pairs:
        raise RunbookDataUnavailable(
            "no accepted and rejected example pairs were found in the Runbook, "
            "so the observable-evidence quality bar has no source."
        )
    return tuple(pairs)


# ── Answer validation, at the API layer ──────────────────────────────────────


def validate_answer(question: company_dna.Question, raw: Any) -> Any:
    """The coerced answer, or `AnswerRejected` carrying what to say to the client.

    THE FORCED SCALES ARE REFUSED HERE, NOT HIDDEN IN THE UI. Runbook §16.2 is
    explicit that Section 2 is answered on a forced scale rather than in free
    text, and a rule enforced only by the control that renders the question is
    a rule anybody with a terminal is exempt from. A prose answer to a scale
    question is refused and both poles are named back, so the refusal teaches
    rather than merely blocks.

    Section 3 and Section 4 are refused by `company_dna.is_observable`, whose
    bar is the Runbook's own accepted and rejected pairs.
    """
    kind = question.kind
    if kind == company_dna.SCALE_QUESTION:
        return _validate_scale(question, raw)
    if kind == company_dna.CHOICE_QUESTION:
        return _validate_choice(question, raw)
    if kind == company_dna.EVIDENCE_QUESTION:
        return _validate_evidence(question, raw)
    if kind == company_dna.EVIDENCE_LIST_QUESTION:
        return _validate_evidence_list(question, raw)
    return _validate_text(question, raw)


def _validate_scale(question: company_dna.Question, raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise AnswerRejected(question.key, _scale_message(question))
    text = str(raw).strip()
    if not re.fullmatch(r"\d+", text):
        raise AnswerRejected(question.key, _scale_message(question))
    value = int(text)
    if value < company_dna.SCALE_MIN or value > company_dna.SCALE_MAX:
        raise AnswerRejected(question.key, _scale_message(question))
    return value


def _scale_message(question: company_dna.Question) -> str:
    poles = question.poles or ("one end", "the other")
    return (
        "This one is a position on a scale rather than something to write out. "
        f'Pick a point between "{poles[0]}" and "{poles[1]}". The middle is a '
        "real answer: it means you genuinely have no preference between them."
    )


def _validate_choice(question: company_dna.Question, raw: Any) -> str:
    text = str(raw or "").strip()
    for option in question.options:
        if text == option:
            return option
    listed = "; ".join(question.options)
    raise AnswerRejected(
        question.key, f"Pick one of the options for this question: {listed}."
    )


def _validate_evidence(question: company_dna.Question, raw: Any) -> str:
    text = str(raw or "").strip()
    if not company_dna.is_observable(text):
        raise AnswerRejected(question.key, company_dna.rejection_message(text))
    return text


def _section_for(question_key: str) -> company_dna.Section | None:
    for section in company_dna.SECTIONS:
        if any(q.key == question_key for q in section.questions):
            return section
    return None


def _validate_evidence_list(question: company_dna.Question, raw: Any) -> str:
    """Several observable items in one field, one per line.

    EVERY LINE IS JUDGED SEPARATELY, and the first one that fails is named. A
    list judged as one blob would pass on the strength of its best entry, and
    the weakest entry is the one that becomes an unevidenceable competency.

    The count bounds come from the section, because §16 states them ("five to
    eight behaviours") and Appendix A3 prints them. A short list is refused
    with the number asked for, never silently accepted.
    """
    items = [
        line.strip(" -\t")
        for line in str(raw or "").splitlines()
        if line.strip(" -\t")
    ]
    section = _section_for(question.key)
    minimum = getattr(section, "min_items", None) if section else None
    maximum = getattr(section, "max_items", None) if section else None
    item_format = (getattr(section, "item_format", "") if section else "") or ""

    if not items:
        raise AnswerRejected(
            question.key,
            "I need at least one item here, written as something I could have "
            "watched happen." + (f" {item_format}" if item_format else ""),
        )
    # OBSERVABILITY BEFORE COUNT, deliberately. A client who typed one adjective
    # needs to be told it is an adjective; telling them to write four more of
    # the same thing first teaches the wrong lesson and costs a round trip. The
    # count is checked once every item is already the right shape.
    for item in items:
        if not company_dna.is_observable(item):
            raise AnswerRejected(question.key, company_dna.rejection_message(item))
    # RUNBOOK-AMBIGUITY (16.3): the section states "five to eight behaviours"
    # while Appendix A3 prints five blank slots. Resolved as a repeating field
    # accepting five to eight, per RUNBOOK_OPEN_QUESTIONS.md Q11. The bounds are
    # read off the SECTION rather than restated here, so the resolution lives in
    # exactly one place.
    if minimum and len(items) < minimum:
        raise AnswerRejected(
            question.key,
            f"This one asks for at least {_spelled(minimum)} separate items, one "
            "per line. One broad statement cannot be probed the way several "
            "specific ones can.",
        )
    if maximum and len(items) > maximum:
        raise AnswerRejected(
            question.key,
            f"That is more than the {_spelled(maximum)} items this asks for. "
            "Keep the ones your strongest people most clearly show.",
        )
    return "\n".join(items)


#: Numbers are written as words in anything a client reads. The product rule is
#: about ratings rather than counts, but a refusal that says "at least 5" reads
#: like a score even when it is not, and there are only ever a handful here.
_NUMBER_WORDS: dict[int, str] = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _spelled(count: int) -> str:
    return _NUMBER_WORDS.get(count, str(count))


def _validate_text(question: company_dna.Question, raw: Any) -> str:
    text = str(raw or "").strip()
    if question.required and not text:
        raise AnswerRejected(
            question.key, "This one needs an answer before we can carry on."
        )
    return text


# ── Session progress ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SectionProgress:
    """How far through one section the client is.

    REQUIRED AND OPTIONAL ARE COUNTED SEPARATELY, because four of §16's twelve
    sections carry no required question at all (non-negotiables, offer reality,
    sourcing preferences and historical calibration: a client with no absolute
    requirements has a real and complete answer to Section 5). `complete` means
    nothing is outstanding, so those four are complete from the first moment,
    and `required_total` is what tells a screen to label the section optional
    rather than to draw a tick nobody earned.
    """

    key: str
    title: str
    intent: str
    #: Every question in the section, and how many carry an answer.
    total: int
    answered: int
    #: The subset the session cannot close without.
    required_total: int
    required_answered: int
    complete: bool


def progress(answers: Mapping[str, Any]) -> tuple[SectionProgress, ...]:
    """Per-section progress across the instrument, in instrument order.

    Section by section rather than one bar, because the session is resumable
    and "you are part of the way through" is not something a person can act on.
    "Sections one to four are done and you are in the middle of five" is.
    """
    out: list[SectionProgress] = []
    for section in company_dna.SECTIONS:
        required = [q for q in section.questions if q.required]
        required_answered = [q for q in required if _answered(answers.get(q.key))]
        answered = [q for q in section.questions if _answered(answers.get(q.key))]
        out.append(
            SectionProgress(
                key=section.key,
                title=section.title,
                intent=section.intent,
                total=len(section.questions),
                answered=len(answered),
                required_total=len(required),
                required_answered=len(required_answered),
                complete=len(required_answered) == len(required),
            )
        )
    return tuple(out)


def _answered(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


# ── The stored document ──────────────────────────────────────────────────────


def compile_document(answers: Mapping[str, Any], *, dna_version: int) -> dict[str, Any]:
    """The Runbook §17.1 artefact, as the dictionary stored on the row.

    A THIN LAYER OVER `company_dna.compile_artifact`, ON PURPOSE. The
    compilation itself belongs to the instrument module, which owns §15's six
    output kinds and the refusals that go with them. Duplicating any of that
    here would be a second answer to "what does this client's philosophy
    configure", and the second answer is always the one somebody finds later.

    What is added is a property of the STORED artifact rather than of the
    compilation: the schema version, which version of the client's DNA this is,
    and the `CDNA-nn` identifiers §17.1 puts on the behavioural competencies.

    Deterministic: the same answers and the same version produce a
    byte-identical `canonical_json`. Nothing here is a timestamp, a uuid, a
    random draw or an iteration over an unordered set, and there is no model
    call anywhere in the path.
    """
    engine = company_dna.compile_artifact(_in_instrument_order(answers)).as_dict()
    document: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "dna_version": int(dna_version),
    }
    document.update(engine)
    document["behavioural_competencies"] = _behavioural_competencies(engine)
    return document


def _in_instrument_order(answers: Mapping[str, Any]) -> dict[str, Any]:
    """The same answers, keyed in the order the instrument asks them.

    THIS IS LOAD-BEARING, NOT TIDINESS. `company_dna.compile_artifact` iterates
    the mapping it is handed, and its `provenance` list therefore comes out in
    whatever order the caller's dictionary happened to be in. Two things make
    that a real defect rather than a cosmetic one:

      * a JSONB column does not promise to return its keys in the order they
        were written, so an intake read back from the row could compile to a
        different `provenance` order than the one just written;
      * the checksum covers the whole document, and the checksum is what the
        client's explicit confirmation is bound to. A reordered provenance list
        is a different fingerprint, so the confirmation would be refused on the
        next read for a reason nobody could see.

    Ordering by the INSTRUMENT rather than by sorting the keys is deliberate:
    provenance reads as a record of the session, and the session runs in
    instrument order. Answers to keys the instrument does not know are kept, in
    sorted order, so a stale key from an older version of the instrument is
    still carried and is still deterministic.

    REPORTED UPSTREAM: the order sensitivity is in `compile_artifact` and is
    normalised here rather than there because that module is owned elsewhere.
    """
    order = {
        question.key: index
        for index, question in enumerate(
            q for section in company_dna.SECTIONS for q in section.questions
        )
    }
    unknown = sorted(key for key in answers if key not in order)

    def position(key: str) -> int:
        # `dict.get(key, default)` evaluates its default eagerly, so the
        # fallback has to be a branch rather than an argument.
        if key in order:
            return order[key]
        return len(order) + unknown.index(key)

    return {key: answers[key] for key in sorted(answers, key=position)}


def _behavioural_competencies(engine: Mapping[str, Any]) -> list[dict[str, str]]:
    """Runbook §17.1's `behavioural_competencies`, with stable CDNA ids.

    The statements are §16 Section 3's observable-evidence answers, which the
    instrument has already validated and which it keeps deliberately separate
    from Section 4's risk probes: one is what to look for and the other is what
    to look out for, and one bucket would lose the difference.

    NO DEFAULT WEIGHT IS ATTACHED. §17.1's worked example carries a
    `default_weight_in_D3`; that number belongs to the transformation pipeline,
    which derives a weight from all three layers and clamps the product.
    Writing one here would be a second, unclamped source for the same figure.
    """
    return [
        {
            "id": f"{BEHAVIOURAL_ID_PREFIX}-{index:02d}",
            "statement": statement,
            "assessment_route": "structured_behavioural_probe",
        }
        for index, statement in enumerate(engine.get("observable_signals") or [], 1)
    ]


def canonical_json(document: Mapping[str, Any]) -> str:
    """One byte sequence per document. Sorted keys, no incidental whitespace.

    This is what the checksum is taken over and what a version diff compares,
    so the ordering has to come from the data rather than from insertion order,
    which a rehydrated JSONB column does not preserve.
    """
    return json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)


def checksum(document: Mapping[str, Any]) -> str:
    """The artifact's fingerprint.

    Three jobs. It is the identity of the understanding the client confirms, so
    a confirmation cannot be carried across an answer changed after it was
    given. It is what a version diff keys on without re-reading two documents.
    And it is what the determinism test compares across processes.
    """
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


# ── The retrieval interface everything downstream uses ───────────────────────


@dataclass(frozen=True)
class CompiledDNA:
    """A stored, completed Layer 2 artifact, as everything downstream sees it.

    THE FIELD SET IS THE BOUNDARY. There is no answers field, no transcript
    field, no pending-prompt field and no free-form context dictionary, and
    `tests/test_company_dna_compilation.py` asserts the EXACT set rather than
    the absence of particular names. A later field called `context` would pass
    a narrower test and reopen the whole hole.
    """

    tenant_id: uuid.UUID
    version: int
    document: dict[str, Any]
    checksum: str
    completed_at: datetime | None

    def engine_view(self) -> dict[str, Any]:
        """The configuration keys, and nothing else.

        Runbook §15's compilation rule at its narrowest point: presentation
        preferences, sourcing hints, refusals, provenance and the explicitly
        labelled `recruiter_context` are for people, and they do not cross into
        the pipeline that decides what a candidate is graded on.
        """
        return {key: self.document.get(key) for key in SUTRA_KEYS}


def engine_object(compiled: CompiledDNA) -> company_dna.CompanyDNA:
    """The `company_dna.CompanyDNA` the transformation pipeline already takes.

    One type for the engine's Layer 2 input, not a second one.
    `transformation` reads `weight_modifiers`, `independence_required`,
    `threshold_modifier` and `evidence_max_age_days` off this object; the
    remaining keys travel because stage 2 and Vaada's question generation read
    the observable signals and the risk probes, and the gates read the
    disqualifiers.
    """
    view = compiled.engine_view()
    return company_dna.CompanyDNA(
        weight_modifiers=dict(view.get("weight_modifiers") or {}),
        independence_required=int(view.get("independence_required") or 1),
        evidence_max_age_days=view.get("evidence_max_age_days"),
        threshold_modifier=float(view.get("threshold_modifier") or 1.0),
        disqualifiers=list(view.get("disqualifiers") or []),
        observable_signals=list(view.get("observable_signals") or []),
        risk_probes=list(view.get("risk_probes") or []),
    )


async def load_compiled(
    session: AsyncSession, tenant_id: uuid.UUID | str, *, version: int | None = None
) -> CompiledDNA | None:
    """The client's compiled Layer 2 artifact, or None when they have none.

    THREE COLUMNS. The raw intake and the session transcript are not selected,
    are not returned, and are not reachable from the object this hands back.

    None is a real answer and it is not an error: a client with no Company DNA
    can still create jobs and draft descriptions. What they cannot do is lock a
    scorecard, because there is nothing for Sutra to compile against. The
    refusal itself belongs to gate G1 in `hiring.gates`, which is reached only
    from `miti.pipeline` and is therefore not on any live path yet; this
    function returning None is the CONDITION G1 will read, not the refusal.
    """
    tid = uuid.UUID(str(tenant_id))
    query = select(
        CompanyDNARow.version,
        CompanyDNARow.artifact_json,
        CompanyDNARow.completed_at,
    ).where(CompanyDNARow.tenant_id == tid, CompanyDNARow.status == "complete")
    query = (
        query.where(CompanyDNARow.version == int(version))
        if version is not None
        else query.where(CompanyDNARow.is_current.is_(True))
    )
    row = (await session.execute(query.limit(1))).first()
    if row is None:
        return None
    document = dict(row[1] or {})
    return CompiledDNA(
        tenant_id=tid,
        version=int(row[0]),
        document=document,
        checksum=checksum(document),
        completed_at=row[2],
    )


# ── Plain language, with no number in it ─────────────────────────────────────

#: The five dimensions in the words a person uses about them, keyed on the
#: names `hiring.ontology` uses. A renamed dimension therefore shows up as a
#: missing sentence in a test rather than as a silently generic one.
_DIMENSION_PHRASES: dict[str, tuple[str, str]] = {
    "verified_competence": (
        "what someone can demonstrably do",
        "we will lean less on demonstrated skill and more on the rest of the picture",
    ),
    "track_record_impact": (
        "what someone has already delivered",
        "we will lean less on past delivery and more on where someone could get to",
    ),
    "role_context_fit": (
        "how closely someone matches the way your team actually works",
        "we will worry less about fitting your current way of working",
    ),
    "authenticity_consistency": (
        "whether the account of the work holds up when it is checked",
        "we will spend less of the assessment on verification",
    ),
    "trajectory_potential": (
        "where someone is heading rather than only where they are",
        "we will lean less on potential and more on what is already there",
    ),
}


def plain_language(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What the compiled artifact MEANS, in sentences, carrying no numbers.

    This is what Bodha reads back for explicit confirmation before the session
    closes, and what the completed view shows afterwards. A client confirming a
    table of multipliers is confirming that the arithmetic looks plausible; a
    client confirming "we will look harder at what someone has already
    delivered than at what they might grow into" is confirming the thing they
    actually said.

    NO WEIGHT, PERCENTAGE, RATIO OR SCORE APPEARS IN THE OUTPUT. That is the
    standing product rule, and here it is also the only thing that makes the
    restatement checkable by the person who has to check it.
    """
    return [
        {
            "key": "emphasis",
            "title": "What we will weigh more heavily",
            "lines": _weight_sentences(document.get("weight_modifiers") or {})
            or [
                "You did not lean either way on the trade-offs, so every "
                "candidate is read against our standard balance."
            ],
        },
        {
            "key": "evidence",
            "title": "How much proof a claim needs",
            "lines": _evidence_sentences(document),
        },
        {
            "key": "good",
            "title": "What good looks like at your company",
            "lines": [
                str(item.get("statement") or "")
                for item in document.get("behavioural_competencies") or []
            ]
            or [
                "You have not yet described what your strongest people "
                "demonstrably do, so nothing specific to you is being probed."
            ],
        },
        {
            "key": "risks",
            "title": "What we will probe for, because it has gone wrong before",
            "lines": [str(probe) for probe in document.get("risk_probes") or []]
            or ["You have not described a hire that did not work out."],
        },
        {
            "key": "constraints",
            "title": "Your absolute requirements",
            "lines": _constraint_sentences(document),
        },
        {
            "key": "reach",
            "title": "How widely we will look",
            "lines": _reach_sentences(document),
        },
        {
            "key": "reporting",
            "title": "How your reports will read",
            "lines": _presentation_sentences(document),
        },
        {
            "key": "context",
            "title": "What we noted for your recruiter, and not for the engine",
            "lines": _recruiter_context_lines(document),
        },
    ]


def _weight_sentences(modifiers: Mapping[str, Any]) -> list[str]:
    """One sentence per dimension the client actually moved.

    Ordered by dimension name rather than by size, because ordering by size is
    a ranking and a ranking is a number wearing a different coat.
    """
    lines: list[str] = []
    for dimension in sorted(modifiers):
        try:
            value = float(modifiers[dimension])
        except (TypeError, ValueError):
            continue
        phrases = _DIMENSION_PHRASES.get(dimension)
        if phrases is None or abs(value - 1.0) < 1e-9:
            continue
        raised, lowered = phrases
        lines.append(
            f"We will look harder at {raised}."
            if value > 1.0
            else f"Given what you told us, {lowered}."
        )
    return lines


def _evidence_sentences(document: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    independence = document.get("independence_required")
    if isinstance(independence, int) and independence > 1:
        lines.append(
            "A claim has to be backed by more than one independent source "
            "before we treat it as evidenced. A candidate repeating something "
            "they already wrote is one person saying one thing twice."
        )
    else:
        lines.append(
            "A convincing first-hand account is enough for us to treat a claim "
            "as evidenced, unless something contradicts it."
        )
    max_age = document.get("evidence_max_age_days")
    if isinstance(max_age, int):
        if max_age <= 730:
            lines.append("Experience has to be current to count for much.")
        elif max_age <= 1500:
            lines.append("Fairly recent experience counts; older work counts for less.")
        else:
            lines.append("Older experience still counts, with some discount for age.")
    else:
        lines.append(
            "How long ago something happened does not change what it is worth."
        )
    tenure = str(document.get("tenure_reading") or "").strip()
    if tenure:
        lines.append(tenure)
    raw_level = document.get("threshold_modifier")
    # An ABSENT modifier means "no company adjustment", which is 1.0. Spelled
    # out rather than reached with `or`, because `or` would turn a legitimate
    # zero into 1.0 and a missing key into a number nobody chose, and a
    # threshold quietly defaulting is the kind of neutral-value bug that is
    # invisible afterwards. A present but unparseable value is a corrupted
    # artifact and raises rather than being read as neutral.
    if raw_level is None:
        level = 1.0
    else:
        try:
            level = float(raw_level)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "threshold_modifier on the compiled artifact is not a number: "
                f"{raw_level!r}"
            ) from exc
    if level > 1.0:
        lines.append(
            "Your bar sits above our standard one, so fewer people will clear it."
        )
    elif level < 1.0:
        lines.append(
            "Your bar sits below our standard one, so more people will reach you."
        )
    else:
        lines.append("Your bar sits at our standard one.")
    return lines


def _constraint_sentences(document: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    accepted = [str(item) for item in document.get("disqualifiers") or []]
    refused = [str(item) for item in document.get("refused_disqualifiers") or []]
    if accepted:
        lines.append("We will treat these as absolute, before anything is scored:")
        lines.extend(accepted)
    else:
        lines.append(
            "You named no absolute requirement, so nobody is excluded before "
            "they are assessed."
        )
    if refused:
        lines.append(
            "We could not accept these, because they rest on something a "
            "person may not lawfully be filtered on:"
        )
        lines.extend(refused)
    if document.get("prohibited_filters_confirmed") is True:
        lines.append("You have confirmed the prohibited-filter list.")
    else:
        lines.append("The prohibited-filter list is not confirmed yet.")
    return lines


def _reach_sentences(document: Mapping[str, Any]) -> list[str]:
    """Exploration slots and sourcing, without saying how many.

    The slot count is a number and it belongs to the engine. What the client
    needs to know is the behaviour it buys them, which is whether people from
    outside the obvious shape reach their shortlist at all.
    """
    lines: list[str] = []
    try:
        slots = int(document.get("exploration_slots") or 0)
    except (TypeError, ValueError):
        slots = 0
    if slots > 0:
        lines.append(
            "We will keep room on every shortlist for people whose background "
            "does not look like the obvious one, because you told us you are "
            "open to them."
        )
    else:
        lines.append(
            "We will stay close to the conventional background for this kind of "
            "role, because that is what you told us works here."
        )
    if document.get("sourcing_hints"):
        lines.append(
            "Your sourcing preferences shape where we look and never who we "
            "assess. Everyone linked to a job is scored."
        )
    return lines


def _recruiter_context_lines(document: Mapping[str, Any]) -> list[str]:
    """Runbook §15's leftover bucket, shown and LABELLED as what it is.

    "If a client statement cannot be expressed as a weight modifier, an evidence
    requirement, a threshold, a disqualifier, a sourcing instruction, or a
    dossier presentation preference, it is context for the recruiter, not
    configuration for the engine, and it is labelled as such."

    The labelling is the part that has to be visible. A client who told us their
    real time-to-hire, or that counter offers arrive after signing, said
    something a recruiter needs and the engine must not act on, and stating that
    distinction back is how they can tell the two apart in what we understood.
    Their own words, so the numbers in them are theirs.
    """
    context = document.get("recruiter_context") or {}
    if not isinstance(context, Mapping):
        return ["Nothing was recorded here."]
    values = [
        str(value).strip()
        for key, value in sorted(context.items())
        if key != "note" and str(value or "").strip()
    ]
    return values or [
        "Everything you told us mapped onto a configuration the engine reads."
    ]


def _presentation_sentences(document: Mapping[str, Any]) -> list[str]:
    presentation = document.get("dossier_preferences") or {}
    lines: list[str] = []
    depth = presentation.get("depth")
    if depth:
        lines.append(f"Report depth: {depth}.")
    first_pass = presentation.get("first_pass")
    if first_pass:
        lines.append(f"First pass: {first_pass}.")
    language = presentation.get("language")
    if language:
        lines.append(f"Written in: {language}.")
    decider = presentation.get("decider")
    if decider:
        lines.append(f"Reports are written for: {decider}.")
    if not lines:
        lines.append("Your reports will use our standard shape.")
    return lines
