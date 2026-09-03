"""Face descriptor comparison (proctoring-spec-doc.md section 3.3).

A descriptor is a 128-float vector from a recognition network. It is NOT an
image and cannot be turned back into one; two descriptors of the same person
sit close together in Euclidean distance and two different people sit apart.
The browser computes a fresh descriptor every `identity_check_interval_seconds`
and compares it against the baseline captured at the system check.

WHY THE SERVER HAS A COPY OF THIS ARITHMETIC
--------------------------------------------
The comparison runs in the browser because the frames never leave it. What
the browser sends is the DISTANCE it measured, on an `IDENTITY_CHECK_MISMATCH`
event, and the server decides whether that distance is a mismatch against the
one threshold both sides read from `config.py`. A browser that reported a
mismatch at a distance below the threshold is running a different rule from
the one the report describes, and its event is recorded but does not count.
The distance function itself is here so the rule can be tested end to end
against real vectors rather than against a number a test typed in.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from app.models.proctoring import FACE_DESCRIPTOR_WIDTH
from app.services.proctoring.config import ProctoringConfig

__all__ = ["descriptor_distance", "is_mismatch", "MISMATCH_DISTANCE_KEY"]

#: The metadata key an `IDENTITY_CHECK_MISMATCH` event carries its measured
#: distance under. Absent means the browser did not report one.
MISMATCH_DISTANCE_KEY = "distance"


def descriptor_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Euclidean distance between two descriptors of the pinned width.

    Refuses a vector of the wrong width rather than truncating: a descriptor
    from a different network compared against a face-api.js baseline gives a
    number that means nothing, and a meaningless number below the threshold
    reads as a match.
    """
    if len(a) != FACE_DESCRIPTOR_WIDTH or len(b) != FACE_DESCRIPTOR_WIDTH:
        raise ValueError(
            f"a face descriptor has exactly {FACE_DESCRIPTOR_WIDTH} values; "
            f"got {len(a)} and {len(b)}"
        )
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def is_mismatch(distance: float | None, config: ProctoringConfig) -> bool:
    """Whether one identity check counts toward the consecutive-mismatch rule.

    A check with no reported distance is trusted as the browser sent it: the
    browser is the only party that saw the face, and refusing every event that
    omits a diagnostic would make the rule depend on a field the specification
    does not require. A check WITH a distance is held to the threshold.
    """
    if distance is None:
        return True
    return float(distance) > config.face_distance_threshold
