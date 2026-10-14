# Legacy reset survey (brief record)

**Status:** history record. Not an instruction.
**Path kept on purpose:** `backend/app/scripts/legacy_reset.py --survey` writes its output to this path. Running that command overwrites this file with a fresh survey; commit or discard the result deliberately.

- **What it was:** a read-only survey taken 2026-08-28 against a scratch database at revision `0062_embedding_provenance` (51 tables, each classified), listing purged rows per tenant and per job, edge cases, schema findings, object-store reconciliation and the order of the reset steps (export with a test restore, dry run, apply, re-embed before regrading, regrade).
- **Headline:** the reset assumed gate G1 blocked evaluation on a live path. It did not: G1 was reachable from no route or worker, so the archive-and-mark step could not rely on it, and `--purge --confirm` refuses while that holds (it now checks `inspect_gate_wiring`).
- **What became of it:** Part A went live (2026-08-29 onward). The 2026-09-25 release scrapped the legacy tables behind counting guards; the classification of what was dropped and what is kept as history is now [../operations/LEGACY_TABLES.md](../operations/LEGACY_TABLES.md).
- **Original:** `git show 73103c3:docs/history/LEGACY_RESET_SURVEY.md`.
