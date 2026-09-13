"""Tool definition pinning: a schema change is a diff in CI, never a widening.

WHY A TOOL SCHEMA IS A SECURITY ARTIFACT
-----------------------------------------
An agent's permission was granted against a tool as it was DEFINED on the day
somebody read it. `AGENT_PROBE` holding `extract_assessment` is a decision
about what a transcript tool returns; widening `AssessmentFacts` with a field
carrying, say, the candidate's contact details changes what that grant means
without touching `permissions.AGENT_TOOLS` and without anybody re-reading the
grant. This platform runs no MCP server, and the control is the same one the
MCP rug pull needs: pin the definition, and make a change to it visible.

WHAT IS PINNED, AND WHAT DELIBERATELY IS NOT
----------------------------------------------
Pinned: the tool's name, its input and output JSON schemas, its risk class, and
the set of agents granted it. Those are what define REACH -- what an agent may
name, what it gets back, what the call can do to the world, and who may make
it.

Not pinned: `timeout_seconds`, `deadline_seconds`, `max_attempts`,
`cache_ttl_seconds`, and the description. Those define LATENCY and prose. A
manifest that churned when somebody tuned a timeout would be regenerated
without reading, which is how a pinned file stops pinning anything.

WHY THE HASH IS PER TOOL AND THERE IS ALSO A TOTAL
----------------------------------------------------
The per-tool digest names WHICH tool changed, which is the first thing a
reviewer needs. The `tools_digest` over the sorted per-tool digests catches the
case a per-tool comparison cannot: a tool REMOVED, or one added, in a diff that
touches nothing else.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

from app.services.tools import permissions, registry

#: Checked in beside this module. `tests/test_tool_schema_pinning.py` compares
#: it against the live registry and fails on any difference, so regenerating it
#: is a deliberate commit rather than a side effect of running the suite.
MANIFEST_PATH = pathlib.Path(__file__).with_name("tool_manifest.json")

#: Bumped when the SHAPE of this manifest changes, so an old file is refused as
#: incomparable rather than silently mismatching on every tool at once.
MANIFEST_VERSION = 1


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def describe_tool(spec: registry.ToolSpec) -> dict[str, Any]:
    """One tool's pinned definition, as plain comparable data."""
    definition: dict[str, Any] = {
        "name": spec.name,
        "risk": spec.risk.value,
        "granted_to": sorted(permissions.agents_holding(spec.name)),
        "input_schema": spec.input_model.model_json_schema(),
        "output_schema": spec.output_model.model_json_schema(),
    }
    definition["digest"] = _digest(definition)
    return definition


def build() -> dict[str, Any]:
    """The manifest for the registry as it stands in this process."""
    tools = [describe_tool(spec) for spec in registry.specs()]
    return {
        "manifest_version": MANIFEST_VERSION,
        "tools": tools,
        "tools_digest": _digest([tool["digest"] for tool in tools]),
    }


def load() -> dict[str, Any]:
    """The checked-in manifest.

    Raises when the file is absent. There is no "regenerate it silently"
    branch: a missing manifest means nothing is pinned, and answering that with
    an empty dict would turn the pinning test into a test that passes because
    it compared nothing.
    """
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def write() -> dict[str, Any]:
    """Rewrite the checked-in manifest from the live registry.

    Called by a person who has decided the change is intended, never by the
    executor and never by a test.
    """
    manifest = build()
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def differences() -> list[str]:
    """What the registry says that the checked-in manifest does not.

    Returns human-readable lines rather than a boolean, because "the manifest
    does not match" is not actionable and "extract_assessment gained a field"
    is.
    """
    current = build()
    pinned = load()
    out: list[str] = []

    if pinned.get("manifest_version") != MANIFEST_VERSION:
        return [
            f"manifest_version is {pinned.get('manifest_version')!r}, "
            f"this code writes {MANIFEST_VERSION!r}; regenerate it"
        ]

    pinned_tools = {tool["name"]: tool for tool in pinned.get("tools", [])}
    current_tools = {tool["name"]: tool for tool in current["tools"]}

    for name in sorted(set(current_tools) - set(pinned_tools)):
        out.append(f"{name}: registered but absent from the manifest")
    for name in sorted(set(pinned_tools) - set(current_tools)):
        out.append(f"{name}: in the manifest but no longer registered")

    for name in sorted(set(current_tools) & set(pinned_tools)):
        live, held = current_tools[name], pinned_tools[name]
        if live["digest"] == held.get("digest"):
            continue
        for key in ("risk", "granted_to", "input_schema", "output_schema"):
            if live[key] != held.get(key):
                out.append(f"{name}: {key} changed")
    return out
