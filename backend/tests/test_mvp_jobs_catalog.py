"""Contract checks for the permanent MVP jobs data migration."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "0006_import_mvp_jobs_catalog.py"
_SPEC = spec_from_file_location("mvp_jobs_catalog", _PATH)
assert _SPEC and _SPEC.loader
catalog = module_from_spec(_SPEC)
_SPEC.loader.exec_module(catalog)


def test_catalog_has_all_thirty_source_jobs_and_three_tenants() -> None:
    assert len(catalog.JOBS) == 30
    assert {row[0] for row in catalog.JOBS} == set(catalog.TENANT_DOMAINS)
    assert {row[1] for row in catalog.JOBS} == {f"JOB-{value}" for value in range(1001, 1031)}


def test_catalog_only_uses_existing_poster_roles_and_has_publishable_data() -> None:
    assert {row[9] for row in catalog.JOBS} <= {"hr_manager", "recruiter", "hiring_manager"}
    assert all(row[6] > 0 and row[10] for row in catalog.JOBS)
