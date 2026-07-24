"""Import the permanent MVP jobs catalogue supplied for PickReady.

The source CSV is deliberately *not* read at runtime.  Its normalized contents
live in this idempotent Alembic data migration so every environment receives
the same 30 publish-ready roles through the normal database lifecycle.
"""
from __future__ import annotations

import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


# company, source id, title, location, experience, level, openings, salary,
# remote, posting role, skills.  All CSV rows were Engineering / Full-time,
# use the same education preference, were Open, and were dated 2026-07-24.
JOBS = (
    ("Sarkar Corp", "JOB-1001", "Java Backend Developer", "Pune", "3-5", "Mid", 1, "10-15", False, "recruiter", ("Java", "Spring Boot", "REST", "MySQL", "Docker")),
    ("Sarkar Corp", "JOB-1002", "Python Backend Developer", "Pune", "0-2", "Junior", 4, "22-35", False, "hiring_manager", ("Python", "FastAPI", "PostgreSQL", "Redis", "Docker")),
    ("Sarkar Corp", "JOB-1003", "MERN Stack Developer", "Hyderabad", "0-2", "Senior", 4, "10-15", True, "hiring_manager", ("MongoDB", "Express", "React", "Node.js")),
    ("Sarkar Corp", "JOB-1004", "Full Stack Developer (.NET)", "Hyderabad", "0-2", "Junior", 3, "6-10", False, "hr_manager", (".NET", "ASP.NET Core", "C#", "SQL Server")),
    ("Sarkar Corp", "JOB-1005", "React Frontend Developer", "Chennai", "0-2", "Mid", 2, "6-10", True, "hr_manager", ("React", "TypeScript", "Redux", "HTML", "CSS")),
    ("Sarkar Corp", "JOB-1006", "Machine Learning Engineer", "Chennai", "0-2", "Senior", 1, "15-22", False, "hr_manager", ("Python", "PyTorch", "TensorFlow", "ML")),
    ("Sarkar Corp", "JOB-1007", "AI / Generative AI Engineer", "Pune", "5-8", "Mid", 2, "10-15", True, "hr_manager", ("LLMs", "RAG", "LangGraph", "Vector DBs")),
    ("Sarkar Corp", "JOB-1008", "Data Engineer", "Pune", "5-8", "Mid", 5, "15-22", False, "hiring_manager", ("Python", "Spark", "Airflow", "Kafka")),
    ("Sarkar Corp", "JOB-1009", "Data Analyst", "Chennai", "0-2", "Mid", 4, "15-22", False, "recruiter", ("SQL", "Power BI", "Python", "Excel")),
    ("Sarkar Corp", "JOB-1010", "DevOps / Cloud Engineer", "Pune", "0-2", "Mid", 1, "10-15", True, "hr_manager", ("AWS", "Docker", "Kubernetes", "Terraform")),
    ("ACRM Corp", "JOB-1011", "Java Backend Developer", "Pune", "2-4", "Mid", 2, "10-15", False, "hr_manager", ("Java", "Spring Boot", "REST", "MySQL", "Docker")),
    ("ACRM Corp", "JOB-1012", "Python Backend Developer", "Chennai", "5-8", "Senior", 5, "22-35", False, "hiring_manager", ("Python", "FastAPI", "PostgreSQL", "Redis", "Docker")),
    ("ACRM Corp", "JOB-1013", "MERN Stack Developer", "Remote", "2-4", "Mid", 1, "22-35", True, "hr_manager", ("MongoDB", "Express", "React", "Node.js")),
    ("ACRM Corp", "JOB-1014", "Full Stack Developer (.NET)", "Pune", "3-5", "Mid", 5, "10-15", False, "recruiter", (".NET", "ASP.NET Core", "C#", "SQL Server")),
    ("ACRM Corp", "JOB-1015", "React Frontend Developer", "Hyderabad", "0-2", "Junior", 3, "15-22", False, "recruiter", ("React", "TypeScript", "Redux", "HTML", "CSS")),
    ("ACRM Corp", "JOB-1016", "Machine Learning Engineer", "Hyderabad", "5-8", "Mid", 1, "10-15", True, "hiring_manager", ("Python", "PyTorch", "TensorFlow", "ML")),
    ("ACRM Corp", "JOB-1017", "AI / Generative AI Engineer", "Pune", "0-2", "Mid", 4, "10-15", True, "recruiter", ("LLMs", "RAG", "LangGraph", "Vector DBs")),
    ("ACRM Corp", "JOB-1018", "Data Engineer", "Remote", "0-2", "Mid", 4, "15-22", False, "hr_manager", ("Python", "Spark", "Airflow", "Kafka")),
    ("ACRM Corp", "JOB-1019", "Data Analyst", "Bengaluru", "2-4", "Junior", 3, "6-10", True, "hr_manager", ("SQL", "Power BI", "Python", "Excel")),
    ("ACRM Corp", "JOB-1020", "DevOps / Cloud Engineer", "Remote", "5-8", "Junior", 4, "22-35", True, "hr_manager", ("AWS", "Docker", "Kubernetes", "Terraform")),
    ("Specter & Co.", "JOB-1021", "Java Backend Developer", "Remote", "5-8", "Mid", 1, "6-10", False, "hiring_manager", ("Java", "Spring Boot", "REST", "MySQL", "Docker")),
    ("Specter & Co.", "JOB-1022", "Python Backend Developer", "Hyderabad", "3-5", "Junior", 5, "15-22", True, "hr_manager", ("Python", "FastAPI", "PostgreSQL", "Redis", "Docker")),
    ("Specter & Co.", "JOB-1023", "MERN Stack Developer", "Bengaluru", "2-4", "Senior", 1, "6-10", False, "recruiter", ("MongoDB", "Express", "React", "Node.js")),
    ("Specter & Co.", "JOB-1024", "Full Stack Developer (.NET)", "Remote", "5-8", "Junior", 3, "10-15", True, "hiring_manager", (".NET", "ASP.NET Core", "C#", "SQL Server")),
    ("Specter & Co.", "JOB-1025", "React Frontend Developer", "Hyderabad", "3-5", "Senior", 4, "15-22", False, "hr_manager", ("React", "TypeScript", "Redux", "HTML", "CSS")),
    ("Specter & Co.", "JOB-1026", "Machine Learning Engineer", "Pune", "0-2", "Senior", 1, "15-22", True, "hiring_manager", ("Python", "PyTorch", "TensorFlow", "ML")),
    ("Specter & Co.", "JOB-1027", "AI / Generative AI Engineer", "Chennai", "0-2", "Senior", 5, "15-22", False, "hr_manager", ("LLMs", "RAG", "LangGraph", "Vector DBs")),
    ("Specter & Co.", "JOB-1028", "Data Engineer", "Bengaluru", "5-8", "Junior", 5, "6-10", False, "hr_manager", ("Python", "Spark", "Airflow", "Kafka")),
    ("Specter & Co.", "JOB-1029", "Data Analyst", "Hyderabad", "0-2", "Junior", 5, "10-15", True, "hr_manager", ("SQL", "Power BI", "Python", "Excel")),
    ("Specter & Co.", "JOB-1030", "DevOps / Cloud Engineer", "Bengaluru", "5-8", "Mid", 2, "10-15", True, "hiring_manager", ("AWS", "Docker", "Kubernetes", "Terraform")),
)

TENANT_DOMAINS = {
    "Sarkar Corp": "sarkar-corp.local",
    "ACRM Corp": "acrm-corp.local",
    "Specter & Co.": "specter-co.local",
}


def upgrade() -> None:
    connection = op.get_bind()
    for (
        company, source_job_id, title, location, experience_years, level,
        openings, salary_lpa, remote, posting_role, skills,
    ) in JOBS:
        jd = {
            "source_job_id": source_job_id,
            "source_status": "Open",
            "role": title,
            "description": f"We are hiring a {title} to join {company}.",
            "department": "Engineering",
            "location": location,
            "employment_type": "Full-time",
            "experience_years": experience_years,
            "education": "B.E./B.Tech (preferred)",
            "skills": list(skills),
            "openings": openings,
            "remote": remote,
            "application_deadline": "2026-09-30",
            "import_note": "Permanent MVP jobs catalogue import",
        }
        compensation = {"currency": "INR", "range_lpa": salary_lpa, "unit": "LPA"}
        connection.execute(
            sa.text(
                """
                INSERT INTO jobs (
                    id, tenant_id, title, department, level, jd_json,
                    compensation_json, status, requirement_period, created_by,
                    ratified_at, created_at
                )
                SELECT
                    CAST(:id AS uuid), t.id, CAST(:title AS varchar),
                    'Engineering', CAST(:level AS varchar), CAST(:jd AS jsonb),
                    CAST(:compensation AS jsonb), 'ratified',
                    CAST(:experience_years AS varchar), creator.id,
                    CAST('2026-07-24T00:00:00+00:00' AS timestamptz),
                    CAST('2026-07-24T00:00:00+00:00' AS timestamptz)
                FROM tenants t
                LEFT JOIN LATERAL (
                    SELECT u.id
                    FROM users u
                    WHERE u.tenant_id = t.id AND u.role = CAST(:posting_role AS varchar)
                    ORDER BY u.created_at, u.id
                    LIMIT 1
                ) creator ON true
                WHERE t.domain = CAST(:tenant_domain AS varchar)
                  AND NOT EXISTS (
                    SELECT 1 FROM jobs existing
                    WHERE existing.tenant_id = t.id
                      AND existing.jd_json ->> 'source_job_id' = CAST(:source_job_id AS text)
                  )
                """
            ),
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"pickready:mvp:{source_job_id}")),
                "tenant_domain": TENANT_DOMAINS[company],
                "source_job_id": source_job_id,
                "title": title,
                "level": level,
                "jd": json.dumps(jd),
                "compensation": json.dumps(compensation),
                "experience_years": experience_years,
                "posting_role": posting_role,
            },
        )


def downgrade() -> None:
    # This is customer-supplied operating data.  It remains on downgrade rather
    # than deleting live jobs and their candidate links/audit history.
    pass
