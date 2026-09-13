"""Tool definitions are pinned, so a change to one is a diff somebody reads.

RPN-AI-UP-001 W3.4. An agent's permission was granted against a tool as it was
DEFINED on the day somebody read it. Widening `AssessmentFacts` with a field
carrying, say, a candidate's contact details changes what `AGENT_PROBE`'s grant
means without touching `permissions.AGENT_TOOLS` and without anybody
re-reading the grant. This platform runs no MCP server; the control is the same
one the MCP rug pull needs, applied internally.

WHAT FAILING THIS TEST MEANS
------------------------------
Not "something is broken". It means a tool's reach changed, and the fix is to
look at the change, decide it is intended, and regenerate the manifest in the
same commit:

    python -c "from app.services.tools import manifest; manifest.write()"

A test that regenerated the file itself would pass forever while pinning
nothing, which is the failure mode of every checked-in snapshot that has an
"update" flag people learn to reach for first.
"""
from __future__ import annotations

import json

from app.services import tools
from app.services.tools import manifest, permissions, policy


def test_the_checked_in_manifest_matches_the_live_registry() -> None:
    differences = manifest.differences()
    assert not differences, (
        "A tool definition changed. Read the change, and if it is intended "
        "regenerate the manifest in the same commit with "
        "`python -c \"from app.services.tools import manifest; "
        "manifest.write()\"`:\n  " + "\n  ".join(differences)
    )


def test_the_manifest_covers_every_registered_tool() -> None:
    pinned = {tool["name"] for tool in manifest.load()["tools"]}
    assert pinned == set(tools.names())


def test_the_total_digest_would_catch_a_tool_being_removed() -> None:
    """A per-tool comparison cannot see a deletion, because the tool it would
    have compared is not there to iterate over."""
    live = manifest.build()
    fewer = {
        **live,
        "tools": live["tools"][1:],
    }
    fewer["tools_digest"] = manifest._digest([t["digest"] for t in fewer["tools"]])
    assert fewer["tools_digest"] != live["tools_digest"]


def test_a_widened_output_schema_changes_that_tools_digest() -> None:
    """The failure the pinning exists for, reproduced on a copy.

    An extra field on an output model is the quiet version of widening reach:
    every agent already granted the tool starts receiving it, and no grant was
    edited.
    """
    spec = tools.get("extract_assessment")
    assert spec is not None
    before = manifest.describe_tool(spec)

    widened = json.loads(json.dumps(before))
    widened["output_schema"]["properties"]["candidate_email"] = {"type": "string"}
    widened.pop("digest")
    assert manifest._digest(widened) != before["digest"]


def test_the_risk_class_is_part_of_what_is_pinned() -> None:
    """`ToolSpec.risk` defaults to READ, which is the least privileged class and
    therefore the safe default. The dangerous direction is a tool that really
    does send an email inheriting it, and this is what makes that a diff: the
    risk class is in every tool's pinned definition."""
    for tool in manifest.load()["tools"]:
        assert tool["risk"] in {risk.value for risk in policy.RiskClass}
        assert tools.get(tool["name"]).risk.value == tool["risk"]


def test_who_holds_a_tool_is_part_of_what_is_pinned() -> None:
    """Granting an existing tool to another agent is a widening of reach that
    changes no schema at all, so the grant set is pinned beside the schemas."""
    for tool in manifest.load()["tools"]:
        assert tool["granted_to"] == sorted(permissions.agents_holding(tool["name"]))


def test_latency_settings_are_deliberately_not_pinned() -> None:
    """A manifest that churned when somebody tuned a timeout would be
    regenerated without reading, which is how a pinned file stops pinning."""
    pinned_keys = set(manifest.load()["tools"][0])
    for tuning in (
        "timeout_seconds",
        "deadline_seconds",
        "max_attempts",
        "cache_ttl_seconds",
        "description",
    ):
        assert tuning not in pinned_keys


def test_the_manifest_version_is_carried_so_an_old_file_is_refused() -> None:
    """A shape change to the manifest itself must read as "regenerate this",
    not as every tool having changed at once."""
    assert manifest.load()["manifest_version"] == manifest.MANIFEST_VERSION
