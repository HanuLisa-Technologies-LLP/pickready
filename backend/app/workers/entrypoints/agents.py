"""Lambda handlers for the two request/response agents.

`readypick-jd-gen` and `readypick-company-profile`. Both are thin: they open a
session, call the service function that already implements the agent, and
return its result as JSON. No agent logic lives here, because there is one
implementation of each agent and it lives in `app/services` where the local
path calls it too.

WHY THESE ARE CONTAINER IMAGES AND NOT ZIPS
-------------------------------------------
The infrastructure brief suggests zip packaging unless a dependency forces
otherwise. A dependency forces otherwise. Both agents reach the model router,
the prompt registry, the agent loop and a database session, which pulls
SQLAlchemy, asyncpg, the OpenAI client and the whole `app` package. That is the
backend image, and building a second artifact carrying the same code would let
an agent and the API disagree about what a prompt says or what a grade means.
The same image, a different entry point, is the rule this repository already
follows for the API and the worker roles.

`readypick-assessment-trigger` IS a zip, and it is the only one: it imports
boto3 and nothing else, and it must stay that way, because it is the thing that
holds `iam:PassRole`.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from app.workers.entrypoints import bootstrap

bootstrap()
logger = logging.getLogger(__name__)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def jd_generation_handler(event: Any, _context: Any = None) -> dict[str, Any]:
    """`readypick-jd-gen`. Event: {"brief": {...}}."""
    if not isinstance(event, dict) or not isinstance(event.get("brief"), dict):
        raise ValueError("jd-gen event must carry a 'brief' object")

    async def _work() -> dict[str, Any]:
        from app.services import jd_generation

        return await jd_generation.generate_jd_document(event["brief"])

    result = _run(_work())
    logger.info(
        "agent.jd_generated title=%s chars=%d",
        str(event["brief"].get("title") or "-"),
        len(str(result.get("jd_markdown") or "")),
    )
    return result


def company_profile_handler(event: Any, _context: Any = None) -> dict[str, Any]:
    """`readypick-company-profile`. Event: {"company", "website", "industry"}."""
    if not isinstance(event, dict) or not str(event.get("company") or "").strip():
        raise ValueError("company-profile event must carry a non-empty 'company'")

    async def _work() -> dict[str, Any]:
        from app.services import company_research
        from app.workers.runtime import worker_session

        # A session is opened because the model router reads its provider keys
        # through one. It is NOT used to write anything: the draft is returned
        # for a human to apply behind an explicit Edit control, and a route that
        # saved on its own would rewrite the sections every candidate reads
        # without anyone approving the words.
        async with worker_session() as session:
            draft = await company_research.research_company(
                session,
                company=str(event["company"]),
                website=event.get("website"),
                industry=event.get("industry"),
            )
        return asdict(draft)

    result = _run(_work())
    logger.info(
        "agent.company_profile_drafted company=%s sources=%d degraded=%s",
        event["company"],
        len(result.get("sources") or []),
        result.get("degraded"),
    )
    return result
