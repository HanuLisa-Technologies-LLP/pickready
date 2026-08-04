"""Celery application. Task implementations live in app.workers.tasks.

Task-name contract (API code enqueues via `celery_app.send_task(name, ...)`
so the API process never imports worker dependencies):

  pickready.send_email(tenant_id, to, template_name, context, attachments=None)
  pickready.send_sms(phone, message)
  pickready.run_matching(job_id)
  pickready.parse_resume(profile_id)
  pickready.send_verification_requests(profile_id)
  pickready.parse_verification_reply(verification_request_id, raw_email_text)
  pickready.refresh_dashboard_views()
"""
from celery import Celery
from kombu import Queue

from app.core.config import get_settings

settings = get_settings()

# Delivery gets its OWN queue, because it was the thing that broke.
#
# Everything used to share one `celery` queue against a `--concurrency=2`
# worker. On 2026-08-01 two `generate_technical_questions` runs wedged both
# slots and a staff invitation enqueued behind them was never delivered: the
# API had already answered 201 with `email_dispatch: "queued"`, so the failure
# was invisible from the outside. Sending an email takes about three seconds
# and must never wait on an LLM chain that legitimately takes minutes.
#
# A worker started without `-Q` consumes every queue named in `task_queues`, so
# the existing single worker pool keeps draining BOTH after this change and no
# message can be stranded. The split only becomes a guarantee once a second
# pool runs with `--args=mail-worker` (see docker-entrypoint.sh), which is what
# gives delivery capacity that AI work cannot occupy.
QUEUE_DEFAULT = "celery"
QUEUE_MAIL = "mail"

celery_app = Celery(
    "pickready",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    task_default_queue=QUEUE_DEFAULT,
    # The routing key is set EXPLICITLY on both. Left unset, Celery fills it
    # from task_default_routing_key, which is the default queue name, so `mail`
    # would be declared with routing key "celery". The Redis transport picks
    # the destination list by ROUTING KEY, not by queue name, so every mail
    # message would have landed back in the `celery` list and a worker started
    # with `--queues=mail` would have sat idle forever while invitations piled
    # up behind the AI work this split exists to escape.
    task_queues=(
        Queue(QUEUE_DEFAULT, routing_key=QUEUE_DEFAULT),
        Queue(QUEUE_MAIL, routing_key=QUEUE_MAIL),
    ),
    # Routing is keyed on the task NAME, so it applies to `send_task` calls from
    # the API process exactly as it does to direct invocations. Every outbound
    # message the product sends is listed here; anything unrouted falls through
    # to the default queue, which is the safe direction to fail.
    task_routes={
        "pickready.send_email": {"queue": QUEUE_MAIL},
        "pickready.send_sms": {"queue": QUEUE_MAIL},
        "pickready.send_lifecycle_email": {"queue": QUEUE_MAIL},
        "pickready.send_payment_failed_email": {"queue": QUEUE_MAIL},
        "pickready.send_application_confirmation": {"queue": QUEUE_MAIL},
        "pickready.send_assessment_reminder": {"queue": QUEUE_MAIL},
        "pickready.send_verification_requests": {"queue": QUEUE_MAIL},
    },
    # An UNREACHABLE broker must fail, not hang. Publishing to Redis has no
    # timeout by default, so `send_task` against a private IP with no route
    # blocks forever rather than raising -- and every caller that carefully
    # wraps the enqueue in try/except gets no chance to run its handler, because
    # nothing is ever raised.
    #
    # Observed while seeding the demo candidates from a Cloud Run job that had
    # REDIS_URL but no VPC egress: the very first enqueue never returned and the
    # task was killed at the 900s ceiling with nothing written. The routing is
    # fixed separately (scripts/deploy.sh gives the job VPC access); this makes
    # the failure mode survivable wherever it happens next, including a
    # Memorystore outage taking down request handlers that only meant to queue
    # an email.
    broker_transport_options={
        "socket_connect_timeout": 5,
        "socket_timeout": 5,
    },
    # Bounded, for the same reason. The default retries the connection forever.
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=3,
    task_acks_late=True,
    task_default_retry_delay=30,
    # A task must never own a pool slot indefinitely. Observed in production:
    # two `generate_technical_questions` runs stopped emitting anything and were
    # still holding both ForkPoolWorkers fifteen minutes later, so every
    # subsequent assessment silently queued behind them and no job ever became
    # ready for candidates. The immediate cause (an LLM attempt that outran its
    # own timeout) is fixed in services/llm_router, but the pool needs its own
    # floor: any future hang costs one task, not the whole worker.
    #
    # Sized against the slowest legitimate chain, report_synthesis, whose total
    # LLM budget is 210s (config/llm_providers.TASK_TOTAL_BUDGET) and which also
    # does DB work either side. The soft limit raises SoftTimeLimitExceeded
    # inside the task, so `autoretry_for=(Exception,)` still gives it a retry
    # with backoff; the hard limit is the backstop that kills a task the soft
    # limit could not interrupt.
    task_soft_time_limit=600,
    task_time_limit=900,
    # Long tasks + acks_late: fetching a batch of messages up front would leave
    # queued work stranded behind whichever one is slow. One at a time.
    worker_prefetch_multiplier=1,
    beat_schedule={
        "refresh-dashboard-views": {
            "task": "pickready.refresh_dashboard_views",
            "schedule": 300.0,  # every 5 minutes (ESD §14)
        },
        # Still scheduled, and still necessary, but it now chases a DIFFERENT
        # thing. The technical bank's approval step was removed on 2026-08-04,
        # so the only remaining way a job sits at `questions_pending_review` is
        # an unapproved PPI FRAMEWORK. That gate survives, and it is exactly as
        # silent a bottleneck as the old one: applications keep arriving, no
        # candidate can be invited, and nothing says why. The task name is kept
        # as-is rather than renamed, because a beat entry and a worker
        # registration have to agree across a rolling deploy and renaming both
        # atomically is not something a rollout can guarantee.
        "remind-unapproved-technical-questions": {
            "task": "pickready.remind_unapproved_technical_questions",
            "schedule": 3600.0,
        },
        # Credit reconciliation for abandoned assessments (killer-spec §3.2).
        # Hourly rather than once a day: the sweep is idempotent, and an hourly
        # cadence means a reminder goes out near its 24h/72h mark instead of
        # whenever the daily run happens to land.
        "reconcile-assessment-credits": {
            "task": "pickready.reconcile_assessment_credits",
            "schedule": 3600.0,
        },
    },
)
