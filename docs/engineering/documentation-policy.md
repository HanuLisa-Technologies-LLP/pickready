# Documentation policy

**Status:** current. **Reviewed:** 2026-10-14. This file governs every other document.

## The rule

Documentation is part of the change. During and after every task, update the affected authoritative documents in the same change, keep them accurate and minimal, and move anything obsolete to [../history/](../history/) as a brief record. A document that is wrong costs more than a missing one: agents act on it.

## Where things live

| Kind | Meaning | Folder |
|---|---|---|
| Specification | Approved intended behaviour. May run ahead of the code only when it says so with a status label. | `docs/spec/`, `docs/product/` |
| Implementation | What exists, traced to code, tests or configuration. | `docs/architecture/`, `docs/reference/` |
| Decision | An owner or architecture ruling that is in force; open questions; owed changes. | `docs/decisions/`, `docs/architecture/adr/` |
| Engineering rules | How to change the code without breaking a rule. | `docs/engineering/` (and `CLAUDE.md`, `AGENTS.md`, `.claude/rules/`) |
| Operational | Reproducible procedures and facts about running systems. | `docs/operations/` |
| Verification | Dated evidence that something was run. Two files are read by tests. | `docs/verification/` |
| History | Obsolete, superseded or abandoned material, written as short records. Never an instruction. | `docs/history/` |

Index of everything: [../README.md](../README.md). Five Markdown files stay at the repository root because tools resolve them there: `README.md`, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, and `PRODUCT.md` with `DESIGN.md` (the Impeccable tooling reads those two from the root). `.impeccable-exceptions.md` also stays.

## Status header

Every current document starts with a status line:

`**Status:** current | decided, pending | measurement. **Reviewed:** YYYY-MM-DD (what it was checked against).`

- **Reviewed** means read against the owner decisions and spot-checked against named code, tests or configuration on that date.
- **Verified** is stronger and is used only with evidence: "verified against `path` on date". Do not write "verified" for a claim that was only read.
- **Decided, pending** marks a section whose rule is ruled but not built. State what exists today beside it. Never describe it as working.
- A **measurement** (a baseline, a live run) records what was true on a day and is never edited to match new behaviour.

## Precedence

When two statements disagree, take the first that applies:

1. **The latest dated owner statement**, as recorded in [../decisions/REGISTER.md](../decisions/REGISTER.md). A newer instruction overrides an older one without being asked again.
2. **The RBAC specification** for authorization, tenant isolation, role ownership, job lifecycle and audit, **as amended** by the register.
3. **The Runbook** (`docs/product/Readypick Hiring Philosophy.md`, v1.4) for evaluation mechanics.
4. **Specifications** in `docs/spec/`, then the Candidate Dashboard Specification for the candidate list surface only.
5. **Implementation documents.** Code and tests are evidence of what exists; they are not evidence of what was intended.

"Restrict more when unsure" applies only where a higher source is silent; it never overrides an affirmative grant. Owner-filed documents (RBAC spec, Runbook, Dashboard spec) stay verbatim. An amendment is recorded in the register and signalled by one line at the top of the amended document.

Unanswered questions live in [../decisions/OPEN.md](../decisions/OPEN.md). **An unanswered question is never treated as approved.** When you must ask the owner, explain the current behaviour, the problem, a real example, the consequence of each option, and your recommendation. Do not ask vague multiple-choice questions.

## Lifecycle

1. A change that alters behaviour updates the owning document, the register (if a decision moved) and any index in the same change.
2. A statement that stops being true is removed from the current document. If it explains something still visible in code or data, add a brief record to history; otherwise git history is enough.
3. History records are short: what it was, dates, why it is obsolete, what replaced it, and how to retrieve the original (`git show <commit>:<path>`). They contain no instructions. See [../history/README.md](../history/README.md).
4. No dead or deprecated text outside `docs/history/`. No "superseded" banners on current documents: fix them or move them.
5. A proposed feature is labelled proposed. A decided but unbuilt feature is labelled decided, pending, and listed under Pending changes in the register.
6. A document that tests or code read by path keeps its path and cited content (table in [testing.md](testing.md)). Code comments cite old paths (including the old `CLAUDE.md` sections); [../history/README.md](../history/README.md) holds the crosswalk.
7. Never write a credential, token, key or live secret into a document. Name where it lives.
8. Never claim a live vendor call, deployment or test succeeded without recorded evidence in `docs/verification/` (`test_no_live_vendor_claims.py`).

## Style and portability

- One topic per file; link instead of copying. Relative links only. Filenames are case-sensitive on Linux: match case exactly. New engineering files are lower-case with hyphens; established spec names stay. The Runbook filename has spaces (a known wart; renaming would break 103 citations): link it with `%20`.
- Line endings LF. No em dash in product copy; keep documents plain too.
- Size guide: `CLAUDE.md` under 200 lines; `AGENTS.md` well under 32 KiB (Codex stops reading at its limit); a rules file under about 60 lines; a topic document under about 25 KB unless it is a single normative record.

## Checks (read-only)

Run before declaring a documentation change done. None of them changes a file.

- Links resolve: for each relative Markdown link in the changed files, confirm the target exists (`ls`).
- No stale vocabulary in current documents: search `docs/` outside `history/` for retired names (matrix, Save Matrix, PFI, Celery, retake, Company DNA, `claude.md` as a path) and for the three forbidden claim phrases.
- Counts: `wc -c CLAUDE.md AGENTS.md` and the line count of `CLAUDE.md`.
- Every row added to the register's Pending table names what it touches.
