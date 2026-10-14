# Agent workflow and context management

**Status:** current. **Reviewed:** 2026-10-14 (against Claude Code memory and best-practice guidance and the Codex AGENTS.md guide as published that day; and against what a live session injected in this repository).
Applies to Claude Code and Codex alike. `CLAUDE.md` and `AGENTS.md` carry the short version; this is the reasoning and the checklists.

## What loads, and what it costs

| Source | Loaded | Notes |
|---|---|---|
| `CLAUDE.md` (Claude) / `AGENTS.md` (Codex) | Every session, in full | Keep short. Claude reads `AGENTS.md` only when no `CLAUDE.md` exists, so this repository keeps both. Codex stops at 32 KiB. |
| `.claude/rules/*.md` with `paths:` | When a matching file is read, written or edited | Domain digests that point to `docs/engineering/`. Codex does not read them. |
| `docs/engineering/*.md` | Only when opened | The full rules per domain. Open the one you need. |
| `docs/spec/*.md`, `docs/architecture/*.md` | Only when opened | Open one; do not sweep the folder. |
| `docs/history/` | Never, unless a record is cited | Provenance, not truth. |

Imports (`@path`) load at launch and do not reduce cost; `CLAUDE.md` therefore links with plain paths and imports nothing.

## Working practice

1. **Start narrow.** Read `CLAUDE.md`, `git status`, then the one document or file the task names. Do not scan or re-read the repository. Do not open a document to find out whether it is relevant: use [../README.md](../README.md) and [where-to-change.md](where-to-change.md).
2. **Search before opening.** A targeted `grep` or the Glob/Grep tools first. Graphify is optional (W-20): use it for unfamiliar architecture, cross-module dependencies and complex relationships when `graphify-out/` exists and is current; use direct search for simple tasks; never run it unnecessarily.
3. **Bound every output.** Pipe through `head`, `tail`, `wc -l`; use `git diff --stat`, `--name-only`, `-n`. Read line ranges of large files. Do not re-read a file you just edited.
4. **Plan, then edit.** For a change that touches several files or an unfamiliar area, explore and plan first; for a one-line fix, just do it.
5. **Verify with a check that can fail.** Run the targeted test while developing and the relevant suite once at the end ([testing.md](testing.md)). Show evidence (output, row count, response), not assertion.
6. **Subagents only for independent work.** Never two on overlapping files; never one beside a container build or a second test run on the same database (CPU and database contention looks like a hang). Use one for broad investigation that would flood the main context, or for a fresh-context review.
7. **Keep documents in step** with the code in the same change ([documentation-policy.md](documentation-policy.md)). Report what changed, what it cost, and what is still open, briefly.
8. **Reset early.** `/clear` between unrelated tasks. After two failed corrections of the same mistake, clear and restart with a sharper prompt. Use `/compact` with instructions rather than waiting for auto-compaction.
9. **A note is a hint, not a fact.** Anything a previous session wrote about code (a summary, a memory, a handoff) is verified against the working tree before it is acted on.

## Compaction

When compacting, preserve: the task and acceptance check; files changed and why; the exact test and verification commands; decisions made this session and any new owner statement (with date); unresolved questions; the branch and `HEAD` sha. Drop exploration output and file contents (they can be re-read).

## Handoff (session continuity)

A handoff is a short Markdown note a session writes when it stops with work open, and the next session reads before touching anything. It is Git-verified: it records the sha it was written at so the next session can check how stale it is.

Write it to `tmp/HANDOFF.md` (this checkout excludes `tmp/` through `.git/info/exclude`; confirm with `git check-ignore tmp/`; never commit it). Template:

```
# Handoff
Written: <date> at <branch> @ <git rev-parse --short HEAD>
Task: <one sentence and its acceptance check>
Done: <files changed, one line each; commands that passed>
Not done / next: <ordered, concrete>
Decisions and owner statements this session: <dated, with register IDs>
Open questions: <links to OPEN.md IDs>
Uncommitted work: <paste of `git status --short`>
```

Resuming: run `git rev-parse --short HEAD`, `git status --short`, and `git log --oneline <note sha>..HEAD`. If the sha differs or the status differs, treat every claim in the note about code as unverified and re-check it. Delete the note when the work lands. Durable facts do not belong in a handoff: they go to the register, the owning document or a commit message.

## Extensions installed on the maintainer machine (audit, no change made)

Observed in a live session on 2026-10-14. Recommendations only; changing configuration needs owner approval.

| Item | What it does | Cost and overlap | Recommendation |
|---|---|---|---|
| Graphify `PreToolUse` hooks in `.claude/settings.json` (and the untracked `.codex/hooks.json`) | Adds a "MANDATORY graphify query" paragraph to the result of every Bash, Grep, Read and Glob call | Repeats the same text every call; contradicts W-20 | Remove (pending change 5). Keep the graphify skill, the graph and the git rebuild hooks. |
| Git `post-commit` and `post-checkout` hooks | Rebuild the graph (AST only) | Launches CPU work after commits; contend with test runs | Keep; do not run the authoritative suite beside it. |
| Impeccable design hooks (`.claude/settings.local.json`, untracked) | Checks UI files after edits and on stop | Runs on every edit and stop | Keep for frontend work. Consider scoping to UI paths. |
| User-level plugins that inject at session start (a terse-style mode, a "lazy developer" mode, a skills-discipline skill, an editor-side session summary) | Each adds instructions every session and some repeat on every prompt | They overlap in purpose and can contradict this repository's rules | Keep the set small; prefer one style mode. Project rules win over any plugin. |
| A very large skills and agents catalogue (several hundred entries) | Listed in the system prompt | A fixed context cost before the first message | Disable unused plugins per project (`enabledPlugins` in project settings) when owner approves. |
| Memory systems: Claude auto memory, a plugin memory store, and per-session summaries | Three places that can hold "facts" | Stale or contradictory notes | Treat all as hints. Repository documents win. Do not store repository facts in memory that the repository already records. |

## Instruction file hygiene

- Two contradicting instructions make the agent pick one arbitrarily: review `CLAUDE.md`, `AGENTS.md`, `.claude/rules/` and user-level files together when behaviour looks wrong.
- Emphasise at most one or two lines. Remove a rule the agent already follows without it. Convert a rule that must never be broken into a test or a hook.
- `CLAUDE.md` is for what the agent cannot infer from code. File-by-file tours, long tutorials and anything that changes weekly belong elsewhere or nowhere.
