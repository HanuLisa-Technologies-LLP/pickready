"""File classification for project artifacts: family, language, supported.

Classification is DATA -- tables keyed by extension -- so adding a discipline
or a format is a row, not a rewrite. An unknown format is classified as
`unsupported` and RECORDED rather than failing the project: the master brief's
graceful-degradation rule, and the honest alternative to pretending a parser
exists (a limitation stated beats contents hallucinated).

Families group formats by how they are parsed, not by discipline: a Verilog
file and a Python file are both `source_code` to the parser even though one is
ECE evidence and the other software evidence. Discipline signals come out of
the evidence engine, which reads the language table below.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

FAMILY_SOURCE = "source_code"
FAMILY_DOCUMENT = "document"
FAMILY_DATA = "structured_data"
FAMILY_SPREADSHEET = "spreadsheet"
FAMILY_NOTEBOOK = "notebook"
FAMILY_ARCHIVE = "archive"
FAMILY_CAD = "cad"
FAMILY_IMAGE = "image"
FAMILY_MANIFEST = "manifest"
FAMILY_CONFIG = "config"
FAMILY_UNSUPPORTED = "unsupported"

#: Extension -> programming language. The breadth here IS the broad-format
#: story for source code: the structural parser reads any of these with the
#: same deterministic signal extraction.
LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".php": "PHP",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".bash": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".r": "R",
    ".m": "MATLAB",
    ".scala": "Scala",
    ".dart": "Dart",
    ".lua": "Lua",
    # ── ECE / hardware description ──
    ".v": "Verilog",
    ".sv": "SystemVerilog",
    ".vhd": "VHDL",
    ".vhdl": "VHDL",
    ".ino": "Arduino C++",
    ".asm": "Assembly",
    ".s": "Assembly",
    # ── Simulation / circuits ──
    ".cir": "SPICE",
    ".sp": "SPICE",
    ".net": "SPICE netlist",
}

#: Well-known dependency/build manifests, parsed for technology evidence.
MANIFEST_FILENAMES: frozenset[str] = frozenset(
    {
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "pipfile",
        "go.mod",
        "cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gemfile",
        "composer.json",
        "mix.exs",
        "cmakelists.txt",
        "makefile",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
)

DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".md", ".markdown", ".txt", ".rst", ".html", ".htm",
     ".odt", ".rtf"}
)
DATA_EXTENSIONS: frozenset[str] = frozenset(
    {".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg", ".conf",
     ".env.example", ".properties"}
)
SPREADSHEET_EXTENSIONS: frozenset[str] = frozenset({".csv", ".tsv", ".xlsx", ".ods"})
ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".zip"})
#: Archives we recognise but do not extract (no safe stdlib inspection path
#: equivalent to ZipInfo's declared sizes). Recorded as a limitation.
UNEXTRACTED_ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
    {".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
)
CAD_EXTENSIONS: frozenset[str] = frozenset(
    {".step", ".stp", ".iges", ".igs", ".stl", ".dxf", ".ifc", ".obj", ".dwg",
     ".sldprt", ".sldasm", ".f3d", ".3mf"}
)
IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".tif", ".tiff"}
)
NOTEBOOK_EXTENSIONS: frozenset[str] = frozenset({".ipynb"})

#: CAD formats with a deterministic text/metadata reader in `parsers`.
PARSABLE_CAD: frozenset[str] = frozenset(
    {".step", ".stp", ".iges", ".igs", ".stl", ".dxf", ".ifc"}
)

#: Names of directories excluded from repositories and archives alike:
#: generated or dependency content that adds bulk, not evidence.
IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        "node_modules", "dist", "build", "out", "target", "vendor",
        ".venv", "venv", "__pycache__", ".git", ".hg", ".svn", ".idea",
        ".vscode", "coverage", ".next", ".nuxt", ".terraform", "bower_components",
        "site-packages", ".mypy_cache", ".pytest_cache", ".ruff_cache", "eggs",
    }
)

#: Well-known extensionless documents. Found live: octocat/Hello-World is one
#: bare `README`, and without this row the whole repository classified as
#: nothing-extractable.
DOCUMENT_FILENAMES: frozenset[str] = frozenset(
    {"readme", "license", "licence", "changelog", "contributing", "notice",
     "authors", "codeowners"}
)

#: Lockfiles: recorded, never parsed for evidence (they restate a manifest).
LOCKFILE_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
        "cargo.lock", "gemfile.lock", "composer.lock", "go.sum",
    }
)


@dataclass(frozen=True)
class Classification:
    family: str
    #: Language for source files, format label otherwise ("PDF", "STEP", ...).
    label: str
    supported: bool
    #: Stated limitation when support is partial or absent.
    limitation: str | None = None


def classify(filename: str) -> Classification:
    """Classify one file by name. Pure and total: every input classifies."""
    base = os.path.basename(filename or "").strip().lower()
    _, extension = os.path.splitext(base)

    if base in LOCKFILE_NAMES:
        return Classification(
            FAMILY_CONFIG, "Lockfile", False,
            "Lockfiles restate a manifest and are recorded, not parsed.",
        )
    if base in MANIFEST_FILENAMES or base.startswith("dockerfile"):
        return Classification(FAMILY_MANIFEST, base, True)
    # Exact match only: `readme.md` already classifies by its extension.
    if base in DOCUMENT_FILENAMES:
        return Classification(FAMILY_DOCUMENT, "TXT", True)
    if extension in LANGUAGE_BY_EXTENSION:
        return Classification(
            FAMILY_SOURCE, LANGUAGE_BY_EXTENSION[extension], True
        )
    if extension in NOTEBOOK_EXTENSIONS:
        return Classification(FAMILY_NOTEBOOK, "Jupyter notebook", True)
    if extension in DOCUMENT_EXTENSIONS:
        if extension in {".odt", ".rtf"}:
            return Classification(
                FAMILY_DOCUMENT, extension.lstrip(".").upper(), False,
                "No structural reader for this document format yet; the file "
                "is recorded but its contents are not extracted.",
            )
        return Classification(FAMILY_DOCUMENT, extension.lstrip(".").upper(), True)
    if extension in DATA_EXTENSIONS:
        return Classification(FAMILY_DATA, extension.lstrip(".").upper(), True)
    if extension in SPREADSHEET_EXTENSIONS:
        if extension == ".ods":
            return Classification(
                FAMILY_SPREADSHEET, "ODS", False,
                "ODS spreadsheets are recorded but not extracted.",
            )
        return Classification(
            FAMILY_SPREADSHEET, extension.lstrip(".").upper(), True
        )
    if extension in ARCHIVE_EXTENSIONS:
        return Classification(FAMILY_ARCHIVE, "ZIP", True)
    if extension in UNEXTRACTED_ARCHIVE_EXTENSIONS:
        return Classification(
            FAMILY_ARCHIVE, extension.lstrip(".").upper(), False,
            "Only ZIP archives are inspected and extracted; repackage as ZIP "
            "for the contents to be analysed.",
        )
    if extension in CAD_EXTENSIONS:
        if extension in PARSABLE_CAD:
            return Classification(FAMILY_CAD, extension.lstrip(".").upper(), True)
        return Classification(
            FAMILY_CAD, extension.lstrip(".").upper(), False,
            "This CAD format is proprietary or binary; the file is recorded "
            "as design evidence but its geometry is not extracted.",
        )
    if extension in IMAGE_EXTENSIONS:
        return Classification(
            FAMILY_IMAGE, extension.lstrip(".").upper(), False,
            "Images are recorded as artifacts but their contents are not "
            "interpreted.",
        )
    return Classification(
        FAMILY_UNSUPPORTED, extension.lstrip(".").upper() or "unknown", False,
        "Unrecognised format; the file is recorded but not parsed.",
    )


def is_ignored_path(path: str) -> bool:
    """True when any path segment is a generated/dependency directory."""
    segments = {segment.lower() for segment in path.replace("\\", "/").split("/")}
    return bool(segments & IGNORED_DIRECTORIES)
