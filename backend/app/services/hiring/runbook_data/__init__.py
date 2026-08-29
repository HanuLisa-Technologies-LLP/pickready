"""Typed, cached, read-only access to the Runbook's mechanical content.

PROVENANCE. Every value under this package is extracted from the Ready Pick Now
Hiring Philosophy & Intelligence Runbook, document ``RPN-PHIL-001`` v1.1, which
lives at the repository root as ``Readypick Hiring Philosophy.md``. These files
are DATA EXTRACTED FROM THE RUNBOOK, NOT AN INDEPENDENT SOURCE OF TRUTH. When
the two disagree the Runbook is right and this package is wrong, and
``backend/tests/test_runbook_parity.py`` fails until one of them is corrected.
It fails in both directions: editing a weight here without editing the Runbook
fails, and editing the Runbook without updating these files fails.

PATH SUBSTITUTION. spec-doc6 section 2.2 writes this package's path as
``app/hiring/runbook_data/``. This repository puts the hiring modules under
``backend/app/services/hiring/``, so the package lives at
``backend/app/services/hiring/runbook_data/``, alongside ``layers.py``,
``department_models.py`` and the rest. The substitution is the same one the
billing work made when its spec wrote ``companies`` and this schema meant
``tenants``.

FORMAT. YAML data files plus this loader, one format, chosen once. Nine files,
one per subject, each a mapping whose entries carry a ``source`` field naming
the Runbook section the entry came from, in the form
``"RPN-PHIL-001 <section sign>18.4"``. A ``meta`` key at the top of each file
carries the document id, the Runbook version and a one-line description; it is
the only mapping in a file that is exempt from carrying a ``source``.

BEHAVIOUR AND CONSTRAINTS.

- Loading is read-only and cached per file name. Callers get the same object
  back on every call and MUST NOT mutate it; a mutation is visible to every
  other caller in the process.
- A missing file, unreadable YAML, a document id that is not
  ``RPN-PHIL-001``, or a top-level entry with no ``source`` raises
  :class:`RunbookDataError`. Nothing here substitutes a default for a failed
  load: a silently defaulted weight is indistinguishable from a weight somebody
  chose, which is the whole failure mode the parity test exists to prevent.
- Nothing here interprets the data. Clamping, normalisation and scoring belong
  to the modules that read these files.
"""

from __future__ import annotations

import functools
import pathlib
from typing import Any, Dict, Tuple, cast

import yaml

#: Field every entry carries, naming the Runbook section it was taken from.
SOURCE_KEY = "source"

#: The Runbook's document id. Every ``source`` string starts with it.
DOCUMENT_ID = "RPN-PHIL-001"

#: The one top-level key exempt from carrying a ``source``: file metadata.
META_KEY = "meta"

_DIR = pathlib.Path(__file__).resolve().parent

_NAMES: Tuple[str, ...] = (
    "bands",
    "company_dna_instrument",
    "department_models",
    "dimensions",
    "disqualifiers",
    "evidence_tiers",
    "precedence",
    "situation_types",
    "swot_instrument",
)


class RunbookDataError(RuntimeError):
    """A Runbook data file is missing, malformed, or uncited.

    Raised rather than degraded, because the alternative is a caller scoring a
    candidate against a default nobody wrote down.
    """


def all_names() -> Tuple[str, ...]:
    """The nine file stems, without the ``.yaml`` suffix, in a stable order."""
    return _NAMES


@functools.lru_cache(maxsize=None)
def load(name: str) -> Dict[str, Any]:
    """Return one data file as a mapping. Cached; the result is shared.

    ``name`` is a file stem from :func:`all_names`, without ``.yaml``.

    Raises :class:`RunbookDataError` if the name is unknown, the file is
    absent, the YAML does not parse, the top level is not a mapping, the
    ``meta.document`` is not :data:`DOCUMENT_ID`, or any top-level entry other
    than ``meta`` carries no ``source`` citation anywhere beneath it.
    """
    if name not in _NAMES:
        raise RunbookDataError(
            "unknown Runbook data file %r; known files are %s"
            % (name, ", ".join(_NAMES)))
    path = _DIR / (name + ".yaml")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RunbookDataError(
            "cannot read Runbook data file %s: %s" % (path, exc)) from exc
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RunbookDataError(
            "Runbook data file %s is not valid YAML: %s" % (path, exc)) from exc
    if not isinstance(parsed, dict):
        raise RunbookDataError(
            "Runbook data file %s must contain a mapping at the top level, "
            "found %s" % (path, type(parsed).__name__))
    _check_meta(name, parsed)
    _check_cited(name, parsed)
    return cast(Dict[str, Any], parsed)


def _check_meta(name: str, parsed: Dict[str, Any]) -> None:
    meta = parsed.get(META_KEY)
    if not isinstance(meta, dict):
        raise RunbookDataError(
            "%s.yaml has no %r mapping; every data file states which document "
            "and which version it was extracted from" % (name, META_KEY))
    document = meta.get("document")
    if document != DOCUMENT_ID:
        raise RunbookDataError(
            "%s.yaml declares document %r; this package only carries data "
            "extracted from %s" % (name, document, DOCUMENT_ID))
    if not meta.get("runbook_version"):
        raise RunbookDataError(
            "%s.yaml states no runbook_version; a citation into a document "
            "whose version is unknown cannot be checked" % (name,))


def _has_source(node: Any) -> bool:
    """True if ``node`` carries a source, or something beneath it does."""
    if isinstance(node, dict):
        if isinstance(node.get(SOURCE_KEY), str):
            return True
        return any(_has_source(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_source(v) for v in node)
    return False


def _check_cited(name: str, parsed: Dict[str, Any]) -> None:
    for key, value in parsed.items():
        if key == META_KEY:
            continue
        if not _has_source(value):
            raise RunbookDataError(
                "%s.yaml: top-level entry %r carries no %r citation anywhere "
                "beneath it. Every value in this package names the Runbook "
                "section it came from, or it is a number somebody invented."
                % (name, key, SOURCE_KEY))


def bands() -> Dict[str, Any]:
    """Grade bands, the authenticity multiplier, confidence and the floors.

    Runbook sections 4.3, 10.5 to 10.10, 12.1, 12.2 and 14.1 to 14.3.
    """
    return load("bands")


def company_dna_instrument() -> Dict[str, Any]:
    """The twelve-section Company DNA intake instrument (Layer 2).

    Runbook sections 15, 16.1 to 16.12, 17.1 to 17.3 and Appendix A.
    """
    return load("company_dna_instrument")


def department_models() -> Dict[str, Any]:
    """Department competency models and the Layer 1 baseline weight matrix.

    Runbook Part VI (sections 21 to 36) and section 11.1.
    """
    return load("department_models")


def dimensions() -> Dict[str, Any]:
    """The five dimensions D1 to D5, their anchors and the aggregation.

    Runbook sections 8.9, 9.1 to 9.6, 10.2 to 10.4 and 11.2 to 11.5.
    """
    return load("dimensions")


def disqualifiers() -> Dict[str, Any]:
    """Permitted and prohibited disqualifier patterns.

    Runbook sections 3.5, 9.3, 12.1, 12.3, 12.4, 12.5 and 16.3.
    """
    return load("disqualifiers")


def evidence_tiers() -> Dict[str, Any]:
    """The six evidence tiers, their modifiers and the independence rules.

    Runbook sections 5.3 to 5.5, 6.1 to 6.7, 7.3 to 7.5, 38.1 and 38.3.
    """
    return load("evidence_tiers")


def precedence() -> Dict[str, Any]:
    """Layer precedence and conflict resolution as ordered predicates.

    Runbook sections 3.4, 3.5, 3.6, 13.2 to 13.5, 14.2.
    """
    return load("precedence")


def situation_types() -> Dict[str, Any]:
    """The six hiring situation types and their weight consequences.

    Runbook sections 11.3, 18.4 and 18.5.
    """
    return load("situation_types")


def swot_instrument() -> Dict[str, Any]:
    """The Role SWOT instrument, its probes and the scorecard rules (Layer 3).

    Runbook sections 18, 18.1 to 18.5, 19, 19.5, 20.2 to 20.5 and Appendix B.
    """
    return load("swot_instrument")


__all__ = [
    "DOCUMENT_ID",
    "META_KEY",
    "RunbookDataError",
    "SOURCE_KEY",
    "all_names",
    "bands",
    "company_dna_instrument",
    "department_models",
    "dimensions",
    "disqualifiers",
    "evidence_tiers",
    "load",
    "precedence",
    "situation_types",
    "swot_instrument",
]
