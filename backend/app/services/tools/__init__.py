"""The tool layer every agent calls through, and the only place tool I/O is
validated, permissioned, bounded and counted.

WHY A LAYER AT ALL
------------------
Before this package an agent reached a service function directly. That works
right up until you ask the three questions this codebase has been bitten by
before: what was this agent actually allowed to touch, what did the call
return, and how long did it take. None of the three had an answer, because
there was nothing between the agent and the service to ask.

FOUR PROPERTIES, EACH ENFORCED STRUCTURALLY RATHER THAN BY CONVENTION
--------------------------------------------------------------------
  Permission   `permissions.AGENT_TOOLS` is DATA, and the executor refuses a
               name that is not granted BEFORE the handler is reached. Same
               shape as `require_capability`: never a role branch inside the
               tool, never a flag the handler checks and might forget.
  Validation   Inputs and outputs are Pydantic v2 models. An output that does
               not match its model is a TOOL defect (`ToolOutputError`), not
               something the caller has to notice -- the failure mode this
               replaces is a handler quietly returning `None` for a field an
               agent then renders into a prompt as the string "None".
  Bounds       Attempts AND wall clock, with the deadline PREDICTING the next
               attempt rather than observing the elapsed time. Identical rule
               to `agent_loop`, and for the identical reason: N attempts at the
               per-call timeout is a multiple of what the user experiences.
  Telemetry    Name, latency, status, cache outcome. NEVER inputs or outputs.
               A JD and a parsed resume are a real person's data and an
               ordinary log is far more widely readable than a trace.

TOOLS RAISE; LOOPS DEGRADE
--------------------------
`execute` raises on final failure and `agent_loop.run_loop` never does. That
split is deliberate and is the whole reason both can be simple: a tool's job is
to be correct or say it could not be, and deciding what a user sees when it
could not be is a product decision that belongs to the loop wrapping it. A tool
that swallowed its own failure and returned an empty shape would push that
decision into a caller that has no way to tell "no results" from "it broke".

COMPENSATION NEVER LEAVES A TOOL (ESD 16)
-----------------------------------------
`extract_jd` and `extract_resume` strip compensation-shaped keys in the tool,
not in the caller. Every agent prompt is downstream of these two, so stripping
here makes "salary never reaches a scoring prompt" a property of the layer
instead of a rule five call sites have to remember.
"""
from __future__ import annotations

from app.services.tools.errors import (
    RetryableToolError,
    ToolError,
    ToolExecutionError,
    ToolInputError,
    ToolNotFound,
    ToolOutputError,
    ToolPermissionError,
    ToolTimeout,
)
from app.services.tools.executor import ToolResult, execute
from app.services.tools.permissions import AGENT_TOOLS, AGENTS, granted_tools, is_granted
from app.services.tools.registry import ToolSpec, get, names, register, specs
from app.services.tools.telemetry import reset_tool_stats, tool_stats

# Importing the module registers every built-in tool. Kept last so the registry
# helpers above are bound before the handlers that call `register` run, and
# referenced explicitly so no import sorter can decide it is unused.
from app.services.tools import implementations as implementations  # noqa: E402

__all__ = [
    "AGENTS",
    "AGENT_TOOLS",
    "RetryableToolError",
    "ToolError",
    "ToolExecutionError",
    "ToolInputError",
    "ToolNotFound",
    "ToolOutputError",
    "ToolPermissionError",
    "ToolResult",
    "ToolSpec",
    "ToolTimeout",
    "execute",
    "get",
    "granted_tools",
    "implementations",
    "is_granted",
    "names",
    "register",
    "reset_tool_stats",
    "specs",
    "tool_stats",
]
