"""readypick-assessment-trigger: start one on-demand Fargate task, and stop.

WHY THIS FUNCTION EXISTS AT ALL
-------------------------------
Running a Fargate task means holding `iam:PassRole` over the assessment agent's
task role, and passing a role is a privilege-escalation primitive: anything
that can pass a role can run code as it. The API service, which is reachable
from the internet through the load balancer, has no business holding that. This
function does, it does nothing else, and it depends on nothing but boto3, so
the blast radius of the one permission that matters is thirty lines of code
that a reviewer can read in full.

That is also why this is the only zip-packaged Lambda in the deployment. The
other three share the backend container image because they import the
application; this one must stay small enough to be obviously correct.

WHAT IT ACCEPTS
---------------
The dispatch payload, exactly as `app.workers.dispatch.payload_for` builds it:

    {"run_id": "...", "task": "pickready.run_functional_assessment",
     "args": ["<link id>"], "kwargs": {}}

It is passed through to the container untouched, in one environment variable,
because the payload shape is defined once in the application and re-spreading
it across ad-hoc variables here would be a second definition of it that drifts
the first time a task grows an argument.

WHY IT VALIDATES BEFORE IT RUNS
-------------------------------
`ecs:RunTask` accepts a request, answers 200, and reports per-task failures in
a `failures` list that an unchecked caller never reads. A function that returns
success on a request ECS refused is a function that loses work silently, which
is the failure mode this whole architecture is meant to make impossible. So the
response is inspected and a refusal is raised.
"""
from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CLUSTER = os.environ["ECS_CLUSTER"]
TASK_DEFINITION = os.environ["ECS_TASK_DEFINITION"]
CONTAINER_NAME = os.environ.get("ECS_CONTAINER_NAME", "readypick-assessment-agent")
SUBNETS = [s for s in os.environ["PRIVATE_SUBNET_IDS"].split(",") if s]
SECURITY_GROUPS = [s for s in os.environ["ECS_SECURITY_GROUP_IDS"].split(",") if s]

#: The container reads its whole assignment from here. Mirrors
#: `app.workers.entrypoints.ecs_task.TASK_ENV`.
TASK_ENV = "READYPICK_TASK"

_ecs = boto3.client(
    "ecs",
    config=Config(
        connect_timeout=5,
        read_timeout=15,
        retries={"max_attempts": 3, "mode": "standard"},
    ),
)


def lambda_handler(event, _context=None):
    if not isinstance(event, dict) or not event.get("task"):
        raise ValueError("assessment-trigger event must carry a 'task' name")

    payload = {
        "run_id": event.get("run_id", ""),
        "task": event["task"],
        "args": event.get("args") or [],
        "kwargs": event.get("kwargs") or {},
    }

    response = _ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=TASK_DEFINITION,
        launchType="FARGATE",
        count=1,
        # Tagged with the task name so a stopped task in the console, and the
        # CloudWatch metric filter over failures, can say WHICH kind of work
        # failed without opening its logs.
        propagateTags="TASK_DEFINITION",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": SUBNETS,
                "securityGroups": SECURITY_GROUPS,
                # Private subnets with a NAT route. A public IP would put the
                # agent directly on the internet to save one NAT hop.
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": CONTAINER_NAME,
                    "environment": [
                        {"name": TASK_ENV, "value": json.dumps(payload)},
                    ],
                }
            ]
        },
    )

    failures = response.get("failures") or []
    if failures:
        # Raised, so the invocation fails, the function's error metric moves,
        # and the on-failure destination publishes. See the module docstring.
        raise RuntimeError(
            f"ecs:RunTask refused {payload['task']}: "
            + "; ".join(
                f"{f.get('reason')} ({f.get('arn') or 'no arn'})" for f in failures
            )
        )

    tasks = response.get("tasks") or []
    if not tasks:
        raise RuntimeError(
            f"ecs:RunTask started nothing for {payload['task']} and reported no failure"
        )

    task_arn = tasks[0]["taskArn"]
    logger.info(
        "trigger.started task=%s run_id=%s task_arn=%s",
        payload["task"],
        payload["run_id"],
        task_arn,
    )
    return {"statusCode": 202, "taskArn": task_arn, "run_id": payload["run_id"]}
