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
    beat_schedule={
        "refresh-dashboard-views": {
            "task": "pickready.refresh_dashboard_views",
            "schedule": 300.0,  # every 5 minutes (ESD §14)
        },
    },
)
