"""The Runbook and `runbook_data/` must not drift apart, in either direction.

WHAT THIS FILE IS FOR. `app/services/hiring/runbook_data/` is a YAML mirror of
the mechanical content of the Ready Pick Now Hiring Philosophy & Intelligence
Runbook, document RPN-PHIL-001, which lives at the repository root as
`Readypick Hiring Philosophy.md`. A mirror nobody checks is a second source of
truth, which is worse than no mirror at all: two numbers that disagree and
nothing that notices. This file is what notices.

It fails when a weight is edited in the data and not in the Runbook, and it
fails when a table is edited in the Runbook and not in the data. Both
directions are load-bearing. The one-directional version of this test
(everything in the data appears somewhere in the document) passes happily while
the document says 0.42 and the data says nothing at all, so the structural
checks below re-parse the Runbook's own tables and compare them row for row,
including their row counts.

WHAT "AT THE CITED SECTION" MEANS. Every entry in the data carries
`source: "RPN-PHIL-001 <section sign>N"`. A section's text runs from its heading
to the next heading at the same or a shallower level, so `<section sign>11`
covers 11.1 through 11.5 and `<section sign>11.1` covers only the baseline
matrix. A value is checked against the text of exactly the section it cites, not
against the whole document, so moving a number to a different section fails.

NORMALISATION, and why it is not a loophole. Comparison strips markdown emphasis
markers, collapses whitespace, and maps U+2014 to a hyphen. The last of those is
forced: the project forbids U+2014 in any string, and the Runbook uses it
heavily, so the data files carry a spaced hyphen where the Runbook has the dash.
Both sides are normalised the same way, so the substitution can hide a dash and
nothing else. Wording changes still fail.

REPORT MODE. `python backend/tests/test_runbook_parity.py --report` prints every
checked value, its citation, and the Runbook line the value was found on. It is
a `__main__` block rather than a pytest option because adding a CLI option means
editing `conftest.py`, which this file does not own.
"""

from __future__ import annotations

import io
import pathlib
import re
import sys
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services.hiring import runbook_data  # noqa: E402

#: U+2014, built from its code point so a repo-wide em dash sweep cannot rewrite
#: the code that normalises it.
EM_DASH = chr(8212)
#: U+2013, the en dash. The Runbook uses it for numeric ranges and it is allowed.
EN_DASH = chr(8211)
#: U+00A7, the section sign that opens every citation's section reference.
SECTION_SIGN = chr(167)

CITATION = re.compile(
    r"^%s (?:%s([0-9]+(?:\.[0-9]+)*)|(Appendix [A-G])|(Part [IVX]+|Part 0)"
    r"|(HOW TO USE THIS RUNBOOK))$"
    % (re.escape(runbook_data.DOCUMENT_ID), re.escape(SECTION_SIGN)))

#: Keys whose values are this repository's own prose about the Runbook, not
#: quotations from it. They are still required to sit under a citation; they are
#: simply not checked for verbatim presence.
EDITORIAL_KEYS = frozenset({
    "runbook_ambiguity", "description", "note", "detail", "anchor_wording_note",
    "unknown_source_type", "see", "source", "document", "runbook_version",
    "anchor_wording_source", "competency_menu_source", "seniority_notes_source",
    "baseline_weight_family", "group", "response_type", "format",
    "stage", "coverage_notes",
})

#: Key suffixes with a meaning. A key ending `_source` holds a citation and is
#: validated as one rather than searched for as a quotation. A key ending
#: `_note` holds this repository's prose about the Runbook. A key ending
#: `_transcribed` holds the Runbook's mathematical notation rewritten in ASCII,
#: which cannot be byte-identical to a formula set in Sigma and Pi; its numbers
#: are still checked by the number test.
CITATION_SUFFIX = "_source"
EDITORIAL_SUFFIXES = ("_note", "_transcribed")

#: Keys holding an index this repository assigned, not a number the Runbook
#: states.
STRUCTURAL_NUMBER_KEYS = frozenset({
    "order", "number", "subsections_present", "section_count", "count",
    "scale_minimum", "scale_maximum", "low", "high", "high_exclusive",
    "d4_low", "d4_high_exclusive", "coefficient_sum", "initial_version",
})


# --------------------------------------------------------------------- runbook
def repo_root() -> pathlib.Path:
    """Walk up from this file until the Runbook is in sight.

    Resolved from `__file__` rather than the working directory, so the test
    behaves the same whether pytest runs from `backend/`, from the repository
    root, or from inside the container.
    """
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if list(parent.glob("Readypick*Hiring*Philosophy*.md")):
            return parent
        if (parent / ".git").exists():
            return parent
    raise AssertionError(
        "could not locate the repository root above %s" % (here,))


def runbook_path() -> pathlib.Path:
    root = repo_root()
    found = sorted(root.glob("Readypick*Hiring*Philosophy*.md"))
    if not found:
        raise AssertionError(
            "%s (RPN-PHIL-001) is not in %s. The data files in runbook_data/ "
            "cite it on every entry and cannot be checked without it."
            % ("Readypick Hiring Philosophy.md", root))
    return found[0]


#: Glyphs the Runbook uses that a data file written under this project's
#: character rules cannot carry, or that a keyboard does not produce. Both
#: sides are folded the same way, so a fold can hide a glyph and nothing else.
GLYPH_FOLD = {
    EM_DASH: "-",
    chr(8722): "-",        # U+2212 minus sign
    chr(160): " ",         # U+00A0 no-break space
    chr(8804): "<=",       # U+2264 less-than or equal to
    chr(8805): ">=",       # U+2265 greater-than or equal to
    chr(8594): "->",       # U+2192 rightwards arrow
    chr(215): "x",         # U+00D7 multiplication sign
    chr(177): "+/-",       # U+00B1 plus-minus sign
}


def normalise(text: str) -> str:
    """Collapse presentation differences that carry no meaning."""
    for glyph, plain in GLYPH_FOLD.items():
        text = text.replace(glyph, plain)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = text.replace("*", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class Runbook:
    """Section index over the Runbook, plus normalised per-section text."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.lines: List[str] = io.open(
            path, encoding="utf-8").read().split("\n")
        self.sections: Dict[str, Tuple[int, int]] = self._index()
        self._norm: Dict[str, str] = {}

    def _index(self) -> Dict[str, Tuple[int, int]]:
        heads: List[Tuple[int, int, Optional[str]]] = []
        in_fence = False
        for i, raw in enumerate(self.lines):
            if raw.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = re.match(r"^(#{1,4}) (.+)$", raw)
            if not m:
                continue
            level = len(m.group(1))
            title = m.group(2).strip()
            key: Optional[str] = None
            mn = re.match(r"^(\d+(?:\.\d+)*)[ .]", title)
            ma = re.match(r"^(Appendix [A-G])", title)
            mp = re.match(r"^PART ([IVX]+|0)\b", title)
            named = title if title == "HOW TO USE THIS RUNBOOK" else None
            if mn:
                key = mn.group(1)
            elif ma:
                key = ma.group(1)
            elif mp:
                key = "Part " + mp.group(1)
            elif named:
                key = named
            heads.append((i, level, key))
        out: Dict[str, Tuple[int, int]] = {}
        for n, (i, level, key) in enumerate(heads):
            if key is None:
                continue
            end = len(self.lines)
            for j in range(n + 1, len(heads)):
                if heads[j][1] <= level:
                    end = heads[j][0]
                    break
            out[key] = (i, end)
        return out

    def has(self, key: str) -> bool:
        return key in self.sections

    def text(self, key: str) -> str:
        if key not in self._norm:
            a, b = self.sections[key]
            self._norm[key] = normalise("\n".join(self.lines[a:b]))
        return self._norm[key]

    def line_of(self, key: str, needle: str) -> Optional[int]:
        """1-based Runbook line where `needle` starts, inside section `key`.

        A quotation may be wrapped across several source lines, so a run of up
        to six joined lines is tried before giving up. Reporting NOT FOUND for
        a value that is plainly there, merely wrapped, would train a reviewer
        to ignore the column.
        """
        a, b = self.sections[key]
        for window in (1, 2, 3, 4, 5, 6):
            for i in range(a, b):
                joined = normalise("\n".join(self.lines[i:i + window]))
                if needle in joined:
                    return i + 1
        return None

    def table(self, key: str, nth: int = 0) -> Tuple[List[str], List[List[str]]]:
        """The nth markdown table inside a section."""
        a, b = self.sections[key]
        tables: List[Tuple[List[str], List[List[str]]]] = []
        i = a
        while i < b:
            if (self.lines[i].strip().startswith("|") and i + 1 < b
                    and re.match(r"^\s*\|[\s:|-]+\|\s*$", self.lines[i + 1])):
                hdr = [normalise(c)
                       for c in self.lines[i].strip().strip("|").split("|")]
                rows: List[List[str]] = []
                j = i + 2
                while j < b and self.lines[j].strip().startswith("|"):
                    rows.append([normalise(c) for c in
                                 self.lines[j].strip().strip("|").split("|")])
                    j += 1
                tables.append((hdr, rows))
                i = j
            else:
                i += 1
        if nth >= len(tables):
            raise AssertionError(
                "%s%s has %d tables, wanted index %d"
                % (SECTION_SIGN, key, len(tables), nth))
        return tables[nth]


@pytest.fixture(scope="module")
def runbook() -> Runbook:
    return Runbook(runbook_path())


# ------------------------------------------------------------------- walking
class Value:
    """One checkable value from a data file, with the citation it sits under."""

    def __init__(self, name: str, path: str, key: Any, value: Any,
                 citation: str) -> None:
        self.name = name
        self.path = path
        #: A mapping key may be a YAML integer (the specificity gradient's
        #: levels are 1 to 5), and the suffix conventions below are string
        #: tests, so it is normalised to text once here rather than at four
        #: call sites.
        self.key = str(key)
        self.value = value
        self.citation = citation

    def __repr__(self) -> str:
        return "%s:%s = %r  [%s]" % (self.name, self.path, self.value,
                                     self.citation)


def _section_of(citation: str) -> str:
    m = CITATION.match(citation)
    if m is None:
        raise AssertionError(
            "citation %r is not of the form %r"
            % (citation, "%s %sN" % (runbook_data.DOCUMENT_ID, SECTION_SIGN)))
    return m.group(1) or m.group(2) or m.group(3) or m.group(4)


def walk(name: str, node: Any, path: str = "",
         citation: Optional[str] = None) -> Iterator[Value]:
    """Yield every leaf under the nearest enclosing `source` citation."""
    if isinstance(node, dict):
        here = node.get(runbook_data.SOURCE_KEY, citation)
        if not isinstance(here, str):
            here = citation
        for key, value in node.items():
            child = "%s.%s" % (path, key) if path else key
            if isinstance(value, (dict, list)):
                yield from walk(name, value, child, here)
            elif here is not None:
                yield Value(name, child, key, value, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            child = "%s[%d]" % (path, i)
            if isinstance(item, (dict, list)):
                yield from walk(name, item, child, citation)
            elif citation is not None:
                yield Value(name, child, path.rsplit(".", 1)[-1], item, citation)


def all_values() -> List[Value]:
    out: List[Value] = []
    for name in runbook_data.all_names():
        data = runbook_data.load(name)
        for key, node in data.items():
            if key == runbook_data.META_KEY:
                continue
            out.extend(walk(name, node, key))
    return out


#: The Runbook writes small counts in words ("Maximum six", "five to eight
#: behaviours", "every six months"), so a digit-only search reports a miss on a
#: number that is plainly stated.
NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
    12: "twelve", 20: "twenty",
}


def number_renderings(value: float) -> List[str]:
    """Every way the Runbook plausibly writes this number."""
    out = {"%g" % value, str(value)}
    if float(value).is_integer() and int(value) in NUMBER_WORDS:
        out.add(NUMBER_WORDS[int(value)])
        out.add(NUMBER_WORDS[int(value)].capitalize())
    if float(value).is_integer():
        out.add(str(int(value)))
    else:
        for places in (1, 2, 3, 4):
            out.add(("%." + str(places) + "f") % value)
        out.add(("%g" % value).lstrip("0"))
    if 0 < value <= 1:
        pct = value * 100
        out.add("%g%%" % pct)
        out.add("%d%%" % round(pct) if float(pct).is_integer() else "%g%%" % pct)
    return sorted(out)


# ----------------------------------------------------------------- the tests
def test_every_data_file_loads() -> None:
    names = runbook_data.all_names()
    assert len(names) == 9, names
    for name in names:
        data = runbook_data.load(name)
        assert isinstance(data, dict) and data, name
        assert data[runbook_data.META_KEY]["document"] == runbook_data.DOCUMENT_ID


def test_the_loader_refuses_an_unknown_file() -> None:
    with pytest.raises(runbook_data.RunbookDataError):
        runbook_data.load("no_such_file")


def test_the_loader_refuses_a_file_whose_entry_carries_no_citation() -> None:
    """A default substituted for a failed load is the defect being prevented."""
    with pytest.raises(runbook_data.RunbookDataError) as caught:
        runbook_data._check_cited("synthetic", {"weights": {"D1": 0.4}})
    assert runbook_data.SOURCE_KEY in str(caught.value)
    runbook_data._check_cited(
        "synthetic",
        {"weights": {runbook_data.SOURCE_KEY: "RPN-PHIL-001 %s11.1"
                     % SECTION_SIGN, "D1": 0.4}})


def test_the_loader_refuses_data_extracted_from_another_document() -> None:
    with pytest.raises(runbook_data.RunbookDataError) as caught:
        runbook_data._check_meta(
            "synthetic", {runbook_data.META_KEY: {"document": "SOME-OTHER-DOC",
                                                  "runbook_version": "1.1"}})
    assert runbook_data.DOCUMENT_ID in str(caught.value)
    with pytest.raises(runbook_data.RunbookDataError):
        runbook_data._check_meta(
            "synthetic",
            {runbook_data.META_KEY: {"document": runbook_data.DOCUMENT_ID}})


def test_no_top_level_entry_lacks_a_source_citation() -> None:
    """spec-doc6 2.2: no data file contains a value with no source citation."""
    for name in runbook_data.all_names():
        data = runbook_data.load(name)
        for key, node in data.items():
            if key == runbook_data.META_KEY:
                continue
            assert runbook_data._has_source(node), (
                "%s.yaml: %r has no %r anywhere beneath it"
                % (name, key, runbook_data.SOURCE_KEY))


def test_every_value_sits_under_a_citation() -> None:
    counted = 0
    for name in runbook_data.all_names():
        data = runbook_data.load(name)
        for key, node in data.items():
            if key == runbook_data.META_KEY:
                continue
            for value in walk(name, node, key):
                assert value.citation, repr(value)
                counted += 1
    assert counted > 600, counted


def test_every_source_citation_names_a_section_that_exists(
        runbook: Runbook) -> None:
    seen = set()
    for value in all_values():
        if value.citation in seen:
            continue
        seen.add(value.citation)
        section = _section_of(value.citation)
        assert runbook.has(section), (
            "%s cites %s%s, which is not a heading in %s"
            % (value.name, SECTION_SIGN, section, runbook.path.name))
    assert len(seen) >= 60, sorted(seen)
    pointers = 0
    for value in all_values():
        if not (isinstance(value.value, str)
                and value.key.endswith(CITATION_SUFFIX)):
            continue
        section = _section_of(value.value)
        assert runbook.has(section), (value.name, value.path, value.value)
        pointers += 1
    assert pointers >= 15, pointers


def test_every_number_appears_at_its_cited_section(runbook: Runbook) -> None:
    misses: List[str] = []
    checked = 0
    for value in all_values():
        if isinstance(value.value, bool) or not isinstance(
                value.value, (int, float)):
            continue
        if value.key in STRUCTURAL_NUMBER_KEYS:
            continue
        section = _section_of(value.citation)
        text = runbook.text(section)
        if not any(r in text for r in number_renderings(value.value)):
            misses.append("%r not found at %s%s (%s)"
                          % (value.value, SECTION_SIGN, section, value))
        checked += 1
    assert checked > 100, checked
    assert not misses, "\n".join(misses)


def test_every_quoted_string_appears_at_its_cited_section(
        runbook: Runbook) -> None:
    misses: List[str] = []
    checked = 0
    for value in all_values():
        if not isinstance(value.value, str) or value.key in EDITORIAL_KEYS:
            continue
        if value.key.endswith(EDITORIAL_SUFFIXES) or value.key.endswith(
                CITATION_SUFFIX):
            continue
        text = normalise(value.value)
        if len(text) < 12 or " " not in text:
            continue
        section = _section_of(value.citation)
        if text not in runbook.text(section):
            misses.append("%s%s does not contain %r  (%s)"
                          % (SECTION_SIGN, section, text[:110], value.path))
        checked += 1
    assert checked > 400, checked
    assert not misses, "%d of %d strings not found:\n%s" % (
        len(misses), checked, "\n".join(misses[:40]))


def test_no_data_file_contains_an_em_dash() -> None:
    directory = pathlib.Path(runbook_data.__file__).resolve().parent
    for name in runbook_data.all_names():
        raw = (directory / (name + ".yaml")).read_text(encoding="utf-8")
        assert EM_DASH not in raw, name


# ------------------------------------------- structural, Runbook-side parity
def test_the_baseline_weight_matrix_matches_the_runbook(
        runbook: Runbook) -> None:
    """Every row of 11.1, compared cell by cell, and the row count too."""
    families = runbook_data.department_models()["baseline_weight_families"]
    a, b = runbook.sections["11.1"]
    from_runbook: Dict[str, Dict[str, List[float]]] = {}
    current: Optional[str] = None
    for i in range(a, b):
        line = runbook.lines[i]
        m = re.match(r"^\*\*(.+?)\*\*\s*$", line.strip())
        if m:
            current = normalise(m.group(1))
            from_runbook[current] = {}
        m2 = re.match(
            r"^\|\s*([^|]+?)\s*\|\s*(0\.\d\d)\s*\|\s*(0\.\d\d)\s*\|\s*"
            r"(0\.\d\d)\s*\|\s*(0\.\d\d)\s*\|\s*(0\.\d\d)\s*\|\s*$", line)
        if m2 and current:
            from_runbook[current][normalise(m2.group(1))] = [
                float(m2.group(k)) for k in range(2, 7)]
    by_label = {normalise(f["label"]): f for f in families.values()}
    assert set(by_label) == set(from_runbook), (
        sorted(set(by_label) ^ set(from_runbook)))
    rows = 0
    for label, table in from_runbook.items():
        stored = by_label[label]["weights"]
        assert set(normalise(k) for k in stored) == set(table), (
            label, sorted(set(normalise(k) for k in stored) ^ set(table)))
        for seniority, weights in table.items():
            key = next(k for k in stored if normalise(k) == seniority)
            got = [float(stored[key]["D%d" % d]) for d in range(1, 6)]
            assert got == weights, (label, seniority, got, weights)
            assert abs(sum(weights) - 1.0) < 1e-9, (label, seniority, weights)
            rows += 1
    assert rows == 39, rows


def test_the_six_evidence_tiers_match_the_runbook(runbook: Runbook) -> None:
    tiers = runbook_data.evidence_tiers()["tiers"]
    _hdr, rows = runbook.table("6.1")
    assert len(rows) == 6 == len(tiers), (len(rows), len(tiers))
    for row in rows:
        tier = tiers[row[0]]
        assert normalise(tier["name"]) == row[1], (row[0], tier["name"])
        assert normalise(tier["definition"]) == row[2], row[0]
        assert normalise(tier["cost_to_fabricate"]) == row[3], row[0]
        assert float(tier["default_strength"]) == float(row[4]), row[0]


def test_the_score_bands_match_the_runbook(runbook: Runbook) -> None:
    bands = runbook_data.bands()["score_bands"]["bands"]
    _hdr, rows = runbook.table("10.8")
    assert len(rows) == len(bands) == 6, (len(rows), len(bands))
    for row, band in zip(rows, bands):
        assert normalise(band["range"]) == row[0], (band, row)
        assert normalise(band["band"]) == row[1], (band, row)


def test_the_six_situation_types_match_the_runbook(runbook: Runbook) -> None:
    types = runbook_data.situation_types()["situation_types"]
    _hdr, rows = runbook.table("18.4")
    assert len(rows) == 6 == len(types), (len(rows), len(types))
    by_name = {normalise(t["name"]): t for t in types.values()}
    for row in rows:
        entry = by_name[row[0]]
        assert normalise(entry["description"]) == row[1], row[0]
        assert normalise(entry["weight_consequence"]) == row[2], row[0]
        assert normalise(entry["evidence_emphasis"]) == row[3], row[0]


def test_the_force_ranking_default_weights_match_the_runbook(
        runbook: Runbook) -> None:
    stored = runbook_data.swot_instrument()["scorecard"]["force_ranking"][
        "default_weights"]
    _hdr, rows = runbook.table("20.3")
    # Compared against each other rather than against a literal count. The
    # assertion's job is "the data says what the Runbook says", and pinning the
    # number of rows made it fail when §20.3 grew rows for counts of one, two
    # and three -- reporting a Runbook edit as a parity failure, which is the
    # opposite of what it exists to detect.
    assert len(rows) == len(stored), (len(rows), len(stored))
    assert rows, "§20.3 has no default-weight table"
    for row in rows:
        weights = [float(w) for w in row[1].split("/")]
        assert [float(w) for w in stored[int(row[0])]] == weights, row
        assert abs(sum(weights) - 1.0) < 1e-9, row
        assert len(weights) == int(row[0]), row


def test_the_confidence_coefficients_match_the_runbook(
        runbook: Runbook) -> None:
    confidence = runbook_data.bands()["confidence"]
    text = runbook.text("10.7")
    total = 0.0
    for term, coefficient in confidence["terms"].items():
        weight = float(coefficient["coefficient"])
        assert ("%.2f x %s" % (weight, term)) in text, (term, weight)
        total += weight
    assert abs(total - 1.0) < 1e-9, total
    _hdr, rows = runbook.table("10.7")
    assert len(rows) == len(confidence["labels"]) == 4, rows


def test_the_dimension_floors_match_the_runbook(runbook: Runbook) -> None:
    floors = runbook_data.bands()["dimension_floors"]["floors"]
    _hdr, rows = runbook.table("12.2")
    assert len(rows) == len(floors) == 4, (len(rows), len(floors))
    for row, stored in zip(rows, floors):
        assert normalise(stored["dimension"]) == row[0], (stored, row)
        assert float(stored["floor"]) == float(row[1]), (stored, row)


def test_the_authenticity_multiplier_breakpoints_match_the_runbook(
        runbook: Runbook) -> None:
    multiplier = runbook_data.bands()["authenticity_multiplier"]
    text = runbook.text("10.5")
    branches = multiplier["piecewise"]
    assert len(branches) == 5, branches
    for branch in branches:
        assert normalise(branch["condition"]) in text, branch["condition"]
    assert float(multiplier["caps_at"]) == 1.00


def test_the_five_dimensions_and_their_anchors_match_the_runbook(
        runbook: Runbook) -> None:
    dims = runbook_data.dimensions()["dimensions"]
    assert list(dims) == ["D1", "D2", "D3", "D4", "D5"], list(dims)
    for n, (key, entry) in enumerate(dims.items(), 1):
        section = "9.%d" % n
        _hdr, rows = runbook.table(section)
        anchors = entry["rubric_anchors"]
        assert len(rows) == len(anchors) == 6, (key, len(rows), len(anchors))
        for row, anchor in zip(rows, anchors):
            assert normalise(anchor["band"]) == row[0], (key, anchor, row)
            assert normalise(anchor["meaning"]) == row[1], (key, anchor)
        head = normalise(runbook.lines[runbook.sections[section][0]])
        assert normalise(entry["name"]) in head, (key, head)


def test_the_twelve_company_dna_sections_match_the_runbook(
        runbook: Runbook) -> None:
    sections = runbook_data.company_dna_instrument()["sections"]
    assert len(sections) == 12, len(sections)
    for entry in sections:
        key = "16.%d" % entry["number"]
        assert runbook.has(key), key
        head = normalise(runbook.lines[runbook.sections[key][0]])
        assert normalise(entry["title"]) in head, (key, entry["title"], head)


def test_the_observable_evidence_example_pairs_are_verbatim(
        runbook: Runbook) -> None:
    """16.3's accepted/rejected pairs are the literal quality bar."""
    section = next(s for s in runbook_data.company_dna_instrument()["sections"]
                   if s["number"] == 3)
    pairs = section["example_pairs"]
    text = runbook.text("16.3")
    assert len(pairs) == 2, pairs
    assert text.count("Rejected:") == 2 and text.count("Accepted:") == 2, text
    for pair in pairs:
        assert normalise(pair["rejected"]) in text, pair["rejected"]
        assert normalise(pair["accepted"]) in text, pair["accepted"]
        assert len(normalise(pair["accepted"])) > 60, pair["accepted"]


def test_the_seven_swot_probes_are_verbatim(runbook: Runbook) -> None:
    probes = runbook_data.swot_instrument()["high_value_probes"]["probes"]
    assert len(probes) == 7, len(probes)
    text = runbook.text("18.3")
    for n, probe in enumerate(probes, 1):
        assert probe["number"] == n, probe
        assert normalise(probe["name"]) in text, probe["name"]
        assert normalise(probe["probe"]) in text, probe["probe"]
        assert normalise(probe["purpose"]) in text, probe["purpose"]


def test_the_swot_rejection_rules_match_the_runbook(runbook: Runbook) -> None:
    rules = runbook_data.swot_instrument()["rejection_rules"]["conditions"]
    a, b = runbook.sections["18.5"]
    from_runbook = [normalise(line.strip()[2:])
                    for line in runbook.lines[a:b]
                    if line.strip().startswith("- ")]
    assert len(from_runbook) == 6, from_runbook
    assert [normalise(r) for r in rules] == from_runbook


def test_the_precedence_rules_match_the_runbook(runbook: Runbook) -> None:
    rules = runbook_data.precedence()["conflict_resolution"]["rules"]
    _hdr, rows = runbook.table("3.5")
    assert len(rows) == len(rules) == 7, (len(rows), len(rules))
    for n, (row, rule) in enumerate(zip(rows, rules), 1):
        assert rule["order"] == n, rule
        assert normalise(rule["conflict"]) == row[0], (rule, row)
        assert normalise(rule["resolution"]) == row[1], (rule, row)


def test_the_prohibited_disqualifiers_match_the_runbook(
        runbook: Runbook) -> None:
    stored = runbook_data.disqualifiers()["prohibited"]["patterns"]
    a, b = runbook.sections["12.4"]
    from_runbook = [normalise(line.strip()[2:])
                    for line in runbook.lines[a:b]
                    if line.strip().startswith("- ")]
    assert len(from_runbook) == 10, from_runbook
    assert [normalise(s) for s in stored] == from_runbook


def test_every_department_competency_menu_matches_the_runbook(
        runbook: Runbook) -> None:
    departments = runbook_data.department_models()["departments"]
    assert len(departments) == 15, len(departments)
    total = 0
    for entry in departments.values():
        section = _section_of(entry["competency_menu_source"])
        _hdr, rows = runbook.table(section)
        menu = entry["competency_menu"]
        assert len(menu) == len(rows), (section, len(menu), len(rows))
        for row, item in zip(rows, menu):
            assert normalise(item["id"]) == row[0], (section, item, row)
            assert normalise(item["competency"]) == row[1], (section, item)
        total += len(menu)
    assert total >= 150, total


def test_the_gates_match_the_runbook(runbook: Runbook) -> None:
    gates = runbook_data.bands()["gates"]["gates"]
    _hdr, rows = runbook.table("4.3")
    assert len(rows) == len(gates) == 4, (len(rows), len(gates))
    for row, gate in zip(rows, gates):
        assert normalise(gate["gate"]) == row[0], (gate, row)
        assert normalise(gate["condition"]) == row[1], (gate, row)


def test_the_independence_groups_match_the_runbook(runbook: Runbook) -> None:
    groups = runbook_data.evidence_tiers()["independence"]["groups"]["sources"]
    _hdr, rows = runbook.table("38.1")
    assert len(rows) == len(groups) == 6, (len(rows), len(groups))
    for row, group in zip(rows, groups):
        assert str(group["number"]) == row[0], (group, row)
        assert normalise(group["source_name"]) == row[1], (group, row)
        assert normalise(group["group"]) == row[3], (group, row)


def test_the_runbook_front_matter_matches_the_declared_version(
        runbook: Runbook) -> None:
    """A citation into a version the data does not name cannot be checked."""
    head = "\n".join(runbook.lines[:40])
    assert runbook_data.DOCUMENT_ID in head, head[:400]
    for name in runbook_data.all_names():
        version = str(runbook_data.load(name)[runbook_data.META_KEY][
            "runbook_version"])
        assert ("| Version | %s |" % version) in head, (name, version)


# ------------------------------------------------------------------- report
def _report() -> int:
    book = Runbook(runbook_path())
    values = all_values()
    print("Runbook: %s" % book.path)
    print("Sections indexed: %d" % len(book.sections))
    print("Values under a citation: %d" % len(values))
    print("")
    unfound: List[Value] = []
    current = None
    for value in values:
        if value.name != current:
            current = value.name
            print("=" * 78)
            print(current + ".yaml")
            print("=" * 78)
        found: Optional[int] = None
        section = _section_of(value.citation)
        if value.key == runbook_data.SOURCE_KEY:
            status = "citation"
        elif value.key.endswith(CITATION_SUFFIX):
            status = "citation -> %s" % value.value
        elif value.key in EDITORIAL_KEYS or value.key.endswith(
                EDITORIAL_SUFFIXES):
            status = "editorial, not quoted"
        elif isinstance(value.value, bool):
            status = "flag"
        elif value.value is None:
            status = "absent in the Runbook, recorded as null"
        else:
            if isinstance(value.value, str):
                found = book.line_of(section, normalise(value.value))
            elif isinstance(value.value, (int, float)):
                for rendering in number_renderings(value.value):
                    found = book.line_of(section, rendering)
                    if found:
                        break
            if found:
                status = "line %d" % found
            elif value.key in STRUCTURAL_NUMBER_KEYS:
                status = "index assigned here"
            elif isinstance(value.value, str) and (
                    len(normalise(value.value)) < 12
                    or " " not in normalise(value.value)):
                status = "identifier"
            else:
                status = "NOT FOUND"
                unfound.append(value)
        print("%-56s %-24s %s" % (value.path[:56], value.citation, status))
    print("")
    print("Values with no line in their cited section: %d" % len(unfound))
    for value in unfound:
        print("  %s:%s = %r  [%s]"
              % (value.name, value.path, value.value, value.citation))
    return 1 if unfound else 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        raise SystemExit(_report())
    raise SystemExit(
        "usage: python tests/test_runbook_parity.py --report\n"
        "       python -m pytest tests/test_runbook_parity.py")
