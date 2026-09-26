"""The Vivekium harness (RPN-HARNESS-001), core.

WHY THIS IS A PACKAGE BESIDE `app/` RATHER THAN INSIDE IT
----------------------------------------------------------
`docs/spec/HARNESS.md` section 1 makes isolation the FIRST requirement, not the
last: a harness that production code can reach is a harness that can change
production behaviour. Siting it outside `app/` is what makes that assertion
cheap to state and impossible to drift past, and
`tests/test_harness_isolation.py` sweeps `app/` by AST to keep it true.

The dependency runs one way and only one way. The harness imports the product
(that is the whole point of a harness) and imports `app/evaluation/` for
metrics and for the report envelope that refuses a bare float. Nothing under
`app/` may name this package.

WHAT LIVES HERE, AND WHAT DELIBERATELY DOES NOT
------------------------------------------------
This module set is the CORE: what a scenario is, what a run is, where artifacts
land, how a report is rendered, and how a run is compared against a promoted
baseline. It knows how to describe and record an execution.

It does not know how to PERFORM one. The execution engine (`runner.py`,
`world.py`, `evaluators/`) and the fault layer (`faults.py`, `doubles/`) are
separate modules. The core imports them lazily and, when they are absent, says
so and exits rather than inventing a result: HARNESS.md section 4 is explicit
that silent survival is a finding rather than a pass, and the same argument
applies with more force to a harness reporting a pass it never computed.

Nothing here opens a database connection or calls a model at import time.
Importing this package is a cheap, side effect free act, which is what lets the
isolation sweep and the CLI both do it.
"""
from __future__ import annotations

#: The document this package conforms to. Quoted in run manifests so an
#: artifact read a year from now names the contract it was produced under,
#: rather than leaving a reader to guess which revision of the harness wrote
#: it. Bumped only when `docs/spec/HARNESS.md` changes in a way that changes
#: the shape of a scenario, a manifest or a report.
CONTRACT = "RPN-HARNESS-001"

__all__ = ["CONTRACT"]
