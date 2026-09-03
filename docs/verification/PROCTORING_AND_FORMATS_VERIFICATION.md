# Verification: proctoring and question formats

What was actually executed for the 2026-09-02 proctoring and question-format
work, and what was not. Written to the standard this repository already holds
itself to: a claim here means a command was run and its output read, and
anything unproven is stated as unproven rather than left out.

## Automated

| Suite | Command | Result |
|---|---|---|
| Backend, whole suite | `python -m pytest -q` against the `docker-compose.test.yml` stack | **5188 passed, 1 skipped** in 739s |
| Frontend, whole suite | `npx vitest run` | **240 passed** in 33 files |
| Frontend types | `npx tsc --noEmit -p tsconfig.json` | clean, exit 0 |
| Frontend lint | `npm run lint` | clean |
| Frontend production build | `npm run build` | `Compiled successfully` |
| Contrast tokens | `node scripts/check-contrast.mjs` | `All 19 contrast assertions pass.` |
| Design drift | `node scripts/impeccable-gate.mjs` | `4 findings, 4 documented exceptions, 0 to answer for. Clean.` |
| Dead code | `python scripts/check-dead-code.py` | 1695 symbols checked, none dead |
| Import cycles | `python scripts/check-import-cycles.py` | 304 modules, no cycle |

The single skip is the one declared in `docs/operations/SKIPS.md`
(`VOYAGE_CONTEXT_4` unset). No new skip was introduced. The backend count rose
from the 3247 recorded in `TEST_BASELINE.md` to 5188.

## The security scan, and why its check is red

The `Security scan` job passes. The `Trivy` check that consumes its results
fails, and the reason is worth writing down rather than leaving for the next
reader to rediscover.

**The scanner had never successfully run before.** The workflow comment records
that the action was pinned to a tag that does not exist, so the job died in
setup on every run since it was written. This pull request is the first time it
has produced results, so GitHub has no baseline on `main` and reports every
finding on a touched file as new.

Two alerts were genuinely introduced by this work and both are fixed:

| Alert | What it was | Resolution |
|---|---|---|
| `DS-0031`, critical | A build argument named `ALLOW_MISSING_HUGGINGFACE_TOKEN` read as an exposed credential. It never held one: the token arrives on a BuildKit secret mount. | Renamed to `SKIP_GATED_MODEL_DOWNLOAD`, which is also what it does. |
| `CVE-2022-0235`, high, and `CVE-2020-15168` | `node-fetch` 2.1.2, pulled in by face-api.js through a 2019 tfjs-core. Verified it reaches neither worker bundle nor the Next client output, because tfjs-core uses native fetch in a browser build. | An npm override to `^2.6.7`, the mechanism this package.json already uses. Every copy now resolves to 2.7.0. |

The remaining seven "new" findings are **pre-existing**, verified individually:
`infra/modules/alb/main.tf` carries two of them and is not changed by this work
at all, and the unrestricted egress rule and public-subnet finding in
`infra/modules/network/main.tf` are both present on `main`. They are reported
here only because the scanner started working and those files sit near a
change. `DS-0026`, no HEALTHCHECK, is raised against all three Dockerfiles
including the two that predate this work: this platform health-checks
containers in the ECS task definition and the compose file rather than in the
image.

**Twenty-one findings on the infrastructure modules are now visible for the
first time and none of them has been triaged.** Some are deliberate and
documented in the Terraform itself, such as the egress rule whose comment names
the model provider, the vendor APIs and SMTP. Triaging the set is real work and
it is not part of this change.

## Manual verification performed

### Composition is evidence-dominant across roles

The real composer and the real validator, run over realistic matrices for all
four grades on both role classifications. Full table and the ambiguity it
resolves are in `docs/spec/ASSESSMENT_QUESTION_FORMATS.md`. Summary:

| Share | Range across the eight configurations |
|---|---|
| Open-ended, which rule 1 enforces | 87.2% to 94.7% |
| Evidence-based within the anchorable part, which rule 1b enforces | 73.7% to 90.9% |
| Supporting formats | 2 to 5 questions per assessment |

Every configuration is accepted by the validator, and every one fits its
role's duration. Evidence-based alone is 35.9% to 63.7% of the whole
assessment, which is the documented judgement call.

### No media is persisted anywhere

- `tests/test_proctoring_no_media.py` and
  `tests/test_proctoring_scoring_isolation.py`: **72 passed**.
- A direct source sweep of `services/proctoring/` and `api/proctoring.py` for
  `put_if_absent`, `put_object`, `upload_file`, `write_bytes`, `tempfile`,
  `NamedTemporaryFile`, `mkstemp`, `shutil` and any `open(..., "w"/"wb"/"a")`
  returns nothing.
- The migrated schema was inspected directly. Across `proctoring_sessions`,
  `proctoring_events`, `proctoring_reports` and `assessment_answers` there is
  no `bytea` column, no blob, and no column holding an object-storage key or
  URL. The only array is `face_descriptor_baseline`, 128 floats, pinned to
  that width by a database CHECK, and it is not an image and cannot be
  inverted into one.

### The delivered-report number ban

A gap was found and closed during integration: `8 out of 10 on the rubric` and
`8 out of 10 for depth` passed the entire ban, because the shared detector's
out-of-N pattern is deliberately narrow to protect interviewer speech, where
`7 out of 10 services` is ordinary technical content. A report-specific rule
now refuses any out-of-N in a delivered document, alongside the two
report-specific rules that already existed. Interviewer speech is unchanged,
verified in both directions.

### Migration

`0076_proctoring_formats` was applied, downgraded and re-applied against the
test database. `tests/test_db_enum_parity.py` passes, so every CHECK
vocabulary agrees with its Python constant.

## Not verified, and why

These are outstanding items from `proctoring-spec-doc.md` section 11 and
`assessment-spec-doc.md` section 8. None of them can be executed in this
environment, and none should be read as passing.

| Item | Why not |
|---|---|
| Real webcam: phone detection, second person, face absence, covering the lens | No camera on this machine, and no browser session was driven. The rule modules are tested frame by frame against fabricated detections; the models have never been loaded by a running worker. |
| Real microphone: second-speaker detection | No microphone, and the diarization model is gated on Hugging Face so it was never downloaded. The second-voice rule is tested with an injected poster. |
| Attempting every blocked action in a real browser | The assessment page is behind Firebase sign-in, which cannot be completed headless. The lockdown layer is covered in jsdom, where `preventDefault` is real but the clipboard is not. |
| A full assessment on a low-spec machine | Requires a real device and a real browser. |
| The speaker-diarization HTTP contract end to end | The analysis service was never run with real model weights, because the pyannote models require an accepted licence and a `HUGGINGFACE_TOKEN` that this repository does not contain. |
| Any live model call for question generation or answer evaluation | The suite runs with no vendor credential by design. Every generation and evaluation path is exercised against a stubbed router; the new prompts have never been sent to a real endpoint. |
| Celery task execution | Registration, beat wiring and deferred imports are proven by `tests/test_celery_task_imports.py`, and the logic the tasks call is tested directly, but no broker ran them. |

**The honest summary: the logic is proven, the integrations are not.** Before
this reaches a candidate, the webcam and microphone paths need a session on a
real device, and the analysis service needs its Hugging Face licence accepted
and its token installed.
