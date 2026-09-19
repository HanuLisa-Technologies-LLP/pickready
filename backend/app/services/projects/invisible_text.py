"""Deterministic invisible-content detection at intake (RPN-AI-UP-001 W9.2).

WHY THIS EXISTS, WITH A MEASURED BASE RATE
-------------------------------------------
An analysis of 200,000 real resumes found roughly one percent carrying prompt
injection attempts, and the rate rose sevenfold between July 2024 and November
2025. At that prevalence a databank bulk upload of twenty five resumes has a
meaningful chance of carrying one. This is not a hypothetical attacker, and the
carrier is almost always text a human reader cannot see: white on white, two
point type, a zero width run, a text object painted in the invisible render
mode, a paragraph pushed off the page.

WHY IT IS DETERMINISTIC AND LIVES IN THE PARSER
------------------------------------------------
Same argument `conversation_guardrails` and `answer_quality` make: the moment a
model provider is degraded is the moment a guard matters most, and a guard that
needs the provider is absent precisely then. Every function here is a pure
function of its arguments. No model, no database, no network, and no candidate
file is ever executed.

THE THREE RULES ON WHAT A HIT MEANS
-------------------------------------
1. It is RECORDED AS PROVENANCE, never silently stripped. A resume with hidden
   instructions is signal about the submission, and `IntakeScan.as_json()` is
   what lands on the row. It mirrors `archive_safety`, which poisons an archive
   as `failed_security` rather than raising.
2. It NEVER auto rejects. Nothing in this module raises, nothing returns a
   decision, and there is no reject field to read. The standing no-auto-reject
   rule and basic fairness agree, and the legal dimension is real: a manipulated
   screen that advances an unqualified candidate or buries a qualified one is a
   discrimination liability event if the decision is later challenged, and an
   audit trail gap makes it indefensible. The recorded limitation IS the audit
   trail.
3. The model sees `IntakeScan.normalised_text`, never the raw file and never
   the raw extracted string. Normalising without recording would be the silent
   strip rule 1 forbids; recording without normalising would leave the payload
   in the prompt.

WHAT IT CANNOT SEE, STATED IN THE CODE
----------------------------------------
"A mismatch between rendered glyphs and extracted text" is only fully decidable
by rasterising the page, which would mean running a renderer over a hostile
file. This product does not do that. What is decidable without a renderer is
text that is EXTRACTED BUT NEVER PAINTED: PDF text render modes 3 and 7, and
the DOCX `vanish` run property. Those are the forms the technique actually
takes in the wild, and `TECHNIQUE_UNRENDERED_TEXT` is named for what is
detected rather than for what it stands in for. A homoglyph substitution inside
a poisoned ToUnicode CMap is NOT detected, and no wording here implies it is.

WHY THE UNICODE CLASSES ARE VENDORED
-------------------------------------
LLM Guard's `InvisibleText` scanner covers part of this and its last release was
May 2025. A security control on the intake path of every resume in the product
is not a place for an unmaintained dependency, so the classes are stated here,
built from `chr(...)` rather than written as literals so the file itself never
carries the characters it is looking for.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

# ── Technique names (the vocabulary the row records) ─────────────────────────

TECHNIQUE_ZERO_WIDTH = "zero_width_characters"
TECHNIQUE_BIDI_CONTROL = "bidirectional_control_characters"
TECHNIQUE_TAG_CHARACTERS = "unicode_tag_characters"
TECHNIQUE_LOW_CONTRAST = "text_matching_the_background_colour"
TECHNIQUE_TINY_TYPE = "text_below_the_legible_size_floor"
TECHNIQUE_OFF_CANVAS = "text_positioned_outside_the_page"
TECHNIQUE_UNRENDERED_TEXT = "text_extracted_but_never_painted"

#: Every technique this module can report. A test pins it, so a technique added
#: without a phrasing entry fails the build rather than rendering as a raw
#: identifier in front of a recruiter.
ALL_TECHNIQUES: tuple[str, ...] = (
    TECHNIQUE_ZERO_WIDTH,
    TECHNIQUE_BIDI_CONTROL,
    TECHNIQUE_TAG_CHARACTERS,
    TECHNIQUE_LOW_CONTRAST,
    TECHNIQUE_TINY_TYPE,
    TECHNIQUE_OFF_CANVAS,
    TECHNIQUE_UNRENDERED_TEXT,
)

#: Recruiter-facing phrasing, one fixed sentence fragment per technique. A
#: fixed catalogue, never a prompt, for the same reason `candidate_updates`
#: uses one: this surface exists BECAUSE something else may be unreliable.
TECHNIQUE_PHRASING: dict[str, str] = {
    TECHNIQUE_ZERO_WIDTH: "characters that occupy no width",
    TECHNIQUE_BIDI_CONTROL: "characters that reorder how text reads",
    TECHNIQUE_TAG_CHARACTERS: "characters from a block that renders as nothing",
    TECHNIQUE_LOW_CONTRAST: "text in a colour that matches its background",
    TECHNIQUE_TINY_TYPE: "text too small to read on the page",
    TECHNIQUE_OFF_CANVAS: "text placed outside the visible page area",
    TECHNIQUE_UNRENDERED_TEXT: "text that is stored in the file but never drawn",
}


# ── Ceilings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InvisibleTextLimits:
    """Every ceiling this module enforces, in one place.

    PROVENANCE OF THE VALUES BELOW. `core/config.py` is owned by another
    workstream in this release and the `invisible_text_*` settings are reported
    with this change rather than added here. `LIMITS` is therefore the single
    statement of each value; when the settings land, this dataclass gains a
    `from_settings()` exactly like `projects/limits.py` has and the call sites
    are unchanged, because every one of them already takes the dataclass.
    """

    #: Zero width characters below this count in a Latin context are counted
    #: and normalised away but do not flag the document. One stray ZWSP is what
    #: pasting from a web page produces; a run of them is a channel.
    zero_width_flag_threshold: int
    #: WCAG relative contrast ratio. 1.0 is identical colours; below this the
    #: text is not readable by a human at any size.
    min_contrast_ratio: float
    #: Points. Below this a glyph is a dot on a printed page.
    min_font_size_points: float
    #: Points a text object may sit outside the page box before it is called
    #: off canvas. Non-zero because glyph origins legitimately sit a hair
    #: outside a tight crop box.
    off_canvas_tolerance_points: float
    #: Pages inspected per PDF. The structural walk is cheap but not free, and
    #: a hostile file may declare thousands of pages.
    max_pages_scanned: int
    #: Text-showing operations inspected per page.
    max_text_operations_per_page: int
    #: DOCX runs inspected per document.
    max_runs_scanned: int
    #: Characters of a per-finding sample kept on the row. Provenance, not
    #: content: enough to recognise the payload, never the whole of it.
    max_sample_chars: int


#: The one place the numbers are stated. See `InvisibleTextLimits`.
LIMITS = InvisibleTextLimits(
    zero_width_flag_threshold=3,
    min_contrast_ratio=1.5,
    min_font_size_points=4.0,
    off_canvas_tolerance_points=2.0,
    max_pages_scanned=40,
    max_text_operations_per_page=4000,
    max_runs_scanned=5000,
    max_sample_chars=160,
)


# ── The character classes, built rather than written ─────────────────────────
#
# Written as `chr(...)` on purpose: a source file carrying the characters it
# detects is a file no reviewer can read and no repository-wide sweep can
# safely rewrite. Same reasoning the em dash stripper follows.

#: Width-less characters that are never meaningful between Latin letters.
_ZERO_WIDTH_CODEPOINTS: frozenset[int] = frozenset(
    {
        0x200B,  # zero width space
        0x2060,  # word joiner
        0x2061,  # function application
        0x2062,  # invisible times
        0x2063,  # invisible separator
        0x2064,  # invisible plus
        0xFEFF,  # zero width no-break space
        0x180E,  # mongolian vowel separator
        0x00AD,  # soft hyphen
        0x034F,  # combining grapheme joiner
        0x115F,  # hangul choseong filler
        0x1160,  # hangul jungseong filler
        0x17B4,  # khmer vowel inherent aq
        0x17B5,  # khmer vowel inherent aa
        0x3164,  # hangul filler
        0xFFA0,  # halfwidth hangul filler
    }
)

#: ZWJ and ZWNJ are LEGITIMATE in Indic scripts and in emoji sequences, and
#: this product's candidates write Devanagari names. They are counted only
#: between Latin characters, where they carry no typographic meaning at all.
_CONTEXTUAL_ZERO_WIDTH: frozenset[int] = frozenset({0x200C, 0x200D})

#: Directional formatting. An RLO run is the classic way to make extracted text
#: read differently from painted text.
_BIDI_CODEPOINTS: frozenset[int] = frozenset(
    {0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
     0x2066, 0x2067, 0x2068, 0x2069}
)

#: The Unicode Tags block. Deprecated for language tagging, renders as nothing,
#: and is the standard carrier for smuggling a full ASCII instruction inside
#: what looks like a single word.
_TAG_RANGE = range(0xE0000, 0xE0080)

_LATIN = re.compile(r"[A-Za-z0-9]")


def _is_tag(codepoint: int) -> bool:
    return codepoint in _TAG_RANGE


# ── Findings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    """One detection. `occurrences` is a count, `sample` is bounded evidence."""

    technique: str
    occurrences: int
    detail: str
    sample: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "technique": self.technique,
            "occurrences": self.occurrences,
            "detail": self.detail,
            "sample": self.sample,
        }


@dataclass(frozen=True)
class IntakeScan:
    """The result of scanning one document.

    There is deliberately no `rejected`, no `blocked` and no `score`. The only
    outputs are what was found, the text the model may see, and a sentence a
    human can read.
    """

    findings: tuple[Finding, ...] = ()
    normalised_text: str = ""
    #: Named reason the structural half could not run (an encrypted PDF, a
    #: DOCX that is not a DOCX). Stated, never swallowed.
    scan_limitation: str | None = None

    @property
    def flagged(self) -> bool:
        return bool(self.findings)

    @property
    def techniques(self) -> tuple[str, ...]:
        seen: list[str] = []
        for finding in self.findings:
            if finding.technique not in seen:
                seen.append(finding.technique)
        return tuple(seen)

    def limitation(self) -> str | None:
        """The recorded limitation. Words only, no counts, no accusation.

        Phrased as a fact about the FILE, not a judgement about the person: the
        candidate may have downloaded a template that carried this, and the
        sentence a recruiter reads must not decide that for them.
        """
        if not self.flagged:
            return None
        phrases = [TECHNIQUE_PHRASING[name] for name in self.techniques]
        if len(phrases) == 1:
            listed = phrases[0]
        else:
            listed = ", ".join(phrases[:-1]) + " and " + phrases[-1]
        return (
            "This document contains content a person reading it would not see: "
            f"{listed}. The visible content was used for assessment and the "
            "hidden content was set aside. A human should review the file."
        )

    def as_json(self) -> dict[str, Any]:
        """The provenance record persisted on the row."""
        return {
            "flagged": self.flagged,
            "techniques": list(self.techniques),
            "findings": [finding.as_json() for finding in self.findings],
            "limitation": self.limitation(),
            "scan_limitation": self.scan_limitation,
        }


# ── The Unicode pass ─────────────────────────────────────────────────────────


def _sample(text: str, limits: InvisibleTextLimits) -> str:
    """A bounded, printable-only excerpt kept as evidence of the payload."""
    printable = "".join(
        char for char in text if char.isprintable() and not _is_invisible(char)
    )
    return " ".join(printable.split())[: limits.max_sample_chars]


def _is_invisible(char: str) -> bool:
    codepoint = ord(char)
    return (
        codepoint in _ZERO_WIDTH_CODEPOINTS
        or codepoint in _CONTEXTUAL_ZERO_WIDTH
        or codepoint in _BIDI_CODEPOINTS
        or _is_tag(codepoint)
    )


def scan_text(text: str, limits: InvisibleTextLimits = LIMITS) -> IntakeScan:
    """Detect invisible Unicode in already-extracted text and normalise it.

    Every character reported here is also removed from `normalised_text`, and
    every character removed is reported. The two must not come apart: one half
    alone is either a silent strip or an unremoved payload.
    """
    source = text or ""
    zero_width = 0
    contextual = 0
    bidi = 0
    tags: list[str] = []
    kept: list[str] = []

    for index, char in enumerate(source):
        codepoint = ord(char)
        if codepoint in _ZERO_WIDTH_CODEPOINTS:
            zero_width += 1
            continue
        if codepoint in _CONTEXTUAL_ZERO_WIDTH:
            before = source[index - 1] if index else ""
            after = source[index + 1] if index + 1 < len(source) else ""
            if _LATIN.fullmatch(before or " ") and _LATIN.fullmatch(after or " "):
                contextual += 1
                continue
            # Legitimate in an Indic cluster or an emoji sequence: it is not a
            # finding and it is not removed either, because removing it would
            # change how the candidate's own name renders.
            kept.append(char)
            continue
        if codepoint in _BIDI_CODEPOINTS:
            bidi += 1
            continue
        if _is_tag(codepoint):
            # The tags block maps one for one onto ASCII: recovering the
            # smuggled string is what makes the record worth reading.
            tags.append(chr(codepoint - 0xE0000))
            continue
        kept.append(char)

    findings: list[Finding] = []
    total_zero_width = zero_width + contextual
    if total_zero_width >= limits.zero_width_flag_threshold:
        findings.append(
            Finding(
                technique=TECHNIQUE_ZERO_WIDTH,
                occurrences=total_zero_width,
                detail=(
                    "The extracted text carries characters that render at zero "
                    "width, which a reader of the document cannot see."
                ),
            )
        )
    if bidi:
        findings.append(
            Finding(
                technique=TECHNIQUE_BIDI_CONTROL,
                occurrences=bidi,
                detail=(
                    "The extracted text carries directional control characters, "
                    "which can make the painted order differ from the stored "
                    "order."
                ),
            )
        )
    if tags:
        findings.append(
            Finding(
                technique=TECHNIQUE_TAG_CHARACTERS,
                occurrences=len(tags),
                detail=(
                    "The extracted text carries characters from the Unicode "
                    "tags block, which render as nothing and decode to "
                    "ordinary text."
                ),
                sample=_sample("".join(tags), limits),
            )
        )
    return IntakeScan(findings=tuple(findings), normalised_text="".join(kept))


# ── Colour arithmetic (WCAG relative luminance) ──────────────────────────────


def _channel(value: float) -> float:
    value = min(max(value, 0.0), 1.0)
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG 2.x relative luminance for a 0..1 RGB triple."""
    red, green, blue = (_channel(component) for component in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> float:
    """WCAG contrast ratio between two colours. 1.0 means identical."""
    lighter = max(relative_luminance(first), relative_luminance(second))
    darker = min(relative_luminance(first), relative_luminance(second))
    return (lighter + 0.05) / (darker + 0.05)


def _cmyk_to_rgb(cyan: float, magenta: float, yellow: float, black: float):
    return (
        (1.0 - min(1.0, cyan + black)),
        (1.0 - min(1.0, magenta + black)),
        (1.0 - min(1.0, yellow + black)),
    )


# ── The PDF structural pass ──────────────────────────────────────────────────
#
# Two independent walks over each page, because they answer different questions
# and the cheap one must not depend on the expensive one:
#
#   * the CONTENT STREAM walk sees colour and render mode, which pypdf's text
#     extractor does not expose;
#   * the EXTRACTION VISITOR sees the text matrix and the font size, which the
#     raw operator stream only gives after reimplementing text state.

#: PDF text render modes that paint nothing. 3 is "invisible" (the mode a
#: scanner's OCR layer legitimately uses, which is why a hit is recorded rather
#: than treated as proof of intent); 7 is "add to clipping path only".
_UNPAINTED_RENDER_MODES = frozenset({3, 7})

_WHITE: tuple[float, float, float] = (1.0, 1.0, 1.0)


def _text_operand_length(operands: list[Any]) -> int:
    """Characters shown by one text-showing operator."""
    total = 0
    for operand in operands:
        if isinstance(operand, (bytes, str)):
            total += len(operand)
        elif isinstance(operand, list):
            total += sum(
                len(item) for item in operand if isinstance(item, (bytes, str))
            )
    return total


def _numeric(operands: list[Any], count: int) -> tuple[float, ...] | None:
    if len(operands) < count:
        return None
    try:
        return tuple(float(value) for value in operands[-count:])
    except (TypeError, ValueError):
        return None


def _scan_pdf_page_stream(
    page: Any, reader: Any, limits: InvisibleTextLimits
) -> tuple[int, int, str, str]:
    """Walk one page's operators.

    Returns (characters painted at low contrast, characters never painted,
    low-contrast sample, unpainted sample). The page background is white unless
    the page first fills a rectangle covering its own box, in which case that
    fill colour is the background: that is how a "white on white" trick is
    built when the author wanted a coloured page.
    """
    from pypdf.generic import ContentStream  # noqa: PLC0415 -- parse path only

    contents = page.get_contents()
    if contents is None:
        return 0, 0, "", ""
    stream = ContentStream(contents, reader)

    box = page.mediabox
    page_width = float(box.width)
    page_height = float(box.height)

    fill: tuple[float, float, float] = (0.0, 0.0, 0.0)
    background = _WHITE
    render_mode = 0
    pending_rect: tuple[float, float] | None = None
    low_contrast = 0
    unpainted = 0
    low_sample: list[str] = []
    unpainted_sample: list[str] = []
    operations = 0

    for operands, operator in stream.operations:
        if operations >= limits.max_text_operations_per_page:
            break
        code = operator.decode("ascii", errors="replace")
        if code == "rg":
            values = _numeric(operands, 3)
            if values is not None:
                fill = (values[0], values[1], values[2])
        elif code == "g":
            values = _numeric(operands, 1)
            if values is not None:
                fill = (values[0], values[0], values[0])
        elif code == "k":
            values = _numeric(operands, 4)
            if values is not None:
                fill = _cmyk_to_rgb(*values)
        elif code in {"sc", "scn"}:
            values = _numeric(operands, 3)
            if values is not None:
                fill = (values[0], values[1], values[2])
            else:
                single = _numeric(operands, 1)
                if single is not None:
                    fill = (single[0], single[0], single[0])
        elif code == "Tr":
            values = _numeric(operands, 1)
            if values is not None:
                render_mode = int(values[0])
        elif code == "re":
            values = _numeric(operands, 4)
            pending_rect = (abs(values[2]), abs(values[3])) if values else None
        elif code in {"f", "F", "f*", "b", "b*", "B", "B*"}:
            if (
                pending_rect is not None
                and pending_rect[0] >= page_width * 0.9
                and pending_rect[1] >= page_height * 0.9
            ):
                background = fill
            pending_rect = None
        elif code in {"Tj", "TJ", "'", '"'}:
            operations += 1
            shown = _text_operand_length(operands)
            if not shown:
                continue
            text = _shown_text(operands)
            if render_mode in _UNPAINTED_RENDER_MODES:
                unpainted += shown
                if len(unpainted_sample) < 8:
                    unpainted_sample.append(text)
                continue
            if contrast_ratio(fill, background) < limits.min_contrast_ratio:
                low_contrast += shown
                if len(low_sample) < 8:
                    low_sample.append(text)
    return (
        low_contrast,
        unpainted,
        _sample(" ".join(low_sample), limits),
        _sample(" ".join(unpainted_sample), limits),
    )


def _shown_text(operands: list[Any]) -> str:
    parts: list[str] = []
    for operand in operands:
        items = operand if isinstance(operand, list) else [operand]
        for item in items:
            if isinstance(item, bytes):
                parts.append(item.decode("latin-1", errors="replace"))
            elif isinstance(item, str):
                parts.append(item)
    return "".join(parts)


def _scan_pdf(data: bytes, limits: InvisibleTextLimits) -> IntakeScan:
    """Structural scan of a PDF. Never raises; a file it cannot read becomes a
    stated `scan_limitation`, which is a different thing from a clean scan."""
    try:
        from pypdf import PdfReader  # noqa: PLC0415 -- heavy import, parse path only

        reader = PdfReader(io.BytesIO(data))
        pages = list(reader.pages[: limits.max_pages_scanned])
    except Exception as exc:  # noqa: BLE001 -- a corrupt or encrypted PDF is a record
        return IntakeScan(
            scan_limitation=(
                "The document could not be inspected for hidden content "
                f"({type(exc).__name__}); only its extracted text was checked."
            )
        )

    low_contrast = 0
    unpainted = 0
    tiny = 0
    off_canvas = 0
    low_samples: list[str] = []
    unpainted_samples: list[str] = []
    tiny_samples: list[str] = []
    off_samples: list[str] = []
    stream_failures = 0

    for page in pages:
        try:
            page_low, page_unpainted, low_text, unpainted_text = _scan_pdf_page_stream(
                page, reader, limits
            )
        except Exception:  # noqa: BLE001 -- one unreadable page, not the document
            stream_failures += 1
        else:
            low_contrast += page_low
            unpainted += page_unpainted
            if low_text:
                low_samples.append(low_text)
            if unpainted_text:
                unpainted_samples.append(unpainted_text)

        try:
            page_tiny, page_off, tiny_text, off_text = _scan_pdf_page_geometry(
                page, limits
            )
        except Exception:  # noqa: BLE001 -- geometry is per page, same rule
            stream_failures += 1
        else:
            tiny += page_tiny
            off_canvas += page_off
            if tiny_text:
                tiny_samples.append(tiny_text)
            if off_text:
                off_samples.append(off_text)

    findings: list[Finding] = []
    if low_contrast:
        findings.append(
            Finding(
                technique=TECHNIQUE_LOW_CONTRAST,
                occurrences=low_contrast,
                detail=(
                    "Text is painted in a colour whose contrast against the "
                    "page is below the readable floor."
                ),
                sample=_sample(" ".join(low_samples), limits),
            )
        )
    if unpainted:
        findings.append(
            Finding(
                technique=TECHNIQUE_UNRENDERED_TEXT,
                occurrences=unpainted,
                detail=(
                    "Text is stored in a render mode that paints nothing, so it "
                    "is extracted but never drawn. A scanned document's text "
                    "layer uses the same mode, so this is recorded and not "
                    "treated as proof of intent."
                ),
                sample=_sample(" ".join(unpainted_samples), limits),
            )
        )
    if tiny:
        findings.append(
            Finding(
                technique=TECHNIQUE_TINY_TYPE,
                occurrences=tiny,
                detail=(
                    "Text is set below the size at which a glyph is legible on "
                    "the printed page."
                ),
                sample=_sample(" ".join(tiny_samples), limits),
            )
        )
    if off_canvas:
        findings.append(
            Finding(
                technique=TECHNIQUE_OFF_CANVAS,
                occurrences=off_canvas,
                detail=(
                    "Text is positioned outside the page area, so it is "
                    "extracted but falls off the visible sheet."
                ),
                sample=_sample(" ".join(off_samples), limits),
            )
        )
    limitation = None
    if stream_failures:
        limitation = (
            "Part of the document could not be inspected for hidden content; "
            "the remainder was checked."
        )
    return IntakeScan(findings=tuple(findings), scan_limitation=limitation)


def _scan_pdf_page_geometry(
    page: Any, limits: InvisibleTextLimits
) -> tuple[int, int, str, str]:
    """Font size and placement for one page, via pypdf's extraction visitor.

    The visitor is the only place pypdf exposes the text matrix and the active
    font size together. `font_size` is the size in text space; a page-level
    scale in the CTM is not applied to it, which is stated rather than
    corrected, because the correction would be a guess and the floor is set low
    enough that ordinary body text is nowhere near it.
    """
    box = page.mediabox
    left, bottom = float(box.left), float(box.bottom)
    right, top = float(box.right), float(box.top)
    tolerance = limits.off_canvas_tolerance_points

    tiny = 0
    off_canvas = 0
    tiny_text: list[str] = []
    off_text: list[str] = []
    seen = 0

    def visitor(text: str, _cm, tm, _font_dict, font_size) -> None:
        nonlocal tiny, off_canvas, seen
        if seen >= limits.max_text_operations_per_page:
            return
        if not (text or "").strip():
            return
        seen += 1
        try:
            size = abs(float(font_size))
        except (TypeError, ValueError):
            size = 0.0
        if 0.0 < size < limits.min_font_size_points:
            tiny += len(text)
            if len(tiny_text) < 8:
                tiny_text.append(text)
        try:
            x, y = float(tm[4]), float(tm[5])
        except (TypeError, ValueError, IndexError):
            return
        if (
            x < left - tolerance
            or x > right + tolerance
            or y < bottom - tolerance
            or y > top + tolerance
        ):
            off_canvas += len(text)
            if len(off_text) < 8:
                off_text.append(text)

    page.extract_text(visitor_text=visitor)
    return (
        tiny,
        off_canvas,
        _sample(" ".join(tiny_text), limits),
        _sample(" ".join(off_text), limits),
    )


# ── The DOCX structural pass ─────────────────────────────────────────────────


def _docx_runs(document: Any, limits: InvisibleTextLimits):
    seen = 0
    paragraphs = list(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    for paragraph in paragraphs:
        for run in paragraph.runs:
            if seen >= limits.max_runs_scanned:
                return
            seen += 1
            yield run


def _scan_docx(data: bytes, limits: InvisibleTextLimits) -> IntakeScan:
    """Structural scan of a DOCX. Hidden text is a run property here, not a
    render mode, so the three techniques map onto three attributes."""
    try:
        import docx  # noqa: PLC0415 -- heavy import, parse path only

        document = docx.Document(io.BytesIO(data))
        runs = list(_docx_runs(document, limits))
    except Exception as exc:  # noqa: BLE001 -- not a real .docx, stated not swallowed
        return IntakeScan(
            scan_limitation=(
                "The document could not be inspected for hidden content "
                f"({type(exc).__name__}); only its extracted text was checked."
            )
        )

    hidden = 0
    low_contrast = 0
    tiny = 0
    hidden_text: list[str] = []
    low_text: list[str] = []
    tiny_text: list[str] = []

    for run in runs:
        text = run.text or ""
        if not text.strip():
            continue
        font = run.font
        if font.hidden:
            hidden += len(text)
            if len(hidden_text) < 8:
                hidden_text.append(text)
            continue
        colour = getattr(font.color, "rgb", None) if font.color is not None else None
        if colour is not None:
            rgb = (colour[0] / 255.0, colour[1] / 255.0, colour[2] / 255.0)
            if contrast_ratio(rgb, _WHITE) < limits.min_contrast_ratio:
                low_contrast += len(text)
                if len(low_text) < 8:
                    low_text.append(text)
        size = font.size
        if size is not None and 0.0 < float(size.pt) < limits.min_font_size_points:
            tiny += len(text)
            if len(tiny_text) < 8:
                tiny_text.append(text)

    findings: list[Finding] = []
    if hidden:
        findings.append(
            Finding(
                technique=TECHNIQUE_UNRENDERED_TEXT,
                occurrences=hidden,
                detail=(
                    "Text is marked hidden, so a word processor extracts it and "
                    "never displays or prints it."
                ),
                sample=_sample(" ".join(hidden_text), limits),
            )
        )
    if low_contrast:
        findings.append(
            Finding(
                technique=TECHNIQUE_LOW_CONTRAST,
                occurrences=low_contrast,
                detail=(
                    "Text is coloured so close to the page that its contrast is "
                    "below the readable floor."
                ),
                sample=_sample(" ".join(low_text), limits),
            )
        )
    if tiny:
        findings.append(
            Finding(
                technique=TECHNIQUE_TINY_TYPE,
                occurrences=tiny,
                detail=(
                    "Text is set below the size at which a glyph is legible on "
                    "the printed page."
                ),
                sample=_sample(" ".join(tiny_text), limits),
            )
        )
    return IntakeScan(findings=tuple(findings))


# ── The one entry point ──────────────────────────────────────────────────────

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK"


def _structural(
    filename: str, data: bytes, limits: InvisibleTextLimits
) -> IntakeScan:
    """Route to the structural scanner by format, on the same magic-byte rules
    the resume extractor already uses so the two cannot disagree about what a
    file is."""
    lowered = (filename or "").lower()
    if lowered.endswith(".pdf") or data[:4] == _PDF_MAGIC:
        return _scan_pdf(data, limits)
    if lowered.endswith(".docx") or data[:2] == _ZIP_MAGIC:
        return _scan_docx(data, limits)
    # A format with no layout layer has nothing structural to hide behind; the
    # Unicode pass in `scan_document` is the whole check, and saying so is not
    # the same as reporting a clean structural scan.
    return IntakeScan()


def scan_document(
    filename: str,
    data: bytes,
    extracted_text: str,
    limits: InvisibleTextLimits = LIMITS,
) -> IntakeScan:
    """Scan one document at intake. TOTAL: it never raises and never rejects.

    `extracted_text` is what the deterministic extractor already produced, so
    this adds one structural walk and one linear pass over a string that has
    already been read. The returned `normalised_text` is what any model may
    see, and it is the ONLY thing a caller should put in a prompt.
    """
    unicode_scan = scan_text(extracted_text, limits)
    structural = _structural(filename, data, limits)
    limitations = [
        value
        for value in (unicode_scan.scan_limitation, structural.scan_limitation)
        if value
    ]
    return IntakeScan(
        findings=unicode_scan.findings + structural.findings,
        normalised_text=unicode_scan.normalised_text,
        scan_limitation=" ".join(limitations) if limitations else None,
    )
