"""LangSmith tracing for every LLM call, attached at the one chokepoint.

WHERE IT ATTACHES, AND WHY THERE
--------------------------------
Every LLM call in the product already goes through
`llm_router.invoke_llm(task_type, ...)` -- that is a standing rule, not an
accident. So tracing goes there, once, rather than being sprinkled across the
technical scorer, the PPI scorer, the interviewer and report synthesis
individually. Four decorated call sites would be four things to remember on the
fifth agent; one chokepoint cannot be forgotten. It is the same reasoning that
put the temperature policy in `config/llm_providers` instead of in each caller.

A run is named after its `task_type`, so the LangSmith dashboard separates
`behavioral_assessment` from `report_synthesis` from `conversation_turn`
without any further wiring, and a quality regression can be read per agent.

IT MUST NEVER BREAK A CALL
--------------------------
Observability is not worth an outage. Every path here is wrapped: if the
LangSmith SDK raises, the network is unreachable, the key is wrong, or the
project does not exist, the call proceeds untraced and the product behaves
exactly as it did before. That is why this is a context manager that swallows
its own failures rather than a decorator that could propagate one -- a traced
call and an untraced call must be indistinguishable to the caller.

OFF BY DEFAULT
--------------
Tracing is enabled only when LANGSMITH_API_KEY is present AND
LANGSMITH_TRACING is not explicitly disabled. With no key -- local development,
CI, the test suite -- `trace_llm` is a no-op that allocates nothing and makes no
network call. Nobody has to remember to switch it off, and a test run cannot
post to a shared project.

WHAT IS RECORDED, AND WHAT IS NOT
---------------------------------
Recorded: task type, message count, prompt size, the model's response length,
which provider answered, and any error. That is enough to see which agent is
failing, which is slow, and which started returning something shorter than it
used to.

NOT recorded by default: the prompt and completion TEXT. A prompt here contains
a real candidate's answers and a real job description, and shipping that to a
third-party service is a decision for whoever owns the data, not a default this
module should quietly make. `LANGSMITH_TRACE_CONTENT=true` opts in.

NEVER recorded, under any setting: the provider API key. The router already
holds the never-log-a-key rule and this module does not weaken it.
"""
from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "is_enabled",
    "trace_agent_loop",
    "trace_content_enabled",
    "trace_llm",
]

_TRUTHY = {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    """Tracing is on only with a key, and only if not explicitly disabled.

    Read on every call rather than cached at import: the tests flip these, and a
    module-level constant would freeze whatever the first import happened to
    see.
    """
    if not os.getenv("LANGSMITH_API_KEY"):
        return False
    flag = os.getenv("LANGSMITH_TRACING")
    if flag is None:
        return True
    return flag.strip().lower() in _TRUTHY


def trace_content_enabled() -> bool:
    """Whether prompt and completion TEXT may leave the process.

    Defaults to false. Prompts carry candidate answers and job descriptions.
    """
    return (os.getenv("LANGSMITH_TRACE_CONTENT") or "").strip().lower() in _TRUTHY


@contextlib.contextmanager
def trace_llm(
    task_type: str,
    *,
    messages: list[dict] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Trace one logical LLM call. Yields a handle, or None when disabled.

    The handle carries `.end(output=..., provider=..., error=...)`. Calling it
    is optional: a caller that raises still produces a run, marked with the
    exception.
    """
    if not is_enabled():
        yield None
        return

    try:
        from langsmith import trace as ls_trace
    except Exception:  # noqa: BLE001 -- SDK missing or broken import
        logger.debug("tracing.langsmith_unavailable")
        yield None
        return

    payload: dict[str, Any] = {
        "task_type": task_type,
        "message_count": len(messages or []),
        "prompt_chars": sum(len(str(m.get("content") or "")) for m in (messages or [])),
    }
    if messages and trace_content_enabled():
        payload["messages"] = messages

    try:
        with ls_trace(
            name=f"llm:{task_type}",
            run_type="llm",
            inputs=payload,
            tags=["pickready", task_type],
            metadata=metadata or {},
        ) as run:
            yield _Handle(run)
    except Exception as exc:  # noqa: BLE001
        # The SDK itself failed -- bad key, unreachable endpoint, unknown
        # project. The call must still happen, so hand back a no-op handle and
        # let the caller run untraced.
        logger.info("tracing.disabled_for_call error=%s", type(exc).__name__)
        yield None


class _Handle:
    """Thin wrapper so a caller never touches the SDK's run object directly,
    and so every write to it is failure-tolerant."""

    __slots__ = ("_run",)

    def __init__(self, run: Any) -> None:
        self._run = run

    def end(
        self,
        *,
        output: str | None = None,
        provider: str | None = None,
        error: str | None = None,
    ) -> None:
        outputs: dict[str, Any] = {}
        if provider:
            outputs["provider"] = provider
        if error:
            outputs["error"] = error
        if output is not None:
            # Length always; the text only on explicit opt-in.
            outputs["output_chars"] = len(output)
            if trace_content_enabled():
                outputs["output"] = output
        try:
            self._run.end(outputs=outputs)
        except Exception:  # noqa: BLE001
            logger.debug("tracing.end_failed")


@contextlib.contextmanager
def trace_agent_loop(
    name: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Trace one complete generate/critique/revise loop as a LangSmith chain.

    Individual model calls remain child LLM traces at the router chokepoint.
    This parent records only operational quality metadata: iteration count,
    token budget, deterministic gate result and typed defect categories. It
    never sends candidate text, even when content tracing is enabled.
    """
    if not is_enabled():
        yield None
        return

    try:
        from langsmith import trace as ls_trace
    except Exception:  # noqa: BLE001
        logger.debug("tracing.langsmith_unavailable")
        yield None
        return

    try:
        manager = ls_trace(
            name=f"agent_loop:{name}",
            run_type="chain",
            inputs={"loop_name": name},
            tags=["pickready", "agent-loop", name],
            metadata=metadata or {},
        )
        run = manager.__enter__()
    except Exception as exc:  # noqa: BLE001
        logger.info("tracing.loop_disabled_for_call error=%s", type(exc).__name__)
        yield None
        return

    try:
        yield _LoopHandle(run)
    finally:
        try:
            manager.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.debug("tracing.loop_exit_failed")


class _LoopHandle:
    """Failure-tolerant writer for loop-level, non-content telemetry."""

    __slots__ = ("_run",)

    def __init__(self, run: Any) -> None:
        self._run = run

    def end(
        self,
        *,
        attempts: int,
        degraded: bool,
        elapsed_ms: int,
        generated_tokens: int,
        defects: list[dict[str, str]],
        error: str | None,
    ) -> None:
        try:
            self._run.end(
                outputs={
                    "attempts": attempts,
                    "degraded": degraded,
                    "gate_result": "degraded" if degraded else "passed",
                    "elapsed_ms": elapsed_ms,
                    "generated_tokens": generated_tokens,
                    "defects": defects,
                    "error": error,
                }
            )
        except Exception:  # noqa: BLE001
            logger.debug("tracing.loop_end_failed")
