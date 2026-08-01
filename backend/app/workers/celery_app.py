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

from app.core.config import get_settings

settings = get_settings()

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
