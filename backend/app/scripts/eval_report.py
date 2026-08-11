"""Offline evaluation of the report and matching agents.

    python -m app.scripts.eval_report

WHY A SECOND EVAL
-----------------
`eval_interview` measures the agent that TALKS to a candidate. This measures
the two paths whose bad output is most expensive and least visible:

  * **AI matching**, which decides the order a recruiter reads applicants in.
    Nobody sees a wrong ordering; they see a shortlist and assume it is right.
  * **The PPI Assessment Report**, which is the product's deliverable. A remark
    that is 38 words instead of 45-50, a stray number, or a borrowed
    third-party instrument name is a defect a client reads before we do.

The interview eval already proves the agent behaves sanely with whatever a
model returns. This one proves the SHAPE of what gets persisted is right, in
every branch, including the ones only an outage reaches.

SAME RULES AS THE INTERVIEW EVAL
--------------------------------
Fully offline and deterministic. No API key, no network, no sampling. A rate
that moves means the CODE changed. And the same limitation applies, stated
plainly: this cannot judge whether a remark is INSIGHTFUL. It judges whether it
is a legal remark. Those are different questions and only the second one can be
defended by CI.

The thresholds live in `tests/test_report_eval.py` and are set where the
product is TODAY. A rate allowed to fall silently is a rate nobody is
defending.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from app.services import functional_assessment as fa
from app.services import matching, ppi
from app.services.rating import (
    GRADE_HIGHLY,
    GRADE_MATCHING,
    GRADE_MODERATELY,
    GRADE_NOT,
    GRADES,
    band_index_for,
    grade_for_percent,
)

# ── The labelled sets ────────────────────────────────────────────────────────

#: Third-party instruments PPI must never be associated with, in code, prompts,
#: UI copy or generated text (claude.md, and the client's own instruction that
#: PPI is proprietary). Written as separate strings rather than one regex so a
#: failure names the instrument that leaked.
BANNED_INSTRUMENTS: tuple[str, ...] = (
    "DISC",
    "MBTI",
    "Myers-Briggs",
    "Hogan",
    "CliftonStrengths",
    "Clifton Strengths",
    "StrengthsFinder",
    "Big Five",
    "OCEAN",
    "Enneagram",
)

#: (internal score, expected grade). The boundaries are the interesting rows:
#: cut-points are inclusive UPWARD (claude.md rule 8), so exactly 90 is Highly
#: Matching and 89 is not. Every one of these has been a bug in some product.
MATCHING_LABELS: tuple[tuple[float, str], ...] = (
    (100, GRADE_HIGHLY),
    (95, GRADE_HIGHLY),
    (90, GRADE_HIGHLY),      # boundary, inclusive upward
    (89.9, GRADE_MATCHING),
    (80, GRADE_MATCHING),
    (75, GRADE_MATCHING),    # boundary
    (74.9, GRADE_MODERATELY),
    (65, GRADE_MODERATELY),
    (60, GRADE_MODERATELY),  # boundary
    (59.9, GRADE_NOT),
    (25, GRADE_NOT),
    (0, GRADE_NOT),
)

#: Ranking cases. Each is (label, parameter scores, expected position order).
#: These are the "labelled examples" the matching agent is measured against:
#: the property that matters is not the absolute number, which never leaves the
#: server, but that a stronger candidate outranks a weaker one on the same job.
def _params(skills: int, experience: int, role: int, education: int) -> dict[str, int]:
    """Build a breakdown using the REAL parameter keys.

    Written as a helper rather than four literal dicts so a rename of
    `matching.PARAMETERS` fails in one place instead of five.
    """
    return dict(
        zip(
            matching.PARAMETERS,
            (skills, experience, role, education),
        )
    )


RANKING_CASES: tuple[tuple[str, tuple[int, int, int, int]], ...] = (
    ("strong all round", (94, 91, 90, 88)),
    ("strong skills, thin experience", (92, 61, 78, 80)),
    ("even mid", (72, 70, 74, 71)),
    ("weak skills", (44, 70, 55, 82)),
    ("weak all round", (31, 28, 35, 40)),
)

#: Competency names a generator might return that are culture-fit by another
#: name. All must be refused: cultural fit cannot be assessed accurately from a
#: single assessment and PPI does not claim otherwise.
CULTURE_NAMES: tuple[str, ...] = (
    "Culture",
    "culture fit",
    "Cultural Fit",
    "CULTURE ALIGNMENT",
    "Company culture",
)

#: Legitimate competencies that merely resemble the forbidden one. The hard
#: part is the DISTINCTION, not the detection: a guard that rejects a real
#: competency fails invisibly, one job setup at a time.
NOT_CULTURE_NAMES: tuple[str, ...] = (
    "Stakeholder influence",
    "Agricultural domain knowledge",
    "Coaching and development",
    "Customer empathy",
)


@dataclass
class Result:
    """One measured behaviour, with the cases that failed kept for reading."""

    name: str
    passed: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def record(self, ok: bool, case: str) -> None:
        self.total += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(case)


# ── Helpers ──────────────────────────────────────────────────────────────────

#: A bare number in client-facing prose. Deliberately NOT a plain `\d` sweep:
#: "How did you bring p99 latency under 200ms?" is ordinary technical content
#: and mangling it would be a worse failure than the one being prevented. What
#: is forbidden is a SCORE shape -- a percentage, an x/y, a "band 3".
SCORE_SHAPED = re.compile(
    r"(\b\d{1,3}\s*%|\b\d{1,3}\s*/\s*\d{1,3}\b|\bband\s+\d\b|\bscore\s+of\s+\d+|\b\d+\s*out\s+of\s+\d+\b)",
    re.IGNORECASE,
)


def _banned_in(text: str) -> str | None:
    lowered = (text or "").lower()
    for instrument in BANNED_INSTRUMENTS:
        # Word-boundary match so "discuss" does not read as "DISC" and
        # "oceanic" does not read as "OCEAN".
        if re.search(rf"\b{re.escape(instrument.lower())}\b", lowered):
            return instrument
    return None


# ── Measurements: matching ───────────────────────────────────────────────────

def _measure_grade_boundaries() -> Result:
    """Every cut-point, from both sides. Inclusive upward (claude.md rule 8)."""
    result = Result("grade_boundaries")
    for score, expected in MATCHING_LABELS:
        actual = grade_for_percent(score)
        result.record(actual == expected, f"{score} -> {actual!r}, expected {expected!r}")
    return result


def _measure_matching_label_is_the_one_scale() -> Result:
    """`matching.matching_label` and `functional_assessment.rating_label` are
    thin aliases over `services/rating` and must stay that way. The product
    used to carry two parallel five-label scales kept in step by hand.

    Note the input SCALES differ and that is not an inconsistency to fix here:
    a matching PARAMETER is stored 1-10 and everything else is 0-100. Writing
    this measurement the first time got that wrong and reported the code as
    broken, which is the same mistake a caller makes -- so both are asserted
    explicitly, and the agreement between them is asserted as well.
    """
    result = Result("one_rating_scale")
    for score, expected in MATCHING_LABELS:
        result.record(
            fa.rating_label(score) == expected,
            f"rating_label({score}) = {fa.rating_label(score)!r}, expected {expected!r}",
        )
        # The same grade, reached from the 1-10 side.
        ten = score / 10.0
        result.record(
            matching.matching_label(ten) == expected,
            f"matching_label({ten}) = {matching.matching_label(ten)!r}, expected {expected!r}",
        )
    # None in, None out, on both. A missing score must not become a grade.
    result.record(matching.matching_label(None) is None, "matching_label(None) is not None")
    result.record(fa.rating_label(None) is None, "rating_label(None) is not None")
    # A boolean is not a score. `isinstance(True, int)` is True in Python, so
    # without the explicit guard `True` would grade Not Matching.
    result.record(fa.rating_label(True) is None, "rating_label(True) produced a grade")
    return result


def _measure_ranking_order() -> Result:
    """A stronger profile outranks a weaker one, on the same job.

    This is the matching agent's only externally observable promise: the
    absolute score never leaves the server, so ORDER is the whole product. The
    cases are ordered strongest-first in RANKING_CASES, and every adjacent
    pair must come out that way.
    """
    result = Result("ranking_order")
    scored = [
        (label, matching.compute_overall_score(_params(*scores)))
        for label, scores in RANKING_CASES
    ]
    for (left_label, left), (right_label, right) in zip(scored, scored[1:]):
        result.record(
            left > right,
            f"{left_label} ({left:.1f}) did not outrank {right_label} ({right:.1f})",
        )
    return result


def _measure_no_weightage_table() -> Result:
    """The 0.35/0.30/0.20/0.15 weighting is gone and must not come back.

    Two things were wrong with it: it was SHOWN to clients as "35% role-fit
    weighting", which is a number reaching a client, and it asserted that
    skills matter 2.3x more than education for every role in the product.
    """
    result = Result("no_weightage_table")
    result.record(not hasattr(matching, "WEIGHTS"), "matching.WEIGHTS exists again")
    # The plain mean, verified rather than assumed: an equal-weight mean of
    # four equal scores is that score.
    even = matching.compute_overall_score(_params(80, 80, 80, 80))
    result.record(abs(even - 80) < 0.01, f"even scores averaged to {even}")
    # And moving any ONE parameter by the same amount must move the overall by
    # the same amount, whichever parameter it was. That is what "no weighting"
    # means, stated as a measurement.
    base = matching.compute_overall_score(_params(70, 70, 70, 70))
    deltas = []
    for key in matching.PARAMETERS:
        scores = {name: 70 for name in matching.PARAMETERS}
        scores[key] = 90
        deltas.append(matching.compute_overall_score(scores) - base)
    result.record(
        max(deltas) - min(deltas) < 0.01,
        f"parameters are not equally weighted: deltas {deltas}",
    )
    return result


def _measure_comment_word_range() -> Result:
    """Matching remarks are 25-30 words, enforced rather than hoped for.

    Checked from BOTH directions, because the enforcement rewrites text: a
    too-short remark must be padded to a real sentence, and a too-long one
    trimmed without being cut mid-sentence.
    """
    result = Result("matching_remark_words")
    cases = [
        "Short.",
        "Strong match.",
        " ".join(["evidence"] * 12),
        " ".join(["evidence"] * 27),
        " ".join(["evidence"] * 60),
        "",
    ]
    for text in cases:
        out = matching.enforce_word_range(text, *fa.MATCHING_REMARK_WORDS)
        count = matching.word_count(out)
        low, high = fa.MATCHING_REMARK_WORDS
        result.record(
            low <= count <= high,
            f"{count} words from input of {matching.word_count(text)}: {out[:60]!r}",
        )
    return result


# ── Measurements: the report ─────────────────────────────────────────────────

def _measure_ppi_remark_word_range() -> Result:
    """PPI items and the overall remark are 45-50 words, in every branch.

    Including the fallbacks, which is the point: the fallback is what a client
    reads when a provider is down, and a 12-word apology in the Behavioural
    Competencies section is the most visible possible failure.
    """
    result = Result("ppi_remark_words")
    low, high = fa.PPI_REMARK_WORDS
    names = ["Distributed systems", "Stakeholder influence", "this area", "Data modelling"]
    for name in names:
        text = fa._fallback_remark_45(name)
        count = fa.word_count(text)
        result.record(low <= count <= high, f"fallback_45({name!r}) = {count} words")
    for name in names:
        text = fa._unanswered_remark(name, 45)
        count = fa.word_count(text)
        result.record(low <= count <= high, f"unanswered({name!r}, 45) = {count} words")
    for name in names:
        text = fa._fallback_remark_25(name)
        count = fa.word_count(text)
        result.record(
            fa.MATCHING_REMARK_WORDS[0] <= count <= fa.MATCHING_REMARK_WORDS[1],
            f"fallback_25({name!r}) = {count} words",
        )
    return result


def _measure_no_banned_instrument() -> Result:
    """PPI is proprietary. No generated or fallback text may name a licensed
    instrument, and the detector must not fire on ordinary English."""
    result = Result("no_third_party_instrument")
    generated = [
        fa._fallback_remark_45("Stakeholder influence"),
        fa._fallback_remark_25("Distributed systems"),
        fa._unanswered_remark("Coaching", 45),
        fa._unanswered_remark("Coaching", 25),
    ]
    for text in generated:
        leak = _banned_in(text)
        result.record(leak is None, f"named {leak!r} in {text[:60]!r}")
    # Both directions. A detector that fires on "discuss" or "oceanic" would
    # reject legitimate copy, and nobody would trust it after the second time.
    for innocent in (
        "We discussed the trade-offs at length and the reasoning held up.",
        "Oceanic freight logistics experience is directly relevant here.",
        "Big Five Consulting is a former employer.",
    ):
        leak = _banned_in(innocent)
        expected_leak = "Big Five" in innocent
        result.record(
            (leak is not None) == expected_leak,
            f"{innocent[:40]!r} -> {leak!r}",
        )
    return result


def _measure_no_numbers_reach_a_client() -> Result:
    """The product's oldest rule. Rated output is four WORDS; the internal
    0-100 score never appears in anything a client reads."""
    result = Result("no_numbers_to_a_client")
    texts = [
        fa._fallback_remark_45("Distributed systems"),
        fa._fallback_remark_25("Data modelling"),
        fa._unanswered_remark("Stakeholder influence", 45),
        matching.enforce_word_range("Strong match.", *fa.MATCHING_REMARK_WORDS),
    ]
    for text in texts:
        hit = SCORE_SHAPED.search(text)
        result.record(hit is None, f"score-shaped {hit.group(0)!r} in {text[:60]!r}" if hit else "")
    # Every grade the client can be shown is one of the four words.
    for score in (0, 25, 59, 60, 74, 75, 89, 90, 100):
        grade = grade_for_percent(score)
        result.record(grade in GRADES, f"{score} produced {grade!r}, not one of the four grades")
    # The one documented exception, and its bounds: the radar band index is a
    # rendering coordinate (a radar has no geometry without a radius) and must
    # stay inside 1..4 so it can never be read as a score.
    for grade in GRADES:
        index = band_index_for(grade)
        result.record(1 <= index <= 4, f"band index {index} for {grade!r} is outside 1..4")
    return result


def _measure_radar_carries_no_numbers() -> Result:
    """Four charts, two shapes each, and not one number anywhere the reader can
    see: no axis tick, no data label, no tooltip. Built from the SAME dimension
    rows the sections render, so a chart cannot disagree with the text."""
    result = Result("radar_has_no_visible_numbers")
    rows = [
        (fa.CATEGORY_MATCHING, "Skills", 88, 82),
        ("primary", "Distributed systems", 91, 82),
        ("primary", "Data modelling", 72, 82),
        ("secondary", "Documentation", 64, 70),
        ("behavioural", "Coaching", 80, 75),
        (fa.CATEGORY_TECHNICAL, "Kafka", 77, None),
    ]
    dimensions = [
        {
            "category": category,
            "name": name,
            "score": score,
            "required_level": required,
            # `build_radar_charts` orders spokes by the row's ordinal, which is
            # what keeps a chart's geometry stable between two candidates on
            # the same job. Supplied here for the same reason it exists there.
            "ordinal": index,
        }
        for index, (category, name, score, required) in enumerate(rows)
    ]
    charts = fa.build_radar_charts(dimensions)
    result.record(len(charts) == 4, f"{len(charts)} charts, expected 4")
    for chart in charts:
        for axis in chart.get("axes", []):
            for key, value in axis.items():
                if key in {"name", "label"}:
                    hit = SCORE_SHAPED.search(str(value))
                    result.record(hit is None, f"axis label carries {value!r}")
    # The Overall chart plots the three PPI aggregates and EXCLUDES technical,
    # which carries no job-requirement level: including it would force the
    # requirement shape to invent a value for that spoke.
    overall = charts[0]
    names = {str(axis.get("name", "")).lower() for axis in overall.get("axes", [])}
    result.record(
        not any("kafka" in name for name in names),
        f"the overall chart plots a technical spoke: {names}",
    )
    return result


def _measure_culture_is_refused() -> Result:
    """Refused at the generator, at save, and by a Postgres CHECK. This
    measures the layer a test can reach, and both directions of it."""
    result = Result("culture_competency_refused")
    for name in CULTURE_NAMES:
        result.record(ppi.is_forbidden_competency(name), f"accepted {name!r}")
    for name in NOT_CULTURE_NAMES:
        result.record(
            not ppi.is_forbidden_competency(name),
            f"wrongly refused the legitimate competency {name!r}",
        )
    return result


def _measure_question_counts_by_grade() -> Result:
    """Counts are fixed by the CANDIDATE's grade, and the direction is the
    surprising part: MORE questions for a junior candidate, fewer for a CXO."""
    result = Result("question_counts_by_grade")
    expected_ppi = {"non_managerial": 25, "managerial": 20, "leadership": 15, "cxo": 10}
    expected_tech = {"non_managerial": 20, "managerial": 17, "leadership": 15, "cxo": 12}
    for grade, count in expected_ppi.items():
        actual = ppi.ppi_question_count(grade)
        result.record(actual == count, f"ppi {grade}: {actual}, expected {count}")
    for grade, count in expected_tech.items():
        actual = fa.technical_question_count(grade)
        result.record(actual == count, f"technical {grade}: {actual}, expected {count}")
    # A grade nobody recognises must not silently produce zero questions.
    result.record(ppi.ppi_question_count(None) > 0, "an unknown grade produced no PPI questions")
    result.record(
        fa.technical_question_count(None) > 0,
        "an unknown grade produced no technical questions",
    )
    return result


def _measure_unanswered_grades_not_matching() -> Result:
    """A question nobody answered grades Not Matching. It used to reach
    `_stable_score`, which hashes into 45..94: over 20,000 seeds, 69.6% of
    gibberish graded Moderately Matching or better."""
    result = Result("unanswered_is_not_matching")
    grade = grade_for_percent(fa.UNANSWERED_SCORE)
    result.record(grade == GRADE_NOT, f"UNANSWERED_SCORE grades {grade!r}")
    result.record(
        fa.UNANSWERED_SCORE < 60,
        f"UNANSWERED_SCORE ({fa.UNANSWERED_SCORE}) is inside a passing band",
    )
    return result


def _measure_probe_selection() -> Result:
    """Suggested interview questions anchor on whatever graded Moderately
    Matching or Not Matching, and on nothing else."""
    result = Result("probe_anchors")
    from app.services.rating import MODERATE_OR_BELOW, PROBE_THRESHOLD

    for score in (0, 25, 59, 60, 74):
        grade = grade_for_percent(score)
        result.record(
            grade in MODERATE_OR_BELOW and score < PROBE_THRESHOLD,
            f"{score} ({grade}) is not probe-worthy but should be",
        )
    for score in (75, 82, 90, 100):
        grade = grade_for_percent(score)
        result.record(
            grade not in MODERATE_OR_BELOW and score >= PROBE_THRESHOLD,
            f"{score} ({grade}) is probe-worthy but should not be",
        )
    return result


def _measure_report_reuse_is_retired() -> Result:
    """Nothing travels between applications. Under PPI both halves come from
    each job's own JD, so carrying a section across would state a grade against
    criteria the candidate was never assessed on."""
    result = Result("no_report_reuse")
    from app.services import retake

    result.record(
        len(retake.PORTABLE_CATEGORIES) == 0,
        f"PORTABLE_CATEGORIES has {len(retake.PORTABLE_CATEGORIES)} entries",
    )
    return result


# ── Runner ───────────────────────────────────────────────────────────────────

async def run() -> list[Result]:
    return [
        _measure_grade_boundaries(),
        _measure_matching_label_is_the_one_scale(),
        _measure_ranking_order(),
        _measure_no_weightage_table(),
        _measure_comment_word_range(),
        _measure_ppi_remark_word_range(),
        _measure_no_banned_instrument(),
        _measure_no_numbers_reach_a_client(),
        _measure_radar_carries_no_numbers(),
        _measure_culture_is_refused(),
        _measure_question_counts_by_grade(),
        _measure_unanswered_grades_not_matching(),
        _measure_probe_selection(),
        _measure_report_reuse_is_retired(),
    ]


def report(results: list[Result]) -> str:
    lines = ["", "PickReady report and matching agents, offline evaluation", ""]
    for item in results:
        mark = "PASS" if item.rate == 1.0 else "FAIL"
        lines.append(f"  [{mark}] {item.name}: {item.passed}/{item.total}")
        for failure in item.failures:
            if failure:
                lines.append(f"           {failure}")
    worst = min((item.rate for item in results), default=0.0)
    total = sum(item.total for item in results)
    lines.append("")
    lines.append(f"  {total} cases, lowest rate: {worst:.2f}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = asyncio.run(run())
    print(report(results))
    return 0 if all(item.rate == 1.0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
