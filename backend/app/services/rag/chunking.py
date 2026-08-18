"""Splitting a document into retrievable pieces without destroying its meaning.

WHY NOT FIXED-WIDTH WINDOWS
---------------------------
A 400-character window over a resume cuts a job title away from the dates under
it and a skill away from the project it was used on. Retrieval then returns a
fragment that is topically correct and evidentially useless: "Kafka, Terraform,
Airflow" retrieves for a Kafka query and tells an agent nothing about whether
the candidate ever ran one. Splitting on the document's OWN boundaries -- a
markdown heading, a blank line, a bullet -- keeps the unit of retrieval the same
as the unit of meaning.

WHAT A SECTION TYPE IS FOR
--------------------------
`section_type` is what lets retrieval ask a narrower question than "anything
similar to this". "Which of this candidate's EXPERIENCE entries mention
distributed systems" is a different and much better query than the same words
against their skills list, where every skill matches everything. It is inferred
from the heading the chunk sits under, deterministically, and falls back to
`prose` rather than guessing.

OVERLAP IS SMALL AND DELIBERATE
-------------------------------
Enough that a sentence spanning a boundary survives in one of the two pieces,
not so much that the same sentence is retrieved twice under two ids and
presented to an agent as two independent pieces of evidence. That second
failure is worse than the first: an agent counting corroboration will find it.

A CHUNK KNOWS WHAT IT CAME FROM
-------------------------------
`content_sha256` over the chunk text is what makes re-embedding incremental --
a JD edited in its third paragraph re-embeds one chunk, not fourteen. And
`source_version` over the whole source document is what lets retrieval exclude
chunks belonging to a superseded version of a JD rather than blending two
versions of the same requirement into one answer.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

#: Target characters per chunk. Empirical: large enough that a bullet plus its
#: context survives, small enough that five chunks fit an interactive prompt
#: budget alongside the instruction and the transcript.
TARGET_CHARS = 400

#: Carried from the tail of the previous chunk. See the module docstring: the
#: number is a compromise between a severed sentence and duplicated evidence.
OVERLAP_CHARS = 75

#: Below this a fragment is not independently retrievable -- a lone heading, a
#: date, a bullet marker -- and is merged into its neighbour instead of becoming
#: a chunk that can win a similarity search on almost nothing.
MIN_CHARS = 60

SOURCE_JD = "jd"
SOURCE_RESUME = "resume"
SOURCE_ASSESSMENT = "assessment"

SECTION_PROSE = "prose"
SECTION_SKILLS = "skills"
SECTION_EXPERIENCE = "experience"
SECTION_EDUCATION = "education"
SECTION_RESPONSIBILITIES = "responsibilities"
SECTION_QA = "qa"

#: Heading text to section type. Ordered: the first match wins, so the more
#: specific patterns come first.
_SECTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"work\s+experience|employment|professional\s+experience|experience", re.I), SECTION_EXPERIENCE),
    (re.compile(r"education|academic|qualification", re.I), SECTION_EDUCATION),
    (re.compile(r"responsibilit|accountabilit|what\s+you.ll\s+do", re.I), SECTION_RESPONSIBILITIES),
    (re.compile(r"skill|technolog|tech\s+stack|competenc", re.I), SECTION_SKILLS),
)

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
#: An ALL CAPS or Title Case line with no terminal punctuation, which is how a
#: resume written in a word processor marks a section.
_BARE_HEADING = re.compile(r"^[A-Z][A-Za-z /&]{2,40}$")


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece, and everything retrieval needs to filter it."""

    content: str
    section_type: str
    ordinal: int
    source_type: str

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "section_type": self.section_type,
            "ordinal": self.ordinal,
            "source_type": self.source_type,
            "content_sha256": self.content_sha256,
        }


def source_version(text: str) -> str:
    """A stable fingerprint of a whole source document.

    Chunks carry it so a query can exclude everything belonging to a superseded
    version of a JD. Without it, an edited JD leaves its old chunks in the index
    and retrieval silently blends two versions of the same requirement.
    """
    return hashlib.sha256(" ".join(str(text or "").split()).encode("utf-8")).hexdigest()


def _classify(heading: str, default: str) -> str:
    for pattern, section in _SECTION_PATTERNS:
        if pattern.search(heading):
            return section
    return default


def _blocks(text: str, default_section: str) -> list[tuple[str, str]]:
    """(section_type, block) pairs, split on headings and blank lines.

    Headings are kept WITH the block beneath them rather than becoming blocks of
    their own. A heading alone is a chunk that matches its own words and carries
    no evidence, and it is exactly what a naive splitter produces most of.
    """
    out: list[tuple[str, str]] = []
    section = default_section
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            joined = "\n".join(buffer).strip()
            if joined:
                out.append((section, joined))
            buffer.clear()

    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        heading = _HEADING.match(line)
        if heading:
            flush()
            section = _classify(heading.group(2), default_section)
            buffer.append(heading.group(2).strip())
            continue
        if not line.strip():
            flush()
            continue
        if _BARE_HEADING.match(line.strip()) and len(line.strip().split()) <= 4:
            flush()
            section = _classify(line, default_section)
            buffer.append(line.strip())
            continue
        buffer.append(line)

    flush()
    return out


def _split_long(block: str) -> list[str]:
    """Break an oversized block on sentence boundaries, with a small overlap."""
    if len(block) <= TARGET_CHARS:
        return [block]

    sentences = re.split(r"(?<=[.!?])\s+|\n", block)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + len(sentence) + 1 > TARGET_CHARS:
            pieces.append(current)
            # Carry the tail forward so a sentence split across the boundary
            # survives whole in the second piece.
            current = (current[-OVERLAP_CHARS:] + " " + sentence).strip()
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)

    # A single sentence longer than the target has no boundary to split on. Hard
    # wrapping it is worse than keeping it: retrieval would return half a
    # sentence. It stays whole and oversized, and the caller's token budget
    # handles it.
    return pieces or [block]


def chunk_text(
    text: str, *, source_type: str, default_section: str = SECTION_PROSE
) -> list[Chunk]:
    """Split one document into chunks, merging fragments too small to retrieve."""
    pieces: list[tuple[str, str]] = []
    for section, block in _blocks(text, default_section):
        for piece in _split_long(block):
            pieces.append((section, piece))

    # Merge undersized pieces forward into the next piece of the SAME section.
    merged: list[tuple[str, str]] = []
    carry = ""
    carry_section = ""
    for section, piece in pieces:
        if carry and carry_section == section:
            piece = f"{carry}\n{piece}".strip()
            carry = ""
        elif carry:
            merged.append((carry_section, carry))
            carry = ""
        if len(piece) < MIN_CHARS:
            carry, carry_section = piece, section
            continue
        merged.append((section, piece))
    if carry:
        if merged and merged[-1][0] == carry_section:
            last_section, last = merged.pop()
            merged.append((last_section, f"{last}\n{carry}".strip()))
        else:
            merged.append((carry_section, carry))

    return [
        Chunk(content=content, section_type=section, ordinal=index, source_type=source_type)
        for index, (section, content) in enumerate(merged)
        if content.strip()
    ]


def chunk_jd(jd_markdown: str) -> list[Chunk]:
    return chunk_text(jd_markdown, source_type=SOURCE_JD, default_section=SECTION_PROSE)


def chunk_resume(resume_text: str) -> list[Chunk]:
    return chunk_text(resume_text, source_type=SOURCE_RESUME, default_section=SECTION_PROSE)


def chunk_exchanges(exchanges: list[dict[str, str]]) -> list[Chunk]:
    """One chunk per question-and-answer pair, never split apart.

    A question without its answer retrieves for the topic and proves nothing; an
    answer without its question is a paragraph with no referent. The pair is the
    smallest unit that is evidence, so it is the unit, regardless of length.
    """
    chunks: list[Chunk] = []
    for index, exchange in enumerate(exchanges):
        question = " ".join(str(exchange.get("question") or "").split())
        answer = " ".join(str(exchange.get("answer") or "").split())
        if not answer:
            continue
        chunks.append(
            Chunk(
                content=f"Q: {question}\nA: {answer}",
                section_type=SECTION_QA,
                ordinal=index,
                source_type=SOURCE_ASSESSMENT,
            )
        )
    return chunks
