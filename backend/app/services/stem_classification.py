"""STEM / Non-STEM role classification engine (Master Directive Part 3).

Rule-based, deterministic, no model call. Part 3 §4 is explicit about why:
classification must complete in well under 200ms, must never block JD display,
and must produce the same answer for the same JD every time. Keyword and
phrase matching over the RAW AI-generated JD text is the whole mechanism.

THE CLIENT NEVER TOUCHES THIS. Part 3 Rule 2: the client does not choose,
see a toggle for, or influence the classification. It runs server-side at JD
generation (Rule 3), locks to the raw pre-edit text, and is stored on the Job
record (Rule 4). The only humans who can change it are Provider Portal admins,
and only before the first completed assessment (Rule 5).

MATCHING IS WHOLE-WORD / WHOLE-PHRASE, per §4.2, to keep 'engineering' inside
'financial engineering' from reading as an engineering discipline. §4.3's
counterweights are implemented as EXCLUSION phrases stripped from the text
before the signal pass, plus conditional signals (Power BI counts only with
DAX; Tableau only with calculated fields).

SCORING, AND ONE RECONCILED CONTRADICTION. The directive uses "confidence"
for two different things and its own acceptance checklist exposes the clash:

  * §4.4's bands are a STEM-NESS score: >= 0.80 auto-STEM, 0.50-0.79 STEM
    tentative, 0.30-0.49 Non-STEM tentative, < 0.30 auto-Non-STEM.
  * The acceptance checklist requires a plainly non-technical sales JD to
    classify "Non-STEM, confidence >= 0.80" — impossible if confidence IS the
    §4.4 band value, because a high value there means STEM.

Both are kept, as two fields. `stem_score` is the §4.4 band value and drives
the label and the review-queue flag. `confidence` is confidence IN THE LABEL
(`stem_score` for STEM, `1 - stem_score` for Non-STEM), which is what the
acceptance checklist measures and what `classification_confidence` stores.
The closing note of the directive asks for discrepancies to be flagged rather
than silently resolved: this one is flagged here and in the PR description.

The default fallback stands as written (§4.4): below 0.50 the role is
Non-STEM, because under-charging 1.0 credit is commercially safer than
over-charging 1.5 and disputing it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

log = logging.getLogger(__name__)

STEM = "STEM"
NON_STEM = "NON_STEM"

#: Part 5 §2.1 — credits deducted per completed ReadyPick Intelligence Report.
CREDIT_COST: dict[str, Decimal] = {
    STEM: Decimal("1.5"),
    NON_STEM: Decimal("1.0"),
}

#: §4.4 — the label boundary. At or above this stem-score the role is STEM.
STEM_THRESHOLD = 0.50
#: §4.4 / Part 3 §9 — the tentative band routed to the Classification Review
#: Queue: 0.30–0.79 in stem-score terms, either side of the boundary.
REVIEW_BAND = (0.30, 0.80)

# ── §4.3 counterweights: phrases REMOVED before the signal pass ─────────────
# Each names a technical-sounding phrase that is not genuinely STEM. Removing
# the phrase (rather than special-casing each signal) means 'financial
# engineering' can never feed the 'engineering' patterns below, whatever shape
# those take.
_EXCLUSION_PHRASES: tuple[str, ...] = (
    "financial engineering",
    "social engineering",
    "reverse engineering of business processes",
    "business process re-engineering",
    "business process reengineering",
    "team chemistry",
    "sales engineering culture",
)

# ── §4.2 signal dictionary ──────────────────────────────────────────────────
# Weights are the calibration that makes the acceptance checklist pass:
# three specific technologies ("Python, machine learning, TensorFlow") must
# reach 0.80, one stray generic term must not reach 0.50.
_STRONG = 0.30  # a specific technology, tool, technique or certification
_MEDIUM = 0.20  # a named discipline or technical concept
_WEAK = 0.10    # a field name that needs company to mean anything

# (signal label, weight, phrase). Phrases are matched case-insensitively as
# whole words; spaces match any whitespace/hyphen run.
_SIGNALS: tuple[tuple[str, float, str], ...] = (
    # Category A — programming and software development
    *[("lang:" + p, _STRONG, p) for p in (
        "python", "java", "javascript", "typescript", "c++", "c#", "golang",
        "rust", "kotlin", "swift", "matlab", "scala", "php", "ruby on rails",
        "sql", "nosql",
    )],
    *[("framework:" + p, _STRONG, p) for p in (
        "react", "angular", "vue", "django", "flask", "spring boot",
        "node.js", "nodejs", "tensorflow", "pytorch", "keras",
    )],
    *[("concept:" + p, _MEDIUM, p) for p in (
        "algorithms", "data structures", "system design", "api development",
        "microservices", "cloud architecture", "devops", "ci/cd",
        "containerisation", "containerization",
    )],
    *[("platform:" + p, _STRONG, p) for p in (
        "kubernetes", "docker", "aws", "gcp", "azure", "linux", "unix",
        "terraform", "ansible",
    )],
    ("concept:software development", _MEDIUM, "software development"),
    ("concept:software engineering", _MEDIUM, "software engineering"),

    # Category B — engineering disciplines
    *[("discipline:" + p, _MEDIUM, p) for p in (
        "mechanical engineering", "civil engineering", "electrical engineering",
        "electronics engineering", "chemical engineering",
        "aerospace engineering", "structural engineering",
        "process engineering", "manufacturing engineering",
        "industrial engineering", "quality engineering",
        "reliability engineering", "embedded systems",
    )],
    *[("tool:" + p, _STRONG, p) for p in (
        "autocad", "solidworks", "catia", "ansys", "fea", "cfd", "plc",
        "scada", "hmi",
    )],

    # Category C — data, analytics and AI
    *[("ai:" + p, _STRONG, p) for p in (
        "machine learning", "deep learning", "neural networks",
        "natural language processing", "computer vision",
        "reinforcement learning",
    )],
    *[("data:" + p, _STRONG, p) for p in (
        "data engineering", "data pipeline", "etl", "data warehouse", "spark",
        "hadoop", "kafka", "airflow",
    )],
    *[("stats:" + p, _MEDIUM, p) for p in (
        "statistical modelling", "statistical modeling", "statistical analysis",
        "quantitative analysis", "regression analysis", "clustering",
        "a/b testing", "hypothesis testing",
    )],

    # Category D — science and research
    *[("lab:" + p, _STRONG, p) for p in (
        "clinical trials", "cell culture", "pcr", "spectroscopy",
        "chromatography", "genomics", "proteomics",
    )],
    *[("research:" + p, _MEDIUM, p) for p in (
        "research methodology", "experimental design", "peer review",
        "scientific writing", "laboratory",
    )],
    *[("science:" + p, _WEAK, p) for p in (
        "physics", "chemistry", "biology", "materials science",
        "environmental science", "pharmaceutical",
    )],

    # Category E — technical certifications and standards
    *[("cert:" + p, _STRONG, p) for p in (
        "aws certified", "google cloud professional", "microsoft azure",
        "cisco ccna", "cisco ccnp", "ccna", "ccnp",
    )],
    *[("standard:" + p, _WEAK, p) for p in ("iec", "ieee", "asme")],
)

#: Engineering-discipline TITLES ("Mechanical Design Engineer" carries the
#: discipline even when the JD body never writes "mechanical engineering").
_TITLE_PATTERN = re.compile(
    r"\b(mechanical|civil|electrical|electronics|chemical|aerospace|"
    r"structural|process|manufacturing|industrial|quality|reliability|"
    r"software|hardware|firmware|embedded|data|machine\s+learning|ml|ai|"
    r"devops|platform|site\s+reliability|network|security|robotics)"
    r"(\s+\w+){0,2}\s+(engineer|scientist|developer|architect)\b",
    re.IGNORECASE,
)

#: §4.3 conditional signals: BI tools count only in a technical pairing.
_CONDITIONAL: tuple[tuple[str, float, str, str], ...] = (
    ("bi:power bi + dax", _STRONG, r"\bpower\s+bi\b", r"\bdax\b"),
    ("bi:tableau + calculated fields", _STRONG, r"\btableau\b", r"\bcalculated\s+field"),
)


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    """Whole-word/phrase matcher. `c++`/`c#`/`node.js` keep their symbols;
    internal spaces match any whitespace or hyphen run."""
    escaped = re.escape(phrase).replace(r"\ ", r"[\s\-]+")
    lead = r"(?<![A-Za-z0-9])"
    tail = r"(?![A-Za-z0-9+#])" if phrase[-1].isalnum() else r"(?![+#])"
    return re.compile(lead + escaped + tail, re.IGNORECASE)


_COMPILED: tuple[tuple[str, float, re.Pattern[str]], ...] = tuple(
    (label, weight, _phrase_regex(phrase)) for label, weight, phrase in _SIGNALS
)


@dataclass(frozen=True)
class ClassificationResult:
    classification: str          # STEM | NON_STEM
    confidence: float            # confidence in the LABEL, 0.00–1.00
    stem_score: float            # the §4.4 band value the label came from
    signals: list[str] = field(default_factory=list)
    tentative: bool = False      # inside the §4.4 review band → review queue
    engine_error: bool = False   # §8 fallback path was taken

    @property
    def credit_cost_per_report(self) -> Decimal:
        return CREDIT_COST[self.classification]


def classify(raw_jd_text: str, job_title: str = "") -> ClassificationResult:
    """Classify one raw AI-generated JD. Deterministic, sub-millisecond."""
    text = f"{job_title}\n{raw_jd_text or ''}".lower()
    for phrase in _EXCLUSION_PHRASES:
        text = _phrase_regex(phrase).sub(" ", text)

    signals: list[str] = []
    score = 0.0
    for label, weight, pattern in _COMPILED:
        if pattern.search(text):
            signals.append(label)
            score += weight

    if _TITLE_PATTERN.search(job_title or "") or _TITLE_PATTERN.search(text):
        signals.append("title:engineering-discipline")
        score += _STRONG

    for label, weight, first, second in _CONDITIONAL:
        if re.search(first, text) and re.search(second, text):
            signals.append(label)
            score += weight

    stem_score = min(1.0, round(score, 2))
    classification = STEM if stem_score >= STEM_THRESHOLD else NON_STEM
    confidence = stem_score if classification == STEM else round(1.0 - stem_score, 2)
    tentative = REVIEW_BAND[0] <= stem_score < REVIEW_BAND[1]
    return ClassificationResult(
        classification=classification,
        confidence=confidence,
        stem_score=stem_score,
        signals=signals,
        tentative=tentative,
    )


def classify_safe(raw_jd_text: str, job_title: str = "") -> ClassificationResult:
    """§8 row one: an engine error NEVER blocks job creation. Default to
    Non-STEM, log loudly, flag for manual review; the client stays unaware."""
    try:
        return classify(raw_jd_text, job_title)
    except Exception:  # noqa: BLE001 — the whole point is 'whatever happens'
        log.exception("stem_classification.engine_error title=%r", job_title)
        return ClassificationResult(
            classification=NON_STEM,
            confidence=0.0,
            stem_score=0.0,
            signals=["classification_engine_error"],
            tentative=True,
            engine_error=True,
        )


def credit_cost(classification: str | None) -> Decimal:
    """Part 5 Rule 9: NULL/unknown classification defaults to Non-STEM (1.0)
    and is a logged data error, never a hard failure at deduction time."""
    if classification not in CREDIT_COST:
        if classification is not None:
            log.error("stem_classification.unknown_value %r", classification)
        return CREDIT_COST[NON_STEM]
    return CREDIT_COST[classification]
