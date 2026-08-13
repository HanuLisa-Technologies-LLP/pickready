"""Regression guards for the retired duplicate Company Page surface."""

from app.main import app
from app.services.capabilities import ALL_CAPABILITIES


def test_company_profile_is_the_only_company_information_api() -> None:
    paths = set(app.openapi()["paths"])

    assert "/api/v1/companies/me/profile" in paths
    assert "/api/v1/companies/me" not in paths
    assert "/api/v1/admin/my-tenant" not in paths


def test_company_page_permission_is_retired() -> None:
    assert "create_company_page" not in ALL_CAPABILITIES
