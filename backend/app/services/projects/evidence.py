"""The evidence engine: parsed artifacts in, structured evidence out.

Everything here is deterministic arithmetic and table lookups -- the same
inputs produce the same Evidence Record every time, so a regenerated record is
diffable and a disagreement with an old one means the CODE changed, not that a
model sampled differently. The reasoning model sees only the reduced pack this
module builds (master brief sections 18 and 30).

Three layers are kept strictly apart, and each is labelled in the output:

    candidate_claims    what the candidate SAID (name, description, verbatim)
    observed_evidence   what the parsers FOUND (deterministic, with provenance)
    ai interpretation   what the model INFERRED (stored separately, never here)

"Versioned intelligence" means the decomposition below -- identity, stack,
architecture, implementation, testing, infrastructure, documentation, domain,
complexity, gaps, uncertainties, provenance -- not V1/V2 history.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.services.projects.formats import (
    FAMILY_CAD,
    FAMILY_DATA,
    FAMILY_DOCUMENT,
    FAMILY_MANIFEST,
    FAMILY_NOTEBOOK,
    FAMILY_SOURCE,
    FAMILY_SPREADSHEET,
)
from app.services.projects.limits import ProjectLimits
from app.services.projects.parsers import ParsedArtifact

# ── Evidence units ───────────────────────────────────────────────────────────

UNIT_TECHNOLOGY = "technology"
UNIT_ARCHITECTURE = "architecture"
UNIT_IMPLEMENTATION = "implementation"
UNIT_TESTING = "testing"
UNIT_INFRASTRUCTURE = "infrastructure"
UNIT_DOCUMENTATION = "documentation"
UNIT_ENGINEERING = "engineering_domain"
UNIT_LIMITATION = "limitation"

#: Ranking priority per unit type: what survives reduction first. INTERNAL
#: ordering data; it never crosses an API boundary.
_UNIT_PRIORITY: dict[str, int] = {
    UNIT_ARCHITECTURE: 0,
    UNIT_IMPLEMENTATION: 1,
    UNIT_TESTING: 2,
    UNIT_TECHNOLOGY: 3,
    UNIT_INFRASTRUCTURE: 4,
    UNIT_ENGINEERING: 5,
    UNIT_DOCUMENTATION: 6,
    UNIT_LIMITATION: 7,
}


@dataclass(frozen=True)
class EvidenceUnit:
    unit_type: str
    statement: str
    #: Where the finding came from: file path plus optional detail. Only as
    #: precise as the parser genuinely was -- no false precision.
    source_path: str
    source_detail: str | None = None

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "unit_type": self.unit_type,
            "statement": self.statement,
            "source_path": self.source_path,
        }
        if self.source_detail:
            payload["source_detail"] = self.source_detail
        return payload


def _units_from_artifact(artifact: ParsedArtifact) -> list[EvidenceUnit]:
    units: list[EvidenceUnit] = []
    signals = artifact.signals
    path = artifact.path

    for technology in signals.get("technologies") or []:
        units.append(
            EvidenceUnit(UNIT_TECHNOLOGY, f"{technology} in use", path)
        )
    if artifact.family == FAMILY_SOURCE:
        language = signals.get("language") or artifact.label
        units.append(EvidenceUnit(UNIT_TECHNOLOGY, f"{language} source code", path))
        if signals.get("is_test"):
            units.append(
                EvidenceUnit(UNIT_TESTING, f"Test code in {language}", path)
            )
        if signals.get("has_routes"):
            units.append(
                EvidenceUnit(UNIT_IMPLEMENTATION, "API endpoint definitions", path)
            )
        if signals.get("has_auth"):
            units.append(
                EvidenceUnit(
                    UNIT_IMPLEMENTATION, "Authentication or authorization logic", path
                )
            )
        if signals.get("has_db_access"):
            units.append(
                EvidenceUnit(UNIT_IMPLEMENTATION, "Database access logic", path)
            )
        if signals.get("has_concurrency"):
            units.append(
                EvidenceUnit(
                    UNIT_IMPLEMENTATION, "Concurrent or asynchronous execution", path
                )
            )
        if signals.get("has_error_handling"):
            units.append(
                EvidenceUnit(UNIT_IMPLEMENTATION, "Explicit error handling", path)
            )
    elif artifact.family == FAMILY_MANIFEST:
        if signals.get("is_containerised"):
            units.append(
                EvidenceUnit(UNIT_INFRASTRUCTURE, "Containerised build", path)
            )
        services = signals.get("compose_services") or []
        if len(services) >= 2:
            units.append(
                EvidenceUnit(
                    UNIT_ARCHITECTURE,
                    f"Multi-service composition ({', '.join(services[:6])})",
                    path,
                )
            )
        if signals.get("dependency_count"):
            units.append(
                EvidenceUnit(
                    UNIT_TECHNOLOGY,
                    f"Declared dependency manifest ({signals['manifest']})",
                    path,
                )
            )
    elif artifact.family == FAMILY_DOCUMENT:
        if signals.get("is_readme"):
            units.append(
                EvidenceUnit(UNIT_DOCUMENTATION, "Project README", path)
            )
        elif signals.get("word_count", 0) > 150:
            units.append(
                EvidenceUnit(UNIT_DOCUMENTATION, "Substantive project document", path)
            )
        for heading in (signals.get("headings") or [])[:6]:
            units.append(
                EvidenceUnit(
                    UNIT_DOCUMENTATION,
                    f"Documented section: {heading}",
                    path,
                    source_detail="heading",
                )
            )
    elif artifact.family == FAMILY_DATA:
        if signals.get("is_ci_config"):
            units.append(
                EvidenceUnit(UNIT_INFRASTRUCTURE, "Continuous integration workflow", path)
            )
        if signals.get("is_infrastructure"):
            units.append(
                EvidenceUnit(UNIT_INFRASTRUCTURE, "Infrastructure configuration", path)
            )
    elif artifact.family == FAMILY_NOTEBOOK:
        units.append(
            EvidenceUnit(
                UNIT_IMPLEMENTATION,
                f"Analysis notebook ({signals.get('code_cell_count', 0)} code cells)",
                path,
            )
        )
    elif artifact.family == FAMILY_SPREADSHEET:
        columns = signals.get("columns") or signals.get("sheet_names") or []
        if columns:
            units.append(
                EvidenceUnit(
                    UNIT_ENGINEERING,
                    f"Structured data ({', '.join(str(c) for c in columns[:6])})",
                    path,
                )
            )
    elif artifact.family == FAMILY_CAD:
        fmt = signals.get("format", artifact.label)
        detail_parts: list[str] = []
        if signals.get("product_count"):
            detail_parts.append(f"{signals['product_count']} products")
        if signals.get("assembly_relationships"):
            detail_parts.append(f"{signals['assembly_relationships']} assembly relationships")
        if signals.get("facet_count"):
            detail_parts.append(f"{signals['facet_count']} facets")
        if signals.get("entity_counts"):
            top = sorted(signals["entity_counts"].items(), key=lambda kv: -kv[1])[:4]
            detail_parts.append(", ".join(f"{k}" for k, _ in top))
        units.append(
            EvidenceUnit(
                UNIT_ENGINEERING,
                f"{fmt} design artifact"
                + (f" ({'; '.join(detail_parts)})" if detail_parts else ""),
                path,
            )
        )
    if artifact.limitation:
        units.append(
            EvidenceUnit(UNIT_LIMITATION, artifact.limitation, path)
        )
    return units


def build_units(
    artifacts: list[ParsedArtifact], limits: ProjectLimits
) -> list[EvidenceUnit]:
    """Extract, deduplicate, rank and cap the evidence units.

    Dedupe is by (type, statement): fifty React components produce ONE
    "React in use" unit whose provenance is its first sighting. That is the
    reduction step that keeps a thousand-file project from becoming a
    thousand-line prompt.
    """
    seen: dict[tuple[str, str], EvidenceUnit] = {}
    for artifact in artifacts:
        for unit in _units_from_artifact(artifact):
            seen.setdefault((unit.unit_type, unit.statement), unit)
    ranked = sorted(
        seen.values(),
        key=lambda unit: (_UNIT_PRIORITY.get(unit.unit_type, 9), unit.statement),
    )
    return ranked[: limits.max_evidence_units]


# ── Domain inference ─────────────────────────────────────────────────────────

_DOMAIN_BY_LANGUAGE: dict[str, str] = {
    "Verilog": "Electronics / hardware design",
    "SystemVerilog": "Electronics / hardware design",
    "VHDL": "Electronics / hardware design",
    "SPICE": "Electronics / circuit simulation",
    "SPICE netlist": "Electronics / circuit simulation",
    "Arduino C++": "Embedded systems",
    "Assembly": "Embedded systems",
    "MATLAB": "Engineering simulation and analysis",
}

_DOMAIN_BY_CAD: dict[str, str] = {
    "IFC": "Civil / architecture (BIM)",
    "DXF": "CAD drafting",
    "DWG": "CAD drafting",
    "STEP": "Mechanical design (CAD)",
    "STP": "Mechanical design (CAD)",
    "IGES": "Mechanical design (CAD)",
    "IGS": "Mechanical design (CAD)",
    "STL": "Mechanical design (CAD)",
}


def infer_domains(artifacts: list[ParsedArtifact]) -> list[str]:
    domains: list[str] = []

    def note(domain: str) -> None:
        if domain not in domains:
            domains.append(domain)

    for artifact in artifacts:
        if artifact.family == FAMILY_SOURCE:
            language = str(artifact.signals.get("language") or artifact.label)
            note(_DOMAIN_BY_LANGUAGE.get(language, "Software engineering"))
        elif artifact.family == FAMILY_CAD:
            note(_DOMAIN_BY_CAD.get(artifact.label.upper(), "Engineering design"))
        elif artifact.family == FAMILY_NOTEBOOK:
            note("Data analysis")
    return domains


# ── The Evidence Record ──────────────────────────────────────────────────────


def _gather(units: list[EvidenceUnit], unit_type: str) -> list[dict[str, Any]]:
    return [unit.as_json() for unit in units if unit.unit_type == unit_type]


def _deterministic_gaps(
    artifacts: list[ParsedArtifact], units: list[EvidenceUnit]
) -> list[str]:
    """Gaps stated from ABSENCE across the whole artifact set. Only rules whose
    absence is meaningful for what was actually submitted -- a CAD project is
    not told it lacks CI."""
    gaps: list[str] = []
    families = {artifact.family for artifact in artifacts}
    types_present = {unit.unit_type for unit in units}
    has_source = FAMILY_SOURCE in families or FAMILY_NOTEBOOK in families
    if has_source and UNIT_TESTING not in types_present:
        gaps.append("No test code was found in the submitted material.")
    if has_source and UNIT_DOCUMENTATION not in types_present:
        gaps.append("No README or project documentation was found.")
    if has_source and UNIT_INFRASTRUCTURE not in types_present:
        gaps.append(
            "No deployment, CI, or infrastructure configuration was found."
        )
    return gaps


def build_evidence_record(
    *,
    project_name: str,
    candidate_description: str,
    artifacts: list[ParsedArtifact],
    units: list[EvidenceUnit],
    submission_kind: str,
    repository_metadata: dict[str, Any] | None = None,
    processing_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """The persisted Project Evidence Record: decomposed, contextualised,
    traceable. Deterministic content only -- the AI interpretation is stored
    beside it, never inside it."""
    supported = [a for a in artifacts if a.supported]
    unsupported = [a for a in artifacts if not a.supported]
    languages = Counter(
        str(a.signals.get("language") or a.label)
        for a in supported
        if a.family == FAMILY_SOURCE
    )
    technologies = sorted(
        {
            unit.statement.removesuffix(" in use")
            for unit in units
            if unit.unit_type == UNIT_TECHNOLOGY and unit.statement.endswith(" in use")
        }
    )
    record: dict[str, Any] = {
        "project_identity": {
            "name": project_name,
            "submission_kind": submission_kind,
            "domains": infer_domains(artifacts),
        },
        # The CLAIM side, verbatim. Downstream reasoning weighs it against
        # observed evidence; nothing here converts it into fact.
        "candidate_claims": {
            "description": candidate_description,
        },
        "artifacts_observed": {
            "file_count": len(artifacts),
            "supported_count": len(supported),
            "unsupported_count": len(unsupported),
            "families": dict(Counter(a.family for a in artifacts)),
            "unsupported_files": [
                {"path": a.path, "limitation": a.limitation}
                for a in unsupported[:20]
            ],
        },
        "technology_stack": {
            "languages": dict(languages.most_common(15)),
            "technologies": technologies,
        },
        "architecture": _gather(units, UNIT_ARCHITECTURE),
        "implementation": _gather(units, UNIT_IMPLEMENTATION),
        "testing": _gather(units, UNIT_TESTING),
        "infrastructure": _gather(units, UNIT_INFRASTRUCTURE),
        "documentation": _gather(units, UNIT_DOCUMENTATION),
        "engineering_signals": _gather(units, UNIT_ENGINEERING),
        "potential_gaps": _deterministic_gaps(artifacts, units),
        "uncertainties": [
            unit.statement for unit in units if unit.unit_type == UNIT_LIMITATION
        ]
        + list(processing_limitations or []),
        "provenance": {
            "derived_from": submission_kind,
            "repository": repository_metadata or None,
            "parser": "deterministic-v1",
        },
    }
    return record


# ── The AI-ready pack ────────────────────────────────────────────────────────


def build_evidence_pack(
    record: dict[str, Any],
    units: list[EvidenceUnit],
    limits: ProjectLimits,
    *,
    readme_excerpt: str = "",
) -> str:
    """The compact, high-signal text the ONE reasoning call receives.

    Bounded by `max_ai_context_chars`; whole lines are dropped from the least
    important end rather than truncating mid-sentence, because a model handed
    half a sentence completes it from its own priors.
    """
    identity = record["project_identity"]
    lines: list[str] = [
        "PROJECT EVIDENCE PACK",
        f"Project: {identity['name']}",
        f"Submission: {identity['submission_kind']}",
    ]
    if identity["domains"]:
        lines.append("Domains: " + "; ".join(identity["domains"]))
    lines.append("")
    lines.append("Candidate description (their claim, verbatim):")
    lines.append(record["candidate_claims"]["description"])
    lines.append("")
    stack = record["technology_stack"]
    if stack["languages"]:
        lines.append("Languages observed: " + ", ".join(stack["languages"]))
    if stack["technologies"]:
        lines.append("Technologies observed: " + ", ".join(stack["technologies"]))
    lines.append("")
    lines.append("Observed evidence (deterministic, with source file):")
    for unit in units:
        if unit.unit_type == UNIT_LIMITATION:
            continue
        lines.append(f"- [{unit.unit_type}] {unit.statement} ({unit.source_path})")
    gaps = record["potential_gaps"]
    if gaps:
        lines.append("")
        lines.append("Deterministic gaps (absences, not judgments):")
        lines.extend(f"- {gap}" for gap in gaps)
    uncertainties = record["uncertainties"]
    if uncertainties:
        lines.append("")
        lines.append("Processing limitations:")
        lines.extend(f"- {item}" for item in uncertainties[:10])
    if readme_excerpt:
        lines.append("")
        lines.append("README excerpt:")
        lines.append(readme_excerpt[:2000])

    pack = "\n".join(lines)
    while len(pack) > limits.max_ai_context_chars and len(lines) > 8:
        lines.pop()
        pack = "\n".join(lines)
    return pack[: limits.max_ai_context_chars]
