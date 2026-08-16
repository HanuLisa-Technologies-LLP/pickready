"""Render an immutable PPI Assessment Report as a branded PDF.

ReportLab avoids shipping a browser runtime. Radar charts are rasterized with
Pillow and embedded as PNG images so the permanent record is portable.
"""
from __future__ import annotations

import io
import math
from datetime import datetime
from typing import Any, Iterable
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from PIL import ImageDraw
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FOOTER = "Confidential " + chr(8212) + " Permanent Assessment Record."
PURPLE = colors.HexColor("#5028E0")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#64748B")
PAPER = colors.HexColor("#F7F7FB")


def _value(item: Any, key: str, default: Any = None) -> Any:
    return getattr(
        item,
        key,
        item.get(key, default) if isinstance(item, dict) else default,
    )


def _text(value: Any) -> str:
    return escape(str(value or ""))


def radar_png(chart: Any, *, size: int = 720) -> bytes:
    """Rasterize one qualitative radar chart without displaying coordinates."""
    axes = list(_value(chart, "axes", []))
    image = PILImage.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    center = size / 2
    radius = size * 0.31
    count = max(1, len(axes))

    for ring in range(1, 5):
        points = [
            (
                center
                + radius * ring / 4 * math.cos(-math.pi / 2 + 2 * math.pi * i / count),
                center
                + radius * ring / 4 * math.sin(-math.pi / 2 + 2 * math.pi * i / count),
            )
            for i in range(count)
        ]
        if len(points) >= 3:
            draw.polygon(points, outline="#CBD5E1")

    requirement: list[tuple[float, float]] = []
    candidate: list[tuple[float, float]] = []
    for index, axis in enumerate(axes):
        angle = -math.pi / 2 + 2 * math.pi * index / count
        edge = (
            center + radius * math.cos(angle),
            center + radius * math.sin(angle),
        )
        draw.line((center, center, *edge), fill="#E2E8F0", width=2)
        requirement_index = int(_value(axis, "requirement_index", 1))
        candidate_index = int(_value(axis, "candidate_index", 1))
        requirement.append(
            (
                center + radius * requirement_index / 4 * math.cos(angle),
                center + radius * requirement_index / 4 * math.sin(angle),
            )
        )
        candidate.append(
            (
                center + radius * candidate_index / 4 * math.cos(angle),
                center + radius * candidate_index / 4 * math.sin(angle),
            )
        )
        label = str(_value(axis, "axis", ""))[:24]
        bounds = draw.textbbox((0, 0), label)
        x = center + (radius + 48) * math.cos(angle) - (bounds[2] - bounds[0]) / 2
        y = center + (radius + 48) * math.sin(angle) - (bounds[3] - bounds[1]) / 2
        draw.text((x, y), label, fill="#172033")

    if len(requirement) >= 3:
        draw.polygon(requirement, outline="#64748B", width=5)
        draw.polygon(candidate, outline="#5028E0", width=6)
    draw.rectangle((32, size - 52, 72, size - 42), fill="#64748B")
    draw.text((82, size - 58), "Job Requirement", fill="#172033")
    draw.rectangle((300, size - 52, 340, size - 42), fill="#5028E0")
    draw.text((350, size - 58), "Candidate Assessment", fill="#172033")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#DDDCEB"))
    canvas.line(18 * mm, 13 * mm, 192 * mm, 13 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 8 * mm, FOOTER)
    canvas.drawRightString(192 * mm, 8 * mm, f"Page {document.page}")
    canvas.restoreState()


def _gap_analysis(report, styles) -> list:
    """Gap Analysis & Action Plan (spec 9.6).

    The retired suggested-question payload is retained in storage for schema
    compatibility but is never rendered in a client-facing PDF.
    """
    story: list = []
    gaps = _value(report, "gap_analysis", None) or {}
    groups = _value(gaps, "groups", []) or []
    if not groups:
        return story

    story.append(Paragraph("Gap Analysis & Action Plan", styles["Section"]))
    focus = _value(gaps, "focus_summary", "")
    if focus:
        story.append(Paragraph(f"<b>{_text(focus)}</b>", styles["Body"]))
        story.append(Spacer(1, 3 * mm))

    for group in groups:
        story.append(
            Paragraph(f"<b>{_text(_value(group, 'label', ''))}</b>", styles["Body"])
        )
        cap = _value(group, "cap_statement", None)
        if cap:
            story.append(Paragraph(_text(cap), styles["Body"]))
        items = _value(group, "items", []) or []
        if not items:
            story.append(
                Paragraph(
                    _text(_value(group, "no_gaps_statement", "No gaps identified.")),
                    styles["Body"],
                )
            )
        for item in items:
            story.append(
                Paragraph(
                    f"<b>{_text(_value(item, 'name', ''))}</b>: "
                    f"{_text(_value(item, 'grade', ''))}",
                    styles["Body"],
                )
            )
            remark = _value(item, "remark", None)
            if remark:
                story.append(Paragraph(_text(remark), styles["Body"]))
            for probe in _value(item, "probes", []) or []:
                story.append(Paragraph(f"&bull; {_text(probe)}", styles["Body"]))
            story.append(Spacer(1, 2 * mm))
        story.append(Spacer(1, 3 * mm))
    return story


def _dimension_cards(
    title: str,
    rows: Iterable[Any],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    items = list(rows)
    if not items:
        return []
    cards: list[list[Any]] = []
    for row in items:
        required = _value(row, "required_level")
        label = f"<b>{_text(_value(row, 'name'))}</b> - {_text(_value(row, 'grade'))}"
        if required:
            label += (
                "<br/><font color='#64748B'>Role requires: "
                f"{_text(required)}</font>"
            )
        cards.append(
            [
                Table(
                    [[
                        Paragraph(label, styles["Body"]),
                        Paragraph(_text(_value(row, "remark")), styles["Body"]),
                    ]],
                    colWidths=[52 * mm, 116 * mm],
                    style=TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), PAPER),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDCEB")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 7),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                            ("TOPPADDING", (0, 0), (-1, -1), 7),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ]
                    ),
                ),
                Spacer(1, 2.5 * mm),
            ]
        )
    story: list[Any] = [
        KeepTogether(
            [
                Paragraph(title, styles["Section"]),
                Spacer(1, 2 * mm),
                *cards[0],
            ]
        )
    ]
    story.extend(KeepTogether(card) for card in cards[1:])
    return story


def render_report_pdf(
    report: Any,
    *,
    candidate_name: str,
    job_title: str,
    tenant_name: str,
    generated_at: datetime,
) -> bytes:
    """Return a complete report PDF; no underlying numeric score is rendered."""
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=19 * mm,
        title=f"PPI Assessment Report - {candidate_name}",
        author="ReadyPick",
    )
    base = getSampleStyleSheet()
    styles = {
        "Title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=23,
            leading=28,
            textColor=INK,
            alignment=TA_CENTER,
        ),
        "Subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["BodyText"],
            fontSize=10,
            leading=15,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "Section": ParagraphStyle(
            "Section",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=PURPLE,
            spaceBefore=7 * mm,
        ),
        "Body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontSize=9.5,
            leading=14,
            textColor=INK,
        ),
    }
    story: list[Any] = [
        Paragraph("ReadyPick", styles["Subtitle"]),
        Paragraph("PPI Assessment Report", styles["Title"]),
        Spacer(1, 3 * mm),
        Paragraph(
            f"<b>{_text(candidate_name)}</b><br/>{_text(job_title)}<br/>"
            f"{_text(tenant_name)}<br/>"
            f"{generated_at.strftime('%d %B %Y')}",
            styles["Subtitle"],
        ),
        Spacer(1, 8 * mm),
        Paragraph("Overall Assessment", styles["Section"]),
        Spacer(1, 2 * mm),
        Paragraph(
            f"<b>{_text(_value(report, 'overall_grade'))}</b><br/>"
            f"{_text(_value(report, 'overall_summary'))}",
            styles["Body"],
        ),
    ]

    for chart in list(_value(report, "radar_charts", [])):
        story.append(
            KeepTogether(
                [
                Spacer(1, 3 * mm),
                Paragraph(
                    _text(_value(chart, "title", "Assessment profile")),
                    styles["Body"],
                ),
                Image(io.BytesIO(radar_png(chart)), width=118 * mm, height=118 * mm),
                ]
            )
        )

    story.extend(_dimension_cards("AI Score", _value(report, "ai_score", []), styles))
    story.extend(_dimension_cards("Must-have", _value(report, "must_have", []), styles))
    story.extend(_dimension_cards("Nice-to-have", _value(report, "nice_to_have", []), styles))
    story.extend(_dimension_cards("Behavioural Competencies", _value(report, "behavioural", []), styles))

    # Validation comes BEFORE Gap Analysis (spec 9.3). It is the candidate's own
    # unrated submission and the Gap Analysis is the last word on the report, so
    # this order is not cosmetic: it keeps every rated statement together and
    # ends the document on what to do next.
    story.append(Paragraph("Validation", styles["Section"]))
    validation = _value(report, "validation", {}) or {}
    for field in validation.get("fields", []):
        story.append(
            Paragraph(
                f"<b>{_text(field.get('label', ''))}</b><br/>"
                f"{_text(field.get('value') or 'Not stated')}",
                styles["Body"],
            )
        )
        story.append(Spacer(1, 1.5 * mm))

    story.extend(_gap_analysis(report, styles))

    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()
