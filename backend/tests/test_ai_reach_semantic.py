"""Change 7 regression tests for AI Reach ranking and shared breaker state."""

import uuid

import pytest

from app.services import bd_leads, reach_embeddings, web_research


TITLES = (
    "Java Backend Developer",
    "Python Backend Developer",
    "MERN Stack Developer",
    "Full Stack Developer (.NET)",
    "React Frontend Developer",
    "Machine Learning Engineer",
    "AI / Generative AI Engineer",
    "Data Engineer",
    "Data Analyst",
    "DevOps / Cloud Engineer",
)
TENANTS = ("Sarkar Corp", "ACRM Corp", "Specter & Co.")
QUERIES = {
    "Java Developer": "Java Backend Developer",
    "Machine Learning Engineer": "Machine Learning Engineer",
    "React Frontend Developer": "React Frontend Developer",
    "Data Analyst": "Data Analyst",
    "DevOps Engineer": "DevOps / Cloud Engineer",
}


def _family_vector(value: str) -> tuple[float, ...]:
    normalized = bd_leads.normalize_role(value)
    families = (
        "java",
        "machine learning",
        "react frontend",
        "data analyst",
        "devops",
    )
    return tuple(1.0 if family in normalized else 0.0 for family in families)


def _catalogue() -> list[bd_leads.ReachCandidate]:
    return [
        bd_leads.ReachCandidate(
            job_id=uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant}:{title}"),
            title=title,
            skills=(),
            vector=_family_vector(title),
            tenant_name=tenant,
            tenant_industry="Technology",
            website_domain=f"{tenant.casefold().replace(' ', '-')}.example.com",
            tenant_domain=None,
            jd_json={},
        )
        for tenant in TENANTS
        for title in TITLES
    ]


@pytest.mark.parametrize("query,expected", QUERIES.items())
def test_known_catalogue_role_ranks_first(query: str, expected: str) -> None:
    ranked = bd_leads.rank_role_candidates(
        query, _family_vector(query), _catalogue()
    )

    assert ranked
    assert ranked[0].candidate.title == expected
    assert [item.candidate.tenant_name for item in ranked[:3]] == sorted(TENANTS)
    assert {item.candidate.title for item in ranked[:3]} == {expected}


def test_irrelevant_results_are_not_used_as_padding() -> None:
    ranked = bd_leads.rank_role_candidates(
        "Ruby on Rails Developer",
        (0.0,) * len(QUERIES),
        _catalogue(),
    )
    assert ranked == []


@pytest.mark.asyncio
async def test_real_embedding_model_ranks_known_catalogue() -> None:
    skills_by_title = {
        "Java Backend Developer": ("Java", "Spring Boot", "REST", "MySQL", "Docker"),
        "Python Backend Developer": ("Python", "FastAPI", "PostgreSQL", "Redis"),
        "MERN Stack Developer": ("MongoDB", "Express", "React", "Node.js"),
        "Full Stack Developer (.NET)": (".NET", "C#", "SQL Server"),
        "React Frontend Developer": ("React", "TypeScript", "Redux", "HTML", "CSS"),
        "Machine Learning Engineer": ("Python", "PyTorch", "TensorFlow", "ML"),
        "AI / Generative AI Engineer": ("LLMs", "RAG", "LangGraph", "Vector DBs"),
        "Data Engineer": ("Python", "Spark", "Airflow", "Kafka"),
        "Data Analyst": ("SQL", "Power BI", "Python", "Excel"),
        "DevOps / Cloud Engineer": ("AWS", "Docker", "Kubernetes", "Terraform"),
    }
    vectors = await reach_embeddings.embed_passages(
        [
            bd_leads.role_embedding_text(title, skills_by_title[title])
            for title in TITLES
        ]
    )
    candidates = [
        bd_leads.ReachCandidate(
            job_id=uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant}:{title}"),
            title=title,
            skills=skills_by_title[title],
            vector=tuple(vectors[TITLES.index(title)]),
            tenant_name=tenant,
            tenant_industry="Technology",
            website_domain="example.com",
            tenant_domain=None,
            jd_json={},
        )
        for tenant in TENANTS
        for title in TITLES
    ]

    for query, expected in QUERIES.items():
        query_vector = await reach_embeddings.embed_query(
            bd_leads.role_embedding_text(query, ())
        )
        ranked = bd_leads.rank_role_candidates(query, query_vector, candidates)
        assert {item.candidate.title for item in ranked} == {expected}
        assert {item.candidate.tenant_name for item in ranked} == set(TENANTS)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.ttls[key] = ex

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)


@pytest.mark.asyncio
async def test_shared_breaker_trips_surfaces_retry_and_resets(monkeypatch) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(web_research, "_breaker_redis", lambda: redis)

    for _ in range(web_research._FAILURE_THRESHOLD):
        await web_research._record_failure()

    opened = await web_research._plan_node(
        {"ctx": type("Context", (), {"api_key": "configured"})()}
    )
    assert opened["status"] == "breaker_open"
    assert "retry automatically" in opened["message"]

    assert await web_research.reset_breaker() is True
    recovered = await web_research._plan_node(
        {
            "ctx": type(
                "Context",
                (),
                {
                    "api_key": "configured",
                    "job_role": "Java Developer",
                    "city": "Pune",
                    "industry": "Technology",
                    "company": None,
                },
            )()
        }
    )
    assert recovered["status"] == "ok"
    assert recovered["queries"]


def test_quota_is_distinct_from_timeout_and_unavailable() -> None:
    response = type("Response", (), {"status_code": 429})()
    quota_error = type("ProviderError", (Exception,), {})("quota")
    quota_error.response = response

    assert web_research._provider_failure(quota_error) == "quota_exhausted"
    assert web_research._provider_failure(TimeoutError()) == "timeout"
    assert web_research._provider_failure(RuntimeError()) == "unavailable"
