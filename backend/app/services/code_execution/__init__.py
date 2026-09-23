"""Code execution: the port domain code uses, and the one sandbox adapter.

Import from `app.services.code_execution.provider` (or this package, which
re-exports it). Never import `judge0` from domain code: the domain does not
know which sandbox runs the program, and
`tests/test_code_execution_architecture.py` enforces that.
"""
from app.services.code_execution.provider import (
    CodeExecutionProvider,
    ExecutionError,
    ExecutionLimits,
    ExecutionNotConfigured,
    ExecutionOutcome,
    ExecutionRejected,
    ExecutionTicket,
    ExecutionTicketLost,
    ExecutionUnavailable,
    ProviderHealth,
    TestExecution,
    TestInput,
    get_provider,
    is_enabled,
    outputs_match,
    override_provider,
)

__all__ = [
    "CodeExecutionProvider",
    "ExecutionError",
    "ExecutionLimits",
    "ExecutionNotConfigured",
    "ExecutionOutcome",
    "ExecutionRejected",
    "ExecutionTicket",
    "ExecutionTicketLost",
    "ExecutionUnavailable",
    "ProviderHealth",
    "TestExecution",
    "TestInput",
    "get_provider",
    "is_enabled",
    "outputs_match",
    "override_provider",
]
