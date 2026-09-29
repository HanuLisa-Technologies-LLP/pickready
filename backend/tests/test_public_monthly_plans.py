"""The public price list and checkout share server-owned monthly plan data."""
from fastapi.testclient import TestClient

from app.main import app
from app.services.monthly_plans import PLANS


def test_public_plan_list_is_exact_and_needs_no_session() -> None:
    response = TestClient(app).get("/api/v1/billing/public/plans")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "public, max-age=300"
    plans = response.json()["plans"]
    assert len(plans) == 4
    for served, plan in zip(plans, PLANS, strict=True):
        assert served["slug"] == plan.slug
        assert served["name"] == plan.name
        assert served["assessments"] == plan.assessments
        assert served["price_inr"] == plan.price_inr
        assert served["gst_inr"] == plan.gst_inr
        assert served["total_inr"] == plan.total_inr
        assert served["rollover_months"] == 3


def test_legacy_public_credit_rate_is_not_published() -> None:
    assert TestClient(app).get("/api/v1/billing/public/credit-packs").status_code == 404
