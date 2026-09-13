"""Situating context on a chunk, and validity intervals on the evidence ledger.

Revision ID: 0091_contextual_evidence
Revises: 0090_agent_action_ledger
Create Date: 2026-09-09

RPN-AI-UP-001 W6.2 and W6.4.

THIS CHAINS TO 0090, WHICH CURRENTLY CHAINS TO 0088
-----------------------------------------------------
W3 owns `0089_agent_policy_and_memory_provenance` and it has not landed yet, so
`0090_agent_action_ledger` names 0088 as its parent for the moment. That is
0090's to repoint, not this revision's. What matters here is that 0091 names
0090 rather than 0088: naming 0088 would give the history TWO heads, and a
rolling deploy applies one head while the other silently never runs. That is
exactly the failure `tests/test_db_enum_parity` asserts a single head to catch.

PART ONE: `context_chunks` GAINS A PREFIX COLUMN, NOT A LONGER `content`
------------------------------------------------------------------------
The generated situating context is MODEL OUTPUT. It goes in `context_prefix`,
beside the verbatim text rather than inside it, mirroring the separation
`candidate_projects` already draws between `ai_interpretation_json` and
`evidence_json`. `content` stays exactly what a candidate or a client wrote,
because retrieval returns it, the evidence ledger's `text_ref` points at it,
and Siddhi cites it. A prefix folded into `content` would make a model's
summary quotable as a candidate's own words, and nothing downstream could tell
the two apart afterwards.

`prefix_model` and `prefix_generated_at` are the same provenance pair
migration 0062 added for `embedding`, for the same reason: the text of a
prefix says nothing about what produced it, and this index will outlive more
than one model id.

WHY `content_tsv` IS REDEFINED, AND WHY THAT IS NOT A CONTRADICTION
--------------------------------------------------------------------
W6.2's measured gain has two halves. Contextual EMBEDDING takes top-20 failure
from 5.7% to 3.7%; contextual LEXICAL indexing takes it to 2.9%. The second
half only happens if the prefix reaches the tsvector, and `content_tsv` is a
GENERATED column over `content` alone.

So it is dropped and recreated over `content` plus the prefix. Three things
make that safe rather than a leak:

  * it is still GENERATED. Migration 0054's reasoning holds unchanged: a
    tsvector maintained by application code silently stops matching the row the
    first time something writes content by another path.
  * `content` is unchanged, so every reader and every citation is unchanged.
  * a tsvector is a SEARCH INDEX. Nothing renders it, nothing quotes it, and
    `to_tsvector` output is stemmed lexemes rather than readable prose.

The GIN index depends on the column, so the order is: drop the index, drop the
column, add the column with the new expression, recreate the index. Dropping
the column first would fail or cascade, and one of those two loses the index
silently.

`coalesce(context_prefix, '')` rather than a bare concatenation: `text || NULL`
is NULL in Postgres, and every chunk written before this migration has a NULL
prefix, so the bare form would empty the tsvector of the ENTIRE existing index
and the keyword half of retrieval would match nothing. It would not raise.

PART TWO: TEMPORAL VALIDITY ON THE LEDGER, AND NOT A GRAPH DATABASE
---------------------------------------------------------------------
The transferable idea from the temporal knowledge graph work is that a claim
has a validity interval. The evidence ledger is already an entity-claim graph
in relational form, so this is five columns rather than a second answer to
"where does evidence live".

What it buys: Miti can distinguish "the candidate used Kafka five years ago"
from "the candidate currently operates Kafka systems", which is what the
Trajectory and Potential dimension needs and cannot currently express.

THE SPLIT BETWEEN THE TWO TABLES IS DELIBERATE
------------------------------------------------
`evidence_items` gets all five. An item is a reference to a SOURCE, so it is
the only row that can answer "when did the thing happen" (`event_date`) and
"when was the document that says so written" (`source_date`). Those two are
routinely different, and conflating them is how a 2019 achievement described in
a resume uploaded yesterday reads as recent.

`evidence_claims` gets three: `valid_from`, `valid_until`, `last_verified_at`.
A claim is an assertion the product makes, so it has a validity interval and a
last-checked stamp, and it has no event and no source of its own -- those
belong to the items standing behind it. Adding `event_date` to a claim would
invite a reader to take it as authoritative over the items it summarises, and
the two would then disagree with nothing able to say which was right.

`evidence_claim_links` gets NOTHING. It records which SIDE of a claim an item
sits on. A stance has no validity interval; the item's does.

WHY DATES AND NOT TIMESTAMPS FOR FOUR OF THE FIVE
---------------------------------------------------
`event_date`, `source_date`, `valid_from` and `valid_until` come from documents
that state a month at best. Storing "March 2021" as a timestamptz invents a
time of day and a zone, and every later comparison then depends on the zone of
whoever is reading. `last_verified_at` is an event in OUR system, so it is an
instant and is timestamptz, like every other `*_at` column in this schema.

NO COLUMN WIDTH CHANGES ANYWHERE
----------------------------------
Nothing is widened, narrowed or retyped. Every vector column keeps its 1024.
If a future embedding move needs a different width it is a dual-column
migration with a shadow read, never an in-place swap, and never a NULL of the
old column before the new one is proven.

NO INDEX ON THE TEMPORAL COLUMNS
----------------------------------
Deliberate. Nothing queries on them yet, and an index nobody reads is write
cost on every ledger insert plus a thing the next reader has to work out the
purpose of. Add one with the query that needs it.

DOWNGRADE
-----------
Drops the added columns and restores `content_tsv` to its 0054 expression and
its index. The prefixes themselves are lost, which is correct: they are
regenerable model output, and `pickready.reconcile_context_index` finds a
document with no chunks rather than a chunk with no prefix, so a downgrade
followed by an upgrade needs a re-index rather than a restore.
"""
from alembic import op

revision = "0091_contextual_evidence"
down_revision = "0090_agent_action_ledger"
branch_labels = None
depends_on = None

_TSV_INDEX = "ix_context_chunks_tsv"

#: The tsvector expression as migration 0054 wrote it, quoted verbatim so the
#: downgrade restores the exact column it found rather than this author's
#: recollection of it.
_TSV_0054 = "to_tsvector('english', content)"

#: The contextual expression. `coalesce` is load-bearing: see the docstring.
_TSV_CONTEXTUAL = (
    "to_tsvector('english', content || ' ' || coalesce(context_prefix, ''))"
)

#: (table, column, type) for the temporal columns. Written out as data so the
#: upgrade and the downgrade cannot disagree about which table got which.
_TEMPORAL: tuple[tuple[str, str, str], ...] = (
    ("evidence_items", "event_date", "date"),
    ("evidence_items", "source_date", "date"),
    ("evidence_items", "valid_from", "date"),
    ("evidence_items", "valid_until", "date"),
    ("evidence_items", "last_verified_at", "timestamptz"),
    ("evidence_claims", "valid_from", "date"),
    ("evidence_claims", "valid_until", "date"),
    ("evidence_claims", "last_verified_at", "timestamptz"),
)


def upgrade() -> None:
    # ── The prefix, its provenance, and the lexical half that reads it ──────
    op.execute("ALTER TABLE context_chunks ADD COLUMN context_prefix text")
    op.execute("ALTER TABLE context_chunks ADD COLUMN prefix_model text")
    op.execute(
        "ALTER TABLE context_chunks ADD COLUMN prefix_generated_at timestamptz"
    )

    # Order matters: the GIN index depends on the generated column.
    op.execute(f"DROP INDEX IF EXISTS {_TSV_INDEX}")
    op.execute("ALTER TABLE context_chunks DROP COLUMN content_tsv")
    op.execute(
        "ALTER TABLE context_chunks ADD COLUMN content_tsv tsvector "
        f"GENERATED ALWAYS AS ({_TSV_CONTEXTUAL}) STORED"
    )
    op.execute(
        f"CREATE INDEX {_TSV_INDEX} ON context_chunks USING gin (content_tsv)"
    )

    # ── Temporal validity on the ledger ─────────────────────────────────────
    for table, column, column_type in _TEMPORAL:
        op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def downgrade() -> None:
    for table, column, _ in reversed(_TEMPORAL):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")

    op.execute(f"DROP INDEX IF EXISTS {_TSV_INDEX}")
    op.execute("ALTER TABLE context_chunks DROP COLUMN content_tsv")
    op.execute(
        "ALTER TABLE context_chunks ADD COLUMN content_tsv tsvector "
        f"GENERATED ALWAYS AS ({_TSV_0054}) STORED"
    )
    op.execute(
        f"CREATE INDEX {_TSV_INDEX} ON context_chunks USING gin (content_tsv)"
    )

    op.execute(
        "ALTER TABLE context_chunks DROP COLUMN IF EXISTS prefix_generated_at"
    )
    op.execute("ALTER TABLE context_chunks DROP COLUMN IF EXISTS prefix_model")
    op.execute("ALTER TABLE context_chunks DROP COLUMN IF EXISTS context_prefix")
