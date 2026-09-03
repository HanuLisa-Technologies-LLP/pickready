"""The numbers that say whether an agent got better, and what each one hides.

WHY SEVERAL METRICS PER AGENT
------------------------------
Every single metric here can be gamed by a change that makes the product worse.
Precision@5 rises if the ranker collapses onto one obvious profile shape;
diversity rises if it ranks randomly. Generic-language rate falls to zero if
remarks become terse and uninformative. They are reported together because the
combination is much harder to move in the wrong direction than any one of them.

WHAT IS MEASURABLE WITHOUT A HUMAN, AND WHAT IS NOT
----------------------------------------------------
Structural metrics -- word ranges, evidence sourcing, generic-language rate,
behavioural phrasing, no-numbers compliance -- are computed from the output
alone and need no labels. Quality metrics -- is this the right ranking, is this
report insightful -- need ground truth a recruiting expert produced, and this
module computes them only when that ground truth exists. It never estimates
them, because an estimated quality metric is a number that moves when the
estimator changes and looks exactly like progress.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.services.verification import generic_language


# ── Ranking quality (needs ground truth) ─────────────────────────────────────


def ndcg(predicted: Sequence[Any], relevance: dict[Any, float], k: int = 5) -> float:
    """Normalised discounted cumulative gain over expert star ratings.

    Rank-position aware, unlike precision: putting the best candidate fifth
    instead of first is a real regression that precision@5 cannot see at all.
    """
    if not relevance or k <= 0:
        return 0.0
    gains = [relevance.get(item, 0.0) for item in list(predicted)[:k]]
    actual = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    best = sorted(relevance.values(), reverse=True)[:k]
    ideal = sum(gain / math.log2(index + 2) for index, gain in enumerate(best))
    return round(actual / ideal, 4) if ideal else 0.0


def spearman(predicted: Sequence[float], truth: Sequence[float]) -> float:
    """Rank correlation between predicted scores and expert ratings.

    Rank-based rather than Pearson because the two scales are not comparable:
    an internal 0-100 and a 1-5 star rating agree about ORDER or they do not.
    """
    pairs = list(zip(predicted, truth))
    n = len(pairs)
    if n < 2:
        return 0.0

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        index = 0
        while index < len(order):
            # Average tied ranks, or two identical scores would get an arbitrary
            # order and a spurious correlation.
            stop = index
            while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
                stop += 1
            average = (index + stop) / 2 + 1
            for position in range(index, stop + 1):
                result[order[position]] = average
            index = stop + 1
        return result

    predicted_ranks, truth_ranks = ranks([p for p, _ in pairs]), ranks([t for _, t in pairs])
    differences = sum((a - b) ** 2 for a, b in zip(predicted_ranks, truth_ranks))
    return round(1 - (6 * differences) / (n * (n * n - 1)), 4)


def diversity(profiles: Sequence[Sequence[str]]) -> float:
    """How different the top-k candidates' skill profiles are from each other.

    1.0 means no two share a skill; 0.0 means they are identical. Reported
    alongside precision precisely because it is the metric precision cannot see:
    a ranker that found five copies of one profile scores well on precision and
    has told the recruiter nothing.
    """
    sets = [set(item) for item in profiles if item]
    if len(sets) < 2:
        return 1.0
    overlaps = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            overlaps.append(len(sets[i] & sets[j]) / len(union) if union else 0.0)
    return round(1 - (sum(overlaps) / len(overlaps)), 4)


# ── Output quality (no ground truth needed) ──────────────────────────────────


#: A behavioural question asks what someone DID. A hypothetical asks what they
#: WOULD do, and the answer to a hypothetical is imagination, not evidence.
_HYPOTHETICAL = ("would you", "what would", "if you were", "imagine", "hypothetically")


def behavioural_rate(probes: Sequence[str]) -> float:
    if not probes:
        return 0.0
    behavioural = sum(
        1
        for probe in probes
        if not any(marker in str(probe).casefold() for marker in _HYPOTHETICAL)
    )
    return round(behavioural / len(probes), 4)


