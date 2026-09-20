"""Content version for a job description, shared by audit and artifacts."""
from __future__ import annotations

import hashlib
import json

from app.models.job import Job


def jd_version(job: Job) -> str:
    material = json.dumps(
        {"markdown": job.jd_markdown or "", "sections": job.jd_json or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
