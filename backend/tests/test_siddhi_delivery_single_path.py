"""The PRISM PDF has ONE path out of the product, and G4 stands in it.

`siddhi.delivery` had no production caller until PLAN-p5 WP5-D/F, so the PDF
route rendered every report straight through `report_pdf.render_report_pdf`
and gate G4 guarded nothing a client received. The fix is only as good as the
absence of a second path, which is what this asserts:

* `delivery` exposes the gate, its non-raising twin for the payload, and the
  gated renderer, and nothing else (its email body, JSON export and
  attachment helpers were deleted: they had no caller and each was a way out
  that skipped the gate);
* nothing in live code calls the raw renderer except `delivery.prism_pdf`;
* the raw renderer runs the number ban on its own input, so even the one
  sanctioned caller cannot skip it.

The route-level ordering (gate before render) is pinned in
`test_prism_pdf_g4.py`.
"""
from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def test_delivery_exposes_exactly_the_gate_and_the_gated_renderer() -> None:
    from app.services.siddhi import delivery

    assert set(delivery.__all__) == {
        "PDF_BLOCKED_REASON",
        "DeliveryBlocked",
        "DeliveryClearance",
        "gate_delivery",
        "clearance_or_reason",
        "prism_pdf",
    }
    for retired in ("prism_json", "prism_email_body", "prism_attachment", "deliver"):
        assert not hasattr(delivery, retired), retired


def _calls_to(name: str) -> list[str]:
    """Every live call site of a function named `name`, as `file:function`."""
    found: list[str] = []
    for path in APP.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for outer in ast.walk(tree):
            if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(outer):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else None
                )
                if called == name:
                    found.append(f"{path.relative_to(APP).as_posix()}:{outer.name}")
    return found


def test_only_the_gated_renderer_calls_the_raw_one() -> None:
    assert sorted(set(_calls_to("render_report_pdf"))) == [
        "services/siddhi/delivery.py:prism_pdf"
    ]


def test_only_the_report_route_renders_a_pdf() -> None:
    assert sorted(set(_calls_to("prism_pdf"))) == [
        "api/assessment_reports.py:download_report_pdf"
    ]


def test_the_raw_renderer_runs_the_number_ban_on_its_own_input() -> None:
    import inspect

    from app.services import report_pdf

    source = inspect.getsource(report_pdf.render_report_pdf)
    assert "numbers.assert_clean(" in source


def test_a_pdf_cannot_be_rendered_without_a_clearance() -> None:
    import pytest

    from app.services.siddhi import delivery

    with pytest.raises(TypeError):
        delivery.prism_pdf(
            object(),
            {},
            candidate_name="A",
            job_title="B",
            tenant_name="C",
            generated_at=None,
        )
    # A clearance a caller builds itself is refused: only the gate mints one.
    with pytest.raises(TypeError, match="minted"):
        delivery.DeliveryClearance(needed_review=False)
