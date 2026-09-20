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

TWO LAYERS, ADDED 2026-09-13: THE BODY, AND THE OCCUPATION
-----------------------------------------------------------
Everything above describes the BODY pass, and for a rich JD it was right. It
was badly wrong for a thin one, in one direction only: a job whose title said
"Software Engineer", "Data Scientist" or "Electronics Engineer" and whose
description was two paragraphs scored 0.30 and was billed, assessed and
reported as Non-STEM. The body pass can say "this text mentions technology";
it cannot say "this is an engineering occupation", and the occupation is what
the classification is for.

So a second layer reads the TITLE for the occupational function it names
(`classify_occupation`), and applies it as a prior on the body score: a
recognised STEM occupation raises the floor to `TITLE_STEM_FLOOR`, a
recognised Non-STEM one caps it at `TITLE_NON_STEM_CEILING`, and an
unrecognised one changes nothing. Both layers are still deterministic, still
rule-based, and still well inside the 200ms budget. Neither replaces the
other, and `ClassificationResult.body_score` keeps what each contributed
visible to a reviewer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

log = logging.getLogger(__name__)

STEM = "STEM"
NON_STEM = "NON_STEM"

#: Part 5 §2.1 — credits deducted per completed Vivekium Intelligence Report.
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
    #: What the title said the OCCUPATION was: "stem", "non_stem", or None
    #: when the taxonomy did not recognise it. Separate from `signals`, which
    #: stays what it has always been: the technical signals found in the BODY.
    occupation: str | None = None
    #: Which vocabulary entry produced `occupation`, for the audit trail.
    occupation_basis: str | None = None
    #: The body score before the occupational floor or ceiling was applied.
    #: Kept so a reviewer can see what the two layers each contributed.
    body_score: float = 0.0

    @property
    def credit_cost_per_report(self) -> Decimal:
        return CREDIT_COST[self.classification]

    @property
    def explanation(self) -> list[str]:
        """`signals` plus the occupational verdict: what gets PERSISTED.

        The two are separate fields in memory because the body signals are a
        stable contract several modules read, and one merged list where the
        caller has to know which entries are which would be worse for both.
        They are merged here, once, at the boundary where the row is written.
        """
        entries = list(self.signals)
        if self.occupation:
            entries.append(f"occupation:{self.occupation}:{self.occupation_basis}")
        return entries


# ═══════════════════════════════════════════════════════════════════════════
# THE OCCUPATIONAL LAYER (§19, §21: classify the FUNCTION, not the keyword)
# ═══════════════════════════════════════════════════════════════════════════
#
# WHY THIS LAYER EXISTS
# ---------------------
# The signal pass above reads the JD BODY. It is calibrated so that three
# specific technologies reach 0.80 and one stray generic term does not reach
# 0.50, and for a rich JD it is right. For a THIN one it was catastrophically
# wrong, and the failure was not a tuning problem:
#
#     Software Engineer   -> 0.30 -> Non-STEM
#     Data Scientist      -> 0.30 -> Non-STEM
#     Electronics Engineer-> 0.30 -> Non-STEM
#     Research Scientist  -> 0.00 -> Non-STEM
#     Cloud Engineer      -> 0.00 -> Non-STEM
#
# A body-only engine can only ever say "this text mentions technology". It
# cannot say "this is an engineering occupation", and the occupation is what
# the classification is FOR. So the title is parsed for the occupational
# function it names, and that verdict is a PRIOR on the body score rather than
# a competing score: an occupation the taxonomy recognises as STEM raises the
# floor, one it recognises as Non-STEM imposes a ceiling, and an occupation it
# does not recognise changes nothing at all.
#
# NOT A LIST OF JOB TITLES (§21)
# ------------------------------
# Three small vocabularies compose: a HEAD NOUN (what kind of worker), a set
# of DOMAIN qualifiers (what field they work in), and a set of seniority words
# that are stripped because "Senior" tells you nothing about the occupation.
# "Principal Embedded Firmware Engineer" is not in any list here; it resolves
# because `embedded`/`firmware` are STEM domains and `engineer` is a STEM head
# noun. The same three vocabularies classify a title nobody has seen yet,
# which is what "generalize to equivalent roles" has to mean.
#
# THE DOMAIN BEATS THE HEAD NOUN, ALWAYS (§19)
# --------------------------------------------
# "Sales Engineer", "HR Manager", "Finance Manager" and "Business Development
# Manager" are Non-STEM whatever their head noun suggests, because the domain
# is the occupation and the head noun is only its shape. So a recognised
# Non-STEM domain decides the title outright, before the head noun is read.
#
# ANALYST AND MANAGER ARE DELIBERATELY NOT DECISIVE
# -------------------------------------------------
# §19 names both: not every "Analyst" is STEM, and not every "Manager" is
# Non-STEM. Neither head noun can carry a title on its own, so both are
# QUALIFIED nouns: they resolve to STEM only when a STEM domain qualifies
# them ("Software Engineering Manager", "Quantitative Analyst") and otherwise
# hand the decision back to the body signals, where "Data Analyst" attached to
# a marketing-reporting JD correctly stays Non-STEM.

#: A recognised STEM occupation puts the stem-score AT LEAST here. Above the
#: §4.4 auto-STEM band (0.80) on purpose: the occupation is not tentative
#: evidence, it is the thing being classified, and routing every thin
#: "Software Engineer" into the Provider's review queue would bury the queue
#: in exactly the rows that need no human.
TITLE_STEM_FLOOR = 0.85

#: A recognised Non-STEM occupation puts the stem-score AT MOST here. Below
#: the 0.50 label boundary (so the label is Non-STEM) and inside the
#: 0.30-0.79 review band (so a Non-STEM occupation whose JD is full of
#: technology reaches a human instead of being silently resolved either way).
#: A Non-STEM occupation with a non-technical body scores near zero and is
#: never tentative, which is what keeps the queue readable.
TITLE_NON_STEM_CEILING = 0.45

#: Stripped before the title is parsed. Seniority, rank and employment shape
#: say nothing about the occupation.
_TITLE_NOISE: frozenset[str] = frozenset({
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "chief",
    "head", "deputy", "associate", "assistant", "trainee", "intern",
    "graduate", "apprentice", "entry", "level", "mid", "i", "ii", "iii", "iv",
    "of", "the", "and", "for", "in", "to", "a", "an", "at",
    "vp", "svp", "evp", "avp", "president", "vice",
    "full", "part", "time", "contract", "freelance", "remote", "onsite",
    "global", "regional", "national", "corporate", "group", "team",
    "1", "2", "3", "4", "i.",
})

#: Multi-word domains, matched BEFORE the title is split into tokens, so a
#: phrase is never torn into misleading halves. "business intelligence" is the
#: reason this exists: split, its first token would read as the Non-STEM
#: "business" domain and take a genuinely data-engineering role with it.
_STEM_DOMAIN_PHRASES: tuple[str, ...] = (
    "business intelligence", "machine learning", "deep learning",
    "artificial intelligence", "computer vision", "natural language",
    "data science", "data engineering", "data platform", "site reliability",
    "control systems", "power systems", "signal processing",
    "information security", "information technology", "quality assurance",
    "test automation", "full stack", "front end", "back end", "web3",
    "supply chain analytics", "operations research", "research and development",
    "product security", "cloud infrastructure", "computer science",
)

_NON_STEM_DOMAIN_PHRASES: tuple[str, ...] = (
    "business development", "human resources", "talent acquisition",
    "people operations", "public relations", "customer success",
    "customer support", "customer service", "client servicing",
    "account management", "inside sales", "field sales", "pre sales",
    "presales", "post sales", "corporate communications", "media relations",
    "investor relations", "content marketing", "brand marketing",
    "digital marketing", "growth marketing", "social media",
    "learning and development", "office administration", "front office",
    "back office", "general administration", "facility management",
    "real estate", "event management", "community management",
)

#: Single-token domains. STEM first: a field whose practice IS science,
#: technology, engineering or mathematics.
_STEM_DOMAINS: frozenset[str] = frozenset({
    "software", "hardware", "firmware", "embedded", "systems", "system",
    "platform", "infrastructure", "devops", "sre", "cloud", "network",
    "networking", "security", "cybersecurity", "cyber", "database", "data",
    "backend", "frontend", "fullstack", "web", "mobile", "android", "ios",
    "api", "microservices", "blockchain", "cryptography", "quantum",
    "ai", "ml", "nlp", "algorithms", "computational", "computing", "computer",
    "mechanical", "electrical", "electronics", "electronic", "civil",
    "chemical", "structural", "aerospace", "aeronautical", "automotive",
    "biomedical", "biotech", "biotechnology", "bioinformatics", "genomics",
    "robotics", "mechatronics", "instrumentation", "metallurgical",
    "metallurgy", "mining", "petroleum", "geotechnical", "geospatial",
    "environmental", "nuclear", "optical", "photonics", "semiconductor",
    "vlsi", "asic", "fpga", "rf", "microwave", "telecom", "telecommunications",
    "wireless", "avionics", "hydraulic", "thermal", "acoustics",
    "engineering", "manufacturing", "production", "process", "industrial",
    "reliability",
    "validation", "verification", "automation", "controls", "plc", "scada",
    "physics", "chemistry", "biology", "biological", "molecular", "clinical",
    "pharmaceutical", "pharmacology", "microbiology", "biochemistry",
    "materials", "geology", "geophysics", "astronomy", "climate",
    "mathematics", "mathematical", "statistics", "statistical", "quantitative",
    "actuarial", "econometrics", "cryptographic", "simulation", "modelling",
    "modeling", "analytics",
})

#: Single-token domains whose practice is not STEM. A title carrying one of
#: these is Non-STEM whatever its head noun (§19).
_NON_STEM_DOMAINS: frozenset[str] = frozenset({
    "sales", "selling", "revenue", "business", "commercial", "account",
    "accounts", "client", "clients", "customer", "partnership",
    "partnerships", "channel", "distribution", "retail", "merchandising",
    "category", "procurement", "purchasing", "sourcing", "vendor",
    "hr", "human", "people", "talent", "recruitment", "recruiting",
    "staffing", "payroll", "compensation", "benefits", "workforce",
    "finance", "financial", "accounting", "audit", "auditing", "tax",
    "taxation", "treasury", "credit", "collections", "billing", "invoicing",
    "marketing", "brand", "branding", "advertising", "communications",
    "communication", "pr", "publicity", "copywriting", "editorial",
    "content", "creative", "campaign",
    "legal", "compliance", "governance", "regulatory", "paralegal",
    "contracts", "secretarial",
    "admin", "administration", "administrative", "office", "clerical",
    "reception", "facilities", "housekeeping", "hospitality", "travel",
    "logistics", "warehouse", "dispatch", "fleet", "transport",
    "training", "learning", "onboarding", "culture", "engagement",
    "strategy", "consulting", "advisory", "transformation", "change",
    "insurance", "underwriting", "claims", "mortgage", "wealth", "banking",
    "property", "leasing", "estate", "tourism", "catering", "fashion",
    "sports", "entertainment", "publishing", "translation", "counselling",
    "counseling", "welfare", "social", "csr", "fundraising", "membership",
})

#: Head nouns whose occupation is STEM on its own. "Engineer" with no
#: qualifier is an engineer; "Scientist" with no qualifier is a scientist.
_STEM_HEAD_NOUNS: frozenset[str] = frozenset({
    "engineer", "engineers", "engineering", "developer", "developers",
    "programmer", "programmers", "coder", "scientist", "scientists",
    "technologist", "mathematician", "statistician", "actuary",
    "cryptographer", "biologist", "chemist", "physicist", "geologist",
    "biochemist", "microbiologist", "pharmacologist", "epidemiologist",
    "toxicologist", "radiologist", "astronomer", "meteorologist",
    "ecologist", "zoologist", "botanist", "virologist", "immunologist",
    "neuroscientist", "agronomist", "metallurgist", "seismologist",
    "draughtsman", "draftsman", "machinist", "electrician", "welder",
    "surveyor", "pathologist", "sysadmin", "devops",
})

#: Head nouns whose occupation is not STEM on its own.
_NON_STEM_HEAD_NOUNS: frozenset[str] = frozenset({
    "recruiter", "recruiters", "accountant", "accountants", "bookkeeper",
    "salesperson", "salesman", "saleswoman", "marketer", "merchandiser",
    "buyer", "broker", "agent", "agents", "realtor", "underwriter",
    "lawyer", "attorney", "solicitor", "paralegal", "notary",
    "receptionist", "secretary", "clerk", "typist", "steward",
    "copywriter", "editor", "journalist", "publicist", "storyteller",
    "counsellor", "counselor", "trainer", "facilitator", "coach",
    "cashier", "teller", "bursar", "registrar", "librarian",
    "chef", "waiter", "concierge", "housekeeper", "driver", "courier",
    "generalist", "partner", "evangelist", "ambassador", "representative",
    "rep", "closer", "hunter", "farmer",
})

#: Head nouns that name a SHAPE of job rather than an occupation. They carry a
#: title only when a domain qualifies them; alone they decide nothing (§19).
_QUALIFIED_HEAD_NOUNS: frozenset[str] = frozenset({
    "analyst", "analysts", "manager", "managers", "director", "directors",
    "officer", "officers", "executive", "executives", "specialist",
    "specialists", "consultant", "consultants", "coordinator", "lead",
    "leader", "architect", "architects", "administrator", "administrators",
    "technician", "technicians", "researcher", "associate", "advisor",
    "adviser", "supervisor", "planner", "strategist", "owner", "expert",
    "professional", "staff", "operator", "operative", "assistant", "intern",
    "trainee", "apprentice", "designer", "writer", "auditor", "inspector",
    "controller", "principal", "head", "chief", "vp", "president",
})

#: §19 names "Analyst" as the word that must not classify a role on its own,
#: and the product already had the case that proves it: a Data Analyst writing
#: weekly marketing reports is Non-STEM. So `analyst` is the one head noun a
#: mere FIELD qualifier cannot carry. It resolves to STEM only under a
#: qualifier whose own practice is mathematical, which is the distinction §17
#: draws when it admits the Quantitative Analyst "where the role is
#: substantially mathematical/quantitative". Every other Analyst goes to the
#: body signals, where a genuinely technical JD still reaches STEM on its own.
_QUANTITATIVE_DOMAINS: frozenset[str] = frozenset({
    "quantitative", "quant", "mathematics", "mathematical", "statistics",
    "statistical", "actuarial", "econometrics", "computational",
    "algorithms", "cryptographic", "bioinformatics", "operations_research",
})

#: The head nouns that require `_QUANTITATIVE_DOMAINS` rather than any STEM
#: domain. A set rather than a special case, so a second such noun is one line.
_QUANTITATIVE_ONLY_HEAD_NOUNS: frozenset[str] = frozenset({"analyst", "analysts"})

#: `-ologist` and `-metrician` name a science by construction. A suffix rule
#: rather than a list, so `hydrologist` and `paleobotanist` resolve without
#: anybody having added them.
_SCIENTIFIC_SUFFIXES: tuple[str, ...] = ("ologist", "ometrician", "ographer")

#: Occupations whose `-ologist`-shaped name is not a laboratory or field
#: science in a HIRING context. Kept short and explicit, because the suffix
#: rule is otherwise the right generalisation.
_SUFFIX_EXCEPTIONS: frozenset[str] = frozenset({
    "technologist",  # already a STEM head noun; listed so the rule is total
})

_STEM_OCCUPATION = "stem"
_NON_STEM_OCCUPATION = "non_stem"

_TOKEN_SPLIT = re.compile(r"[^a-z0-9+#.]+")


def _non_stem_phrase(title: str) -> str | None:
    """The first Non-STEM domain PHRASE in the title, if any."""
    for phrase in _NON_STEM_DOMAIN_PHRASES:
        if _phrase_regex(phrase).search(title):
            return phrase
    return None


def _extract_stem_phrases(title: str) -> tuple[str, list[str]]:
    """Pull every STEM domain phrase out of the title.

    The phrases are REMOVED from the string they were found in, and that
    removal is the whole point rather than a tidy-up: left in place, "machine
    learning" would be re-read token by token and its `learning` would match
    the Non-STEM training-and-development domain, turning an ML Engineer into
    an L&D hire. "business intelligence" fails the same way through
    `business`. A phrase is one word for classification purposes, so it stops
    being two words before anything counts words.
    """
    found: list[str] = []
    remaining = title
    for phrase in _STEM_DOMAIN_PHRASES:
        pattern = _phrase_regex(phrase)
        if pattern.search(remaining):
            found.append(phrase.replace(" ", "_"))
            remaining = pattern.sub(" ", remaining)
    return remaining, found


def _is_scientific_suffix(token: str) -> bool:
    if token in _SUFFIX_EXCEPTIONS:
        return False
    return any(token.endswith(suffix) for suffix in _SCIENTIFIC_SUFFIXES)


@dataclass(frozen=True)
class OccupationVerdict:
    """What occupational function the title names, and on what basis.

    `verdict` is None when the taxonomy does not recognise the occupation. That
    is the common case for a title like "Strategy Associate" and it is not a
    failure: an unrecognised occupation leaves the body signals to decide,
    which is exactly the behaviour this engine had before the layer existed.
    """

    verdict: str | None
    basis: str

    @property
    def signal(self) -> str | None:
        if self.verdict is None:
            return None
        return f"occupation:{self.verdict}:{self.basis}"


def classify_occupation(job_title: str) -> OccupationVerdict:
    """Read the occupational function out of a job title.

    Order is the rule, not an optimisation:
      1. a recognised Non-STEM domain decides outright (§19: an HR Manager is
         Non-STEM however technical the department around them is),
      2. then a recognised STEM domain with a head noun that accepts one,
      3. then a head noun that is an occupation on its own,
      4. otherwise: no verdict, and the body decides.
    """
    title = (job_title or "").strip().lower()
    if not title:
        return OccupationVerdict(None, "")

    non_stem_phrase = _non_stem_phrase(title)
    if non_stem_phrase is not None:
        return OccupationVerdict(
            _NON_STEM_OCCUPATION, non_stem_phrase.replace(" ", "_")
        )

    remaining, stem_phrases = _extract_stem_phrases(title)

    tokens = [t for t in _TOKEN_SPLIT.split(remaining) if t and t not in _TITLE_NOISE]
    if not tokens and not stem_phrases:
        return OccupationVerdict(None, "")

    for token in tokens:
        if token in _NON_STEM_DOMAINS:
            return OccupationVerdict(_NON_STEM_OCCUPATION, token)

    # The head noun is the LAST token the taxonomy recognises as one. Last,
    # because English puts it there: in "Data Platform Engineer" the engineer
    # is the occupation and the data platform is the field.
    head = ""
    for token in reversed(tokens):
        if (
            token in _STEM_HEAD_NOUNS
            or token in _NON_STEM_HEAD_NOUNS
            or token in _QUALIFIED_HEAD_NOUNS
            or _is_scientific_suffix(token)
        ):
            head = token
            break

    qualifiers = [t for t in tokens if t != head] + stem_phrases
    stem_qualifier = next((q for q in qualifiers if q in _STEM_DOMAINS), None)
    if stem_qualifier is None and stem_phrases:
        stem_qualifier = stem_phrases[0]
    quantitative_qualifier = next(
        (q for q in qualifiers if q in _QUANTITATIVE_DOMAINS), None
    )

    if head in _QUANTITATIVE_ONLY_HEAD_NOUNS:
        if quantitative_qualifier:
            return OccupationVerdict(
                _STEM_OCCUPATION, f"{quantitative_qualifier}_{head}"
            )
        return OccupationVerdict(None, "")

    if head and (head in _STEM_HEAD_NOUNS or _is_scientific_suffix(head)):
        basis = f"{stem_qualifier}_{head}" if stem_qualifier else head
        return OccupationVerdict(_STEM_OCCUPATION, basis)

    if head in _QUALIFIED_HEAD_NOUNS and stem_qualifier:
        return OccupationVerdict(_STEM_OCCUPATION, f"{stem_qualifier}_{head}")

    if head in _NON_STEM_HEAD_NOUNS:
        return OccupationVerdict(_NON_STEM_OCCUPATION, head)

    # A bare STEM domain with no head noun at all ("Data Science", "VLSI").
    if not head and stem_qualifier:
        return OccupationVerdict(_STEM_OCCUPATION, stem_qualifier)

    return OccupationVerdict(None, "")


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

    body_score = min(1.0, round(score, 2))

    # The occupational layer. A recognised occupation is a FLOOR or a CEILING
    # on the body score, never a replacement for it: a Software Engineer whose
    # JD is thin still classifies STEM, and one whose JD is rich still scores
    # above the floor and reads the same.
    occupation = classify_occupation(job_title)
    if occupation.verdict == _STEM_OCCUPATION:
        stem_score = max(body_score, TITLE_STEM_FLOOR)
    elif occupation.verdict == _NON_STEM_OCCUPATION:
        stem_score = min(body_score, TITLE_NON_STEM_CEILING)
    else:
        stem_score = body_score

    stem_score = round(stem_score, 2)
    classification = STEM if stem_score >= STEM_THRESHOLD else NON_STEM
    confidence = stem_score if classification == STEM else round(1.0 - stem_score, 2)
    tentative = REVIEW_BAND[0] <= stem_score < REVIEW_BAND[1]
    return ClassificationResult(
        classification=classification,
        confidence=confidence,
        stem_score=stem_score,
        signals=signals,
        tentative=tentative,
        occupation=occupation.verdict,
        occupation_basis=occupation.basis or None,
        body_score=body_score,
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
