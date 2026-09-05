"""The two short agents a request handler waits for, invoked off the API task.

WHAT THESE ARE AND WHY THEY ARE NOT `dispatch`
----------------------------------------------
`dispatch` is fire-and-forget: the caller gets a run id and the answer arrives
in the database later. These two are the opposite. Generating a job description
and researching a company profile both produce a draft the recruiter is sitting
and waiting for, and the route already returns that draft in its response. So
they are invoked SYNCHRONOUSLY, the request handler blocks exactly as long as
it does today, and the only thing that changed is which process spends the time.

WHY MOVE THEM AT ALL, THEN
--------------------------
Because of what they cost the API while they run. `jd_generation` has a 25s
per-attempt and 50s total model budget, deliberately raised above the flat 15s
interactive cap because a multi-thousand-token JD cannot finish in fifteen
seconds. Company research spends a web search and a model call. Both are held
open on an API task that is also serving every other request, and the API runs
at a fixed desired count. Moving them to a function that scales per invocation
means a burst of JD generations cannot consume the capacity the rest of the
product needs.

WHY THERE IS NO `readypick-resume-jd-match`
-------------------------------------------
The infrastructure brief names a third short agent for resume-to-JD matching.
This product's resume-to-JD matching is `pickready.run_matching`: a batch over
every candidate linked to a job, with model calls per batch and a stage-by-stage
progress display a recruiter watches. It is minutes of work, and it is dispatched
in the background rather than awaited. A 256MB, 300-second function could not
finish it, and there is no single-candidate caller in the product to give such a
function instead, so building one would mean inventing a caller for it. It runs
as an on-demand Fargate task with the assessment agent, which is the same
pay-only-while-running model the brief is buying. Recorded in DEPLOYMENT_LOG.md.

TRANSPORT SELECTION, NOT A FALLBACK
-----------------------------------
Under the `aws` backend these invoke Lambda. Under `local` and `record` they
call the same service function in this process, which is precisely what the
route did before this change. There is one implementation of each agent, living
in `app/services`, and both the Lambda handler and the local path call it.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import get_settings
from app.workers import dispatch as dispatch_mod

logger = logging.getLogger(__name__)

JD_FUNCTION = "readypick-jd-gen"
COMPANY_PROFILE_FUNCTION = "readypick-company-profile"


class AgentInvokeError(RuntimeError):
    """The agent function could not be reached or refused the request.

    Raised rather than degraded. Both callers already handle a degraded DRAFT
    (`generate_jd_document` falls back to a template, `research_company` returns
    `degraded=True` with a reason), and those are answers the agent produced.
    A transport failure is not an answer, and returning an empty draft for one
    would present "we could not reach the agent" as "the agent found nothing".
    """


async def _invoke(function: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call `function` and wait for its answer.

    Synchronous invocation on a boto3 client is blocking, so it runs in a
    worker thread: awaiting it on the event loop directly would stall every
    other request this API task is serving for the whole model budget, which is
    the exact cost this move exists to avoid paying.
    """
    import asyncio

    def _call() -> dict[str, Any]:
        import boto3
        from botocore.config import Config

        settings = get_settings()
        client = boto3.client(
            "lambda",
            region_name=settings.aws_region,
            config=Config(
                connect_timeout=5,
                # Longer than the function's own timeout, so the function's
                # ceiling is what stops the work rather than the client giving
                # up on a call that is still running and being retried.
                read_timeout=settings.agent_invoke_read_timeout_seconds,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )
        response = client.invoke(
            FunctionName=function,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        if response.get("FunctionError"):
            # The function raised. Its body is the traceback, which must not
            # reach a recruiter, so only the fact is propagated.
            raise AgentInvokeError(f"{function} raised {response['FunctionError']}")
        body = response["Payload"].read()
        try:
            return json.loads(body)
        except ValueError as exc:
            raise AgentInvokeError(f"{function} returned unparseable output") from exc

    try:
        return await asyncio.to_thread(_call)
    except AgentInvokeError:
        raise
    except Exception as exc:  # noqa: BLE001 -- re-raised with the class name only
        logger.exception("agent.invoke_failed function=%s", function)
        raise AgentInvokeError(
            f"could not reach {function}: {type(exc).__name__}"
        ) from exc


def _remote() -> bool:
    return dispatch_mod.backend() == dispatch_mod.BACKEND_AWS


# -- JD generation -----------------------------------------------------------


async def generate_jd_document(brief: dict) -> dict:
    """The job description draft for `brief`. Same return shape as the service."""
    if not _remote():
        from app.services import jd_generation

        return await jd_generation.generate_jd_document(brief)
    result = await _invoke(JD_FUNCTION, {"brief": brief})
    if not isinstance(result, dict) or "jd_markdown" not in result:
        raise AgentInvokeError(f"{JD_FUNCTION} returned no jd_markdown")
    return result


# -- Company profile research ------------------------------------------------


async def research_company(
    session,
    *,
    company: str,
    website: str | None = None,
    industry: str | None = None,
):
    """One company's profile draft. Same return type as the service.

    `session` is used only by the local path, where it carries the caller's
    database session into the model router's key lookup. The remote path opens
    its own session inside the function, because a session is not serialisable
    and a connection does not cross a process boundary.
    """
    from app.services.company_research import CompanyProfileDraft

    if not _remote():
        from app.services import company_research

        return await company_research.research_company(
            session, company=company, website=website, industry=industry
        )
    result = await _invoke(
        COMPANY_PROFILE_FUNCTION,
        {"company": company, "website": website, "industry": industry},
    )
    if not isinstance(result, dict):
        raise AgentInvokeError(f"{COMPANY_PROFILE_FUNCTION} returned no draft")
    return CompanyProfileDraft(
        about_company=str(result.get("about_company") or ""),
        work_life=str(result.get("work_life") or ""),
        benefits=str(result.get("benefits") or ""),
        sources=list(result.get("sources") or []),
        degraded=bool(result.get("degraded")),
        message=result.get("message"),
    )
