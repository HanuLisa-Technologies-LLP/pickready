"""Deployment health checks must prove the database is reachable."""

import pytest

from app import main


class _Session:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement) -> None:
        self.statements.append(str(statement))


@pytest.mark.asyncio
async def test_health_executes_database_probe(monkeypatch) -> None:
    session = _Session()
    monkeypatch.setattr(
        "app.core.db.get_session_factory",
        lambda: lambda: session,
    )

    response = await main.health()

    assert response == {"status": "ok", "database": "ok"}
    assert session.statements == ["SELECT 1"]


@pytest.mark.asyncio
async def test_health_fails_when_database_probe_fails(monkeypatch) -> None:
    class BrokenSession(_Session):
        async def execute(self, statement) -> None:
            raise ConnectionError("database unavailable")

    monkeypatch.setattr(
        "app.core.db.get_session_factory",
        lambda: lambda: BrokenSession(),
    )

    with pytest.raises(ConnectionError, match="database unavailable"):
        await main.health()
