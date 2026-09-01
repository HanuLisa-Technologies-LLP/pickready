"""Project Evidence Intelligence: intake guards, parser router, evidence
engine, AI validation, and the processing lifecycle.

Everything here runs without a database or a network. The pipeline tests use
a stub session (the pipeline touches only get/flush/delete) and monkeypatch
the object-store and reasoning ATTRIBUTES on their modules -- never
`sys.modules`, which is the trap the Miti harness fell into.
"""
from __future__ import annotations

import io
import json
import stat
import uuid
import zipfile

import pytest
from fastapi import HTTPException

from app.models.project import (
    STATUS_FAILED_EXTRACTION,
    STATUS_FAILED_SECURITY,
    STATUS_PARTIALLY_PROCESSED,
    STATUS_PERSISTED,
    STATUS_PROCESSED,
    CandidateProject,
)
from app.services.projects import (
    ai_reasoning,
    archive_safety,
    evidence,
    intake,
    parsers,
    pipeline,
    repository,
)
from app.services.projects.formats import classify, is_ignored_path
from app.services.projects.limits import ProjectLimits


def small_limits(**overrides) -> ProjectLimits:
    values = dict(
        max_projects_per_candidate=10,
        max_files=20,
        max_file_bytes=5 * 1024 * 1024,
        max_total_bytes=20 * 1024 * 1024,
        max_archive_depth=1,
        max_archive_entries=50,
        max_extracted_bytes=20 * 1024 * 1024,
        max_compression_ratio=120,
        max_text_chars_per_file=20_000,
        max_evidence_units=40,
        max_ai_context_chars=8_000,
        repo_max_files=10,
        repo_max_file_bytes=100_000,
    )
    values.update(overrides)
    return ProjectLimits(**values)


# ── Description: the hard 100-word maximum ───────────────────────────────────


def test_a_100_word_description_is_accepted() -> None:
    text = " ".join(f"word{i}" for i in range(100))
    assert intake.validate_description(text, small_limits()) == text


def test_a_101_word_description_is_refused() -> None:
    text = " ".join(f"word{i}" for i in range(101))
    with pytest.raises(HTTPException) as excinfo:
        intake.validate_description(text, small_limits())
    assert excinfo.value.status_code == 422
    assert "100 words" in str(excinfo.value.detail)


def test_an_empty_description_is_refused() -> None:
    with pytest.raises(HTTPException):
        intake.validate_description("   ", small_limits())


# ── Repository URL validation: public only, providers as a registry ──────────


def test_a_public_github_url_is_accepted_and_normalised() -> None:
    ref = repository.validate_repository_url("https://github.com/owner/proj.git")
    assert (ref.provider, ref.owner, ref.name) == ("github", "owner", "proj")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/proj",           # not https
        "https://user:token@github.com/o/p",      # embedded credentials
        "https://example.com/owner/proj",         # unsupported host
        "https://github.com/ownerless",           # no repository segment
        "",
    ],
)
def test_bad_repository_urls_are_refused(url: str) -> None:
    with pytest.raises(repository.RepositoryRejected):
        repository.validate_repository_url(url)


def test_tree_selection_prioritises_meaning_and_drops_generated() -> None:
    entries = [
        {"type": "blob", "path": "node_modules/react/index.js", "size": 10},
        {"type": "blob", "path": "src/app.py", "size": 100},
        {"type": "blob", "path": "README.md", "size": 100},
        {"type": "blob", "path": "package.json", "size": 100},
        {"type": "blob", "path": "data/huge.py", "size": 10_000_000},
        {"type": "blob", "path": "logo.png", "size": 10},
        {"type": "tree", "path": "src"},
    ]
    chosen, ignored, oversize = repository.select_tree_paths(entries, small_limits())
    paths = [e["path"] for e in chosen]
    assert paths[0] == "README.md"
    assert paths[1] == "package.json"
    assert "node_modules/react/index.js" not in paths
    assert "logo.png" not in paths
    assert "data/huge.py" not in paths
    assert oversize == 1
    assert ignored == 2


# ── Archive safety ───────────────────────────────────────────────────────────


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_traversal_entry_poisons_the_whole_archive() -> None:
    data = _zip_bytes({"../evil.txt": b"x", "ok.txt": b"y"})
    with pytest.raises(archive_safety.ArchiveRejected):
        archive_safety.inspect(data, small_limits())


def test_a_symlink_entry_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("innocent")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(archive_safety.ArchiveRejected):
        archive_safety.inspect(buffer.getvalue(), small_limits())


def test_a_decompression_bomb_is_refused_before_extraction() -> None:
    data = _zip_bytes({"bomb.txt": b"\x00" * (60 * 1024 * 1024)})
    with pytest.raises(archive_safety.ArchiveRejected) as excinfo:
        archive_safety.inspect(data, small_limits())
    assert "compression" in excinfo.value.reason or "limit" in excinfo.value.reason


def test_too_many_entries_are_refused() -> None:
    data = _zip_bytes({f"f{i}.txt": b"hello world" for i in range(60)})
    with pytest.raises(archive_safety.ArchiveRejected):
        archive_safety.inspect(data, small_limits())


def test_nesting_past_the_depth_limit_is_refused() -> None:
    inner = _zip_bytes({"deep.txt": b"hello"})
    middle = _zip_bytes({"middle.zip": inner})
    outer = _zip_bytes({"outer.zip": middle})
    with pytest.raises(archive_safety.ArchiveRejected):
        archive_safety.extract(outer, small_limits())


def test_a_clean_archive_extracts_and_drops_generated_directories() -> None:
    data = _zip_bytes(
        {
            "src/main.py": b"print('hi')",
            "node_modules/lib/index.js": b"module.exports = {}",
        }
    )
    extracted = archive_safety.extract(data, small_limits())
    assert [member.path for member in extracted] == ["src/main.py"]


# ── Classification + parser router ───────────────────────────────────────────


def test_classification_covers_the_families() -> None:
    assert classify("api/server.py").family == "source_code"
    assert classify("design.ifc").family == "cad"
    assert classify("report.pdf").family == "document"
    assert classify("Dockerfile").family == "manifest"
    assert classify("model.exe").supported is False
    rar = classify("project.rar")
    assert rar.supported is False and rar.limitation


def test_a_bare_readme_is_a_supported_document() -> None:
    """Found live: octocat/Hello-World is one extensionless `README`, and it
    classified as nothing-extractable before DOCUMENT_FILENAMES existed."""
    cls = classify("README")
    assert cls.family == "document"
    assert cls.supported is True
    assert classify("LICENSE").supported is True


def test_generated_paths_are_ignored() -> None:
    assert is_ignored_path("node_modules/react/index.js")
    assert is_ignored_path("app/__pycache__/x.pyc")
    assert not is_ignored_path("src/components/app.tsx")


def test_python_source_signals_are_deterministic() -> None:
    code = (
        "import flask\nfrom sqlalchemy import select\n\n"
        "@app.route('/health')\ndef health():\n"
        "    try:\n        return query()\n    except ValueError:\n        raise\n"
    )
    artifact = parsers.parse_file("src/app.py", code.encode(), small_limits())
    assert artifact.supported
    assert artifact.signals["language"] == "Python"
    assert "flask" in artifact.signals["imports"]
    assert "Flask" in artifact.signals["technologies"]
    assert artifact.signals["has_routes"] is True
    assert artifact.signals["has_error_handling"] is True
    assert artifact.signals["is_test"] is False


def test_test_files_are_recognised_by_path() -> None:
    artifact = parsers.parse_file(
        "tests/test_app.py", b"def test_ok():\n    assert True\n", small_limits()
    )
    assert artifact.signals["is_test"] is True


def test_package_json_yields_technology_evidence() -> None:
    manifest = json.dumps(
        {"dependencies": {"react": "^18", "pg": "^8"}, "devDependencies": {"jest": "^29"}}
    )
    artifact = parsers.parse_file("package.json", manifest.encode(), small_limits())
    assert set(artifact.signals["technologies"]) >= {"React", "PostgreSQL", "Jest"}


def test_a_corrupt_pdf_degrades_to_a_recorded_limitation() -> None:
    artifact = parsers.parse_file("report.pdf", b"%PDF-not really", small_limits())
    assert artifact.supported is False
    assert artifact.limitation


def test_ifc_entities_are_counted_deterministically() -> None:
    ifc = (
        "ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
        "#1=IFCWALL('a',$,$,$,$,$,$,$);\n#2=IFCWALL('b',$,$,$,$,$,$,$);\n"
        "#3=IFCDOOR('c',$,$,$,$,$,$,$);\nENDSEC;\n"
    )
    artifact = parsers.parse_file("building.ifc", ifc.encode(), small_limits())
    assert artifact.signals["entity_counts"]["IFCWALL"] == 2
    assert artifact.signals["schema"] == "IFC4"


def test_ascii_stl_reports_its_facets() -> None:
    stl = "solid bracket\nfacet normal 0 0 1\nendfacet\nfacet normal 0 0 1\nendfacet\nendsolid"
    artifact = parsers.parse_file("bracket.stl", stl.encode(), small_limits())
    assert artifact.signals["facet_count"] == 2


# ── Evidence engine ──────────────────────────────────────────────────────────


def _software_artifacts() -> list[parsers.ParsedArtifact]:
    limits = small_limits()
    manifest = json.dumps({"dependencies": {"react": "^18"}})
    return [
        parsers.parse_file("package.json", manifest.encode(), limits),
        parsers.parse_file("src/a.jsx", b"import react from 'react'\n", limits),
        parsers.parse_file("src/b.jsx", b"import react from 'react'\n", limits),
    ]


def test_units_are_deduplicated_across_files() -> None:
    units = evidence.build_units(_software_artifacts(), small_limits())
    react_units = [u for u in units if u.statement == "React in use"]
    assert len(react_units) == 1


def test_units_are_capped_by_the_reduction_limit() -> None:
    limits = small_limits(max_evidence_units=3)
    units = evidence.build_units(_software_artifacts(), limits)
    assert len(units) <= 3


def test_claims_and_observations_stay_separate() -> None:
    artifacts = _software_artifacts()
    units = evidence.build_units(artifacts, small_limits())
    record = evidence.build_evidence_record(
        project_name="Shop",
        candidate_description="Built a scalable microservices platform.",
        artifacts=artifacts,
        units=units,
        submission_kind="files",
    )
    assert (
        record["candidate_claims"]["description"]
        == "Built a scalable microservices platform."
    )
    # The claim text never leaks into observed evidence.
    for section in ("architecture", "implementation", "testing", "infrastructure"):
        for unit in record[section]:
            assert "microservices platform" not in unit["statement"]


def test_absence_gaps_are_stated_for_source_projects_only() -> None:
    artifacts = _software_artifacts()
    units = evidence.build_units(artifacts, small_limits())
    record = evidence.build_evidence_record(
        project_name="Shop",
        candidate_description="d",
        artifacts=artifacts,
        units=units,
        submission_kind="files",
    )
    assert any("test code" in gap.lower() for gap in record["potential_gaps"])

    cad = [parsers.parse_file("part.stl", b"solid p\nendsolid", small_limits())]
    cad_units = evidence.build_units(cad, small_limits())
    cad_record = evidence.build_evidence_record(
        project_name="Part",
        candidate_description="d",
        artifacts=cad,
        units=cad_units,
        submission_kind="files",
    )
    assert not any("test code" in gap.lower() for gap in cad_record["potential_gaps"])


def test_every_unit_carries_provenance() -> None:
    units = evidence.build_units(_software_artifacts(), small_limits())
    assert units
    for unit in units:
        assert unit.source_path


def test_the_evidence_pack_respects_the_context_ceiling() -> None:
    limits = small_limits(max_ai_context_chars=600)
    artifacts = _software_artifacts()
    units = evidence.build_units(artifacts, small_limits())
    record = evidence.build_evidence_record(
        project_name="Shop",
        candidate_description="Built things.",
        artifacts=artifacts,
        units=units,
        submission_kind="files",
    )
    pack = evidence.build_evidence_pack(record, units, limits)
    assert len(pack) <= 600
    assert "PROJECT EVIDENCE PACK" in pack


# ── AI interpretation validation ─────────────────────────────────────────────


def _valid_interpretation() -> dict:
    return {
        "claim_assessments": [
            {
                "claim": "Implemented JWT authentication",
                "supporting_evidence": ["Authentication or authorization logic"],
                "limiting_evidence": [],
                "assessment": "strongly supported",
            }
        ],
        "synthesis": "The project demonstrates working full-stack implementation.",
        "meaningful_gaps": ["No deployment evidence."],
        "validation_areas": ["Ask how sessions are invalidated."],
        "evidence_strength": "Moderate",
    }


def test_a_valid_interpretation_passes() -> None:
    result = ai_reasoning.validate_interpretation(_valid_interpretation())
    assert result["evidence_strength"] == "Moderate"


def test_a_strength_outside_the_vocabulary_is_refused() -> None:
    payload = _valid_interpretation()
    payload["evidence_strength"] = "Excellent"
    with pytest.raises(ai_reasoning.ProjectReasoningError):
        ai_reasoning.validate_interpretation(payload)


def test_a_careless_assessment_label_is_refused() -> None:
    payload = _valid_interpretation()
    payload["claim_assessments"][0]["assessment"] = "candidate lied"
    with pytest.raises(ai_reasoning.ProjectReasoningError):
        ai_reasoning.validate_interpretation(payload)


def test_a_rating_shaped_number_is_refused() -> None:
    payload = _valid_interpretation()
    payload["synthesis"] = "A solid 8/10 project overall."
    with pytest.raises(ai_reasoning.ProjectReasoningError):
        ai_reasoning.validate_interpretation(payload)


def test_the_strength_vocabulary_matches_the_database_check() -> None:
    # The migration's CHECK enumerates these words; a drift here would let the
    # service write a row the database refuses.
    assert ai_reasoning.STRENGTH_VOCABULARY == {
        "Strong",
        "Moderate",
        "Limited",
        "Insufficient",
    }


# ── The pipeline lifecycle ───────────────────────────────────────────────────


class FakeSession:
    """The pipeline touches exactly get/flush/delete; nothing else exists to
    touch, so a stub is honest."""

    def __init__(self, project: CandidateProject) -> None:
        self.project = project
        self.deleted: list[object] = []

    async def get(self, model, pk):
        return self.project if pk == self.project.id else None

    async def flush(self) -> None:
        return None

    async def delete(self, obj) -> None:
        self.deleted.append(obj)


class FakeStore:
    """In-memory object store patched over the real transport's attributes."""

    def __init__(self, objects: dict[str, bytes], *, refuse_delete: bool = False) -> None:
        self.objects = dict(objects)
        self.refuse_delete = refuse_delete

    def get_bytes(self, key: str) -> bytes:
        from app.services.object_storage import ObjectStorageError

        if key not in self.objects:
            raise ObjectStorageError("missing")
        return self.objects[key]

    def delete(self, key: str) -> None:
        if not self.refuse_delete:
            self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects


def _files_project() -> CandidateProject:
    project = CandidateProject(
        candidate_id=uuid.uuid4(),
        name="Garage System",
        description="Built a Django garage management system with tests.",
        submission_kind="files",
        status="submitted",
        intake_objects_json=[{"key": "project-intake/p/000-abc", "filename": "app.py"}],
        files_json=[{"filename": "app.py"}],
    )
    project.id = uuid.uuid4()
    project.deletion_attempts = 0
    return project


def _patch_store(monkeypatch: pytest.MonkeyPatch, store: FakeStore) -> None:
    from app.services import object_storage

    monkeypatch.setattr(object_storage, "get_bytes", store.get_bytes)
    monkeypatch.setattr(object_storage, "delete", store.delete)
    monkeypatch.setattr(object_storage, "exists", store.exists)


def _patch_ai(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
    async def fake_interpret(pack: str) -> dict:
        if fail:
            raise ai_reasoning.ProjectReasoningError("no")
        assert "PROJECT EVIDENCE PACK" in pack
        return ai_reasoning.validate_interpretation(_valid_interpretation())

    monkeypatch.setattr(ai_reasoning, "interpret", fake_interpret)


@pytest.mark.asyncio
async def test_happy_path_persists_evidence_then_deletes_the_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    store = FakeStore(
        {"project-intake/p/000-abc": b"import django\nfrom django.db import models\n"}
    )
    _patch_store(monkeypatch, store)
    _patch_ai(monkeypatch)
    session = FakeSession(project)

    result = await pipeline.process_project(session, project.id, limits=small_limits())

    assert result.status == STATUS_PROCESSED
    assert result.evidence_json is not None
    assert result.evidence_units_json
    assert result.ai_interpretation_json is not None
    assert result.evidence_strength == "Moderate"
    assert result.original_deleted_at is not None
    assert result.intake_objects_json == []
    assert store.objects == {}, "the temporary original must actually be gone"
    telemetry = result.telemetry_json
    assert telemetry["ai_status"] == "completed"
    assert telemetry["file_count"] == 1
    assert telemetry["ai_context_chars"] > 0


@pytest.mark.asyncio
async def test_a_failed_deletion_is_recorded_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    store = FakeStore(
        {"project-intake/p/000-abc": b"import django\n"}, refuse_delete=True
    )
    _patch_store(monkeypatch, store)
    _patch_ai(monkeypatch)
    session = FakeSession(project)

    result = await pipeline.process_project(session, project.id, limits=small_limits())

    # Evidence is durable, deletion is NOT claimed.
    assert result.status == STATUS_PERSISTED
    assert result.original_deleted_at is None
    assert result.deletion_attempts == 1
    assert result.intake_objects_json

    # The sweeper's retry succeeds once the store cooperates.
    store.refuse_delete = False
    done = await pipeline.delete_intake_objects(session, result)
    assert done is True
    assert result.status == STATUS_PROCESSED
    assert result.original_deleted_at is not None


@pytest.mark.asyncio
async def test_ai_failure_keeps_deterministic_evidence_as_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    store = FakeStore({"project-intake/p/000-abc": b"import django\n"})
    _patch_store(monkeypatch, store)
    _patch_ai(monkeypatch, fail=True)
    session = FakeSession(project)

    result = await pipeline.process_project(session, project.id, limits=small_limits())

    assert result.status == STATUS_PARTIALLY_PROCESSED
    assert result.evidence_json is not None
    assert result.ai_interpretation_json is None
    assert result.evidence_strength is None
    # Deterministic evidence is durable, so the original still goes.
    assert result.original_deleted_at is not None


@pytest.mark.asyncio
async def test_a_partial_project_completes_via_the_ai_only_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    store = FakeStore({"project-intake/p/000-abc": b"import django\n"})
    _patch_store(monkeypatch, store)
    _patch_ai(monkeypatch, fail=True)
    session = FakeSession(project)
    await pipeline.process_project(session, project.id, limits=small_limits())
    assert project.status == STATUS_PARTIALLY_PROCESSED

    # Second run: originals are gone, so only the interpretation reruns.
    _patch_ai(monkeypatch, fail=False)

    def must_not_fetch(key: str) -> bytes:
        raise AssertionError("the AI-only path must not touch storage")

    from app.services import object_storage

    monkeypatch.setattr(object_storage, "get_bytes", must_not_fetch)
    result = await pipeline.process_project(session, project.id, limits=small_limits())
    assert result.status == STATUS_PROCESSED
    assert result.ai_interpretation_json is not None


@pytest.mark.asyncio
async def test_a_hostile_archive_fails_security_and_keeps_the_staged_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    project.intake_objects_json = [
        {"key": "project-intake/p/000-abc", "filename": "proj.zip"}
    ]
    hostile = _zip_bytes({"../escape.txt": b"x"})
    store = FakeStore({"project-intake/p/000-abc": hostile})
    _patch_store(monkeypatch, store)
    session = FakeSession(project)

    result = await pipeline.process_project(session, project.id, limits=small_limits())

    assert result.status == STATUS_FAILED_SECURITY
    assert result.failure_code == "archive_rejected"
    assert result.evidence_json is None
    # Nothing was persisted, so nothing may be deleted: the staged copy stays
    # for the retry the candidate is offered.
    assert store.objects


@pytest.mark.asyncio
async def test_an_unreadable_submission_is_failed_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    store = FakeStore({})  # the staged object vanished
    _patch_store(monkeypatch, store)
    session = FakeSession(project)

    result = await pipeline.process_project(session, project.id, limits=small_limits())
    assert result.status == STATUS_FAILED_EXTRACTION
    assert result.failure_code == "nothing_extractable"


@pytest.mark.asyncio
async def test_reprocessing_a_completed_project_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    store = FakeStore({"project-intake/p/000-abc": b"import django\n"})
    _patch_store(monkeypatch, store)
    _patch_ai(monkeypatch)
    session = FakeSession(project)
    first = await pipeline.process_project(session, project.id, limits=small_limits())
    snapshot = dict(first.telemetry_json)

    second = await pipeline.process_project(session, project.id, limits=small_limits())
    assert second.status == STATUS_PROCESSED
    assert second.telemetry_json == snapshot, "a rerun of a completed project does nothing"


@pytest.mark.asyncio
async def test_discard_removes_row_and_staged_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _files_project()
    store = FakeStore({"project-intake/p/000-abc": b"data"})
    _patch_store(monkeypatch, store)
    session = FakeSession(project)

    await pipeline.discard_project(session, project)
    assert session.deleted == [project]
    assert store.objects == {}


# ── The recruiter shape: words, never numbers ────────────────────────────────


def test_recruiter_view_reports_strength_as_a_word() -> None:
    from app.services.projects import context

    project = _files_project()
    project.status = STATUS_PROCESSED
    project.evidence_json = {
        "project_identity": {"domains": ["Software engineering"]},
        "technology_stack": {"technologies": ["Django"], "languages": {"Python": 3}},
        "potential_gaps": ["No test code was found in the submitted material."],
        "uncertainties": [],
    }
    project.evidence_units_json = [
        {"unit_type": "implementation", "statement": "Database access logic",
         "source_path": "app.py"},
    ]
    project.ai_interpretation_json = ai_reasoning.validate_interpretation(
        _valid_interpretation()
    )
    project.evidence_strength = "Moderate"

    view = context.recruiter_view(project)
    assert view["evidence_strength"] == "Moderate"
    assert view["candidate_description"] == project.description
    assert "Database access logic" in view["observed_evidence"]
    # No rating-shaped value anywhere in the recruiter payload.
    assert not any(
        isinstance(value, (int, float)) for value in view.values()
    ), "no bare number crosses the recruiter boundary"


def test_the_task_type_is_registered_on_the_reasoning_tier() -> None:
    from app.config import llm_providers

    assert llm_providers.model_for("project_evidence") == llm_providers.MODEL_TERRA
    assert llm_providers.temperature_for("project_evidence") == 0.0
