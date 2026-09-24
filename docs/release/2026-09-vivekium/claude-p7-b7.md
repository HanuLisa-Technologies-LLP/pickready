# CLAUDE.md section draft: Phase 7 WP-B7 (silent broad handlers, AD-1)

Draft for the orchestrator to fold into the release's single top section. Written
in the file's own voice; nothing here edits `claude.md` directly.

## A HANDLER THAT CATCHES EVERYTHING AND LEAVES NO TRACE IS NOT `pass`, AND THE SWEEP NOW SEES IT

`test_no_silent_degradation.py` swept for one SHAPE, `except ...: pass`, and
the 2026 audit found the others walking straight past it. None of these was
`pass`, so every one of them passed:

- **`api/email_senders._verify_sns_signature`** wrapped the RSA check in
  `except Exception: return False` and logged nothing. A forged message and a
  real SES topic whose every event stopped verifying (a rotated certificate, a
  canonical string built from the wrong field list, a bug) both answered 403,
  so an outage of delivery tracking looked exactly like a quiet mailbox. Every
  refusal now writes `email_senders.sns_signature_invalid reason=<why>` or
  `email_senders.sns_cert_fetch_failed reason=<class>` at WARNING, the handlers
  catch only what a hostile message can cause (`InvalidSignature`,
  `ValueError`, `TypeError`; `httpx.HTTPError` and `ValueError` for the
  certificate fetch), and a programming error PROPAGATES as a 500 SNS retries.
- **`verification/email._link_findings`** caught everything around
  `lifecycle_email.link_defects` "because an unknown type raises". It does not:
  an unknown type is answered by table lookup with no defects. The handler
  could only ever catch a bug, and it turned that bug into "the links are
  fine", the one answer a link critic must never give by accident. The try is
  GONE.
- **`answer_classification`**: both parses narrow to `ValueError`
  (`JSONDecodeError` is one), and a JSON list or scalar is refused by an
  explicit shape check rather than reaching the same verdict through an
  `AttributeError` a catch-all happened to absorb.
- **`interview_telemetry`** keeps "an observer never raises into the request"
  and stops being silent about it: each emitter logs
  `interview_telemetry.emit_failed emitter=<which> err=<class>` at WARNING.
  **The CLASS, never the message or a traceback**: an exception raised while
  formatting a field can quote that field, and the fields are what this module
  keeps out of the log. An unreadable latency is COUNTED
  (`unreadable_latencies` in the summary) instead of passed over, so a p95
  computed over half the turns says so.
- **`agent_loop._estimated_tokens`** narrows to `(TypeError, ValueError)`, the
  only two things `json.dumps(default=str)` still raises.

### The third sweep

A handler is SILENT when it is BROAD (bare, `Exception`, `BaseException`, or a
tuple holding one) and its body neither re-raises, nor logs (any `.debug`,
`.info`, `.warning`, `.error`, `.exception`, `.critical`, `.log` call), nor so
much as READS the exception it bound. Reading it is the floor of recording an
outcome: `failure = type(exc).__name__` puts the failure in the result a caller
receives, and `return None` does not.

- **Absolute on the Part A packages, a set ratchet everywhere else**, the same
  two rules the file already applied to fallbacks and swallowers.
  `LEGACY_SILENT_HANDLER_FILES` is a MEASUREMENT with an owner per entry, and
  two entries are JUSTIFIED rather than owed: `projects/parsers.py` (a corrupt
  file returns an artifact carrying `supported=False` and a written limitation)
  and `projects/invisible_text.py` (a page that cannot be read is counted in
  `stream_failures`, which the scan reports).
- **A NARROW handler is deliberately out of scope.** `except ValueError:
  return None` around a parse is a decision about one named failure; the defect
  is the handler that cannot tell a hostile input from a bug in the line above.
- **The detector is pinned both ways** (four silent shapes it must find, six
  accounted shapes it must pass), because a sweep with a blind spot is worse
  than none: the green result is what stops anybody looking.
- **The behavioural half of the file no longer tests `services/orchestration`.**
  That package had no production caller and is deleted in this release; a
  guard test over a module nothing calls proves the module and nothing else.
  The same refusals are now asserted on the code that runs: `hiring.gates`
  (G1 asks the TABLE before the stamp; G4 needs a person, and there is no
  automatic disposition), `agents.provenance` (an artifact-bearing stage with
  no artifact is reported as "a timestamp"), `agents.envelope` (no principal,
  a cross-tenant principal, or a job id in the correlation slot is refused) and
  `agents.artifacts` (an incomplete contract is refused at publish).

## THE DATABASE IS AUTHORITATIVE. AN A2A ARTIFACT IS A HAND-OFF AND A CITATION, NEVER A STORE

`docs/spec/ARCHITECTURE.md` AD-1, CONTRACT v4 item 4. Every fact the product
acts on, shows or bills is read from a table through the RLS-aware session. An
`Artifact` is a typed, versioned, tenant-scoped DESCRIPTION of rows that
already exist, for exactly two jobs: the typed hand-off between agents, and
the ids a trace or a stage record cites.

- **Built FROM committed rows, never the other way round.** Both publishers
  (`ppi.publish_tatva_matrix`, `matching.publish_ai_scores`) run after the
  rows are durable and return None on failure; deleting either call changes no
  stored value, grade, tier or order, which is the practical test.
- **No table stores an artifact payload, and there must never be one.** It
  would be a second answer to "what are this job's criteria" that disagrees
  with the rows exactly after a human edit, a read path RLS does not see, and
  a copy the erasure cascade does not know about.
- **Pinned by `tests/test_architecture_database_authoritative.py`**: the
  artifact, provenance and envelope modules import no SQLAlchemy, no
  `app.models` and no `app.core.db` (lazy imports included), and no ORM table
  or column is shaped as an artifact store.

## Supersessions

- **2026-08-29 spec-doc6 "Anti-slop rules, CI-enforced", "No silent
  fallbacks (no `except Exception: pass`, no bare `except` ...)"**: AMENDED.
  The enforced rule is now the PROPERTY (a broad handler must re-raise, log or
  read what it caught), not the `pass` shape alone.
- **2026-08-05 conversational agent, "Telemetry logs labels, keys and
  timings"**: unchanged, and extended: a failure to emit is logged by
  exception CLASS only, for the same reason.
- **ARCHITECTURE_DIRECTION_2026-08-28.md (advisory)**: wherever it reads as
  artifacts being a store or a source of record, AD-1 wins.
