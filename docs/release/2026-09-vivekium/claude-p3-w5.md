# CLAUDE.md section draft: Phase 3 WP5 (recording, retention, infra)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.
Migration `0126_recording_retention` (the orchestrator re-chains it).

## THE VIDEO INTERVIEW MODE IS DELETED. THE SESSION RECORDING STAYS, IN SEGMENTS

**SUPERSEDES the 2026-09-06 "Dual-mode assessment" subsection** (mark it in
place): there is one assessment mode. The video-interview routes (start,
question mark, whole-file upload, finalize, status), their schemas, the
answer-transcription half of `services/video/processing.py` (audio
extraction, Transcribe over the recording, transcript segmentation, the
transcript writer, the completion it triggered), `recordings.mark_question_shown`
and `services/assessment_canonical.py` are gone.
`tests/test_video_interview_mode_removed.py` keeps them gone and ratchets the
names the conversation and frontend packages delete in parallel (the list may
only shrink). `MODE_VIDEO_INTERVIEW` and `RECORDING_VIDEO_INTERVIEW` survive in
`models/dual_mode.py` as READ-ONLY vocabulary so an old row still loads and is
labelled; nothing writes either value, and no assessment route takes a `mode`.

- **THE RECORDING ARRIVES AS SEGMENTS, AND NO REQUEST HOLDS MORE THAN ONE
  PART.** The deleted upload read the whole session, up to a gigabyte, into
  the shared API task with one `await file.read()`. Now each segment is one S3
  multipart upload (`video_recording_segments`), fed one part of at most
  `video_part_max_bytes` (16 MiB) per request, with a new segment after every
  device recovery or reload. Every size rule runs BEFORE the bytes leave for
  S3: a part under 5 MiB IS the segment's last part whether or not the client
  says so, because S3 would otherwise refuse the whole segment at completion,
  after the session is over and nothing can be re-sent.
- **The segment row is LOCKED for each part and for completion.** Two
  requests for one segment each read `parts_json`, added their ETag and wrote
  it back, so the second silently dropped the first part and S3 joined the
  segment without it. A lost part is lost seconds of a candidate's video with
  nothing reporting it.
- **A completion whose upload the store no longer holds is settled from the
  OBJECT**, by HEAD: an earlier attempt completed or aborted it and its
  database write did not land. Asking the upload again would fail the same way
  every hour for ever.
- **Finalize dispatches AFTER COMMIT** (`dispatch_after_commit`). A tab closed
  before finalize is finalized by the hourly repair from the STORE's own list
  of parts, so a part S3 accepted is never lost to a database write that
  rolled back.

## THE PURGE DATE IS STORED, AND THE SWEEP READS THE EARLIER OF TWO STORED DATES

Owner decision D4: a recording is purged at whichever comes first, 90 days
after the session or the job-closure purge. **SUPERSEDES
`assessment_media_retention_days = 0`** ("no time-based purge"): it is 90 and
a validator refuses zero or less, because a non-positive window stamps a date
in the past and the sweep would delete every recording the moment it was
stored.

- **`media_purge_due_at` is STAMPED ONCE at finalize** as the earlier of the
  session end plus the setting and the job's closure purge if the job is
  already closed. A settings change never moves a date a candidate was given.
- **The sweep reads `LEAST(media_purge_due_at, jobs.assessment_purge_due_at)`
  at SWEEP time**, so a job closed after the stamp still wins. `LEAST` ignores
  NULL, so an open job contributes nothing.
- **The S3 lifecycle rule is the BACKSTOP, not the clock.**
  `assessment-compressed/` expires at `assessment_media_retention_days` (90;
  the compressed object is written after the session ended, so the rule can
  only fire after the stored date), `assessment-raw/` at 7 days,
  `voice-answers/` at 1 day. **A HEAD-confirmed delete is not a purge on a
  versioned bucket**: the delete places a marker and the bytes stay readable
  at their version for `noncurrent_retain_days` (thirty in pilot), so each
  media prefix expires noncurrent versions after ONE day. All three are kept
  out of the Infrequent Access transition (30-day minimum charge).
  `tests/test_media_retention_d4.py` compares the module variable with the
  setting.

## THE REPAIR SWEEP, AND WHAT A STATUS ALONE CANNOT TELL YOU

`pickready.reconcile_assessment_recordings`, hourly, in `schedule.py` and all
three environments' scheduler blocks. Before it, a failed raw deletion
incremented `raw_delete_failures` and nothing ever looked at the row again.
Four passes, each asking the TABLE:

1. raw segment deletions that did not confirm (per segment, `raw_deleted_at`);
2. orphaned recordings (session over, nothing arrived for the grace):
   finalized from the store and processed;
3. finalized recordings no processing run picked up (a lost invoke);
4. **runs killed from outside.** A Fargate task killed mid-transcode commits
   nothing more, so its row said `compressing` for ever while the bucket's
   seven-day raw rule deleted the only copy. `processing_started_at` is
   stamped when the pipeline leaves `uploaded`; past four ffmpeg ceilings plus
   the orphan grace (DERIVED, no new knob) the row gets the failure state of
   its step, logged at ERROR and retryable by the hiring team. Slow is not
   dead: inside the bound the run is left alone.

One recording at a time, committing as it goes; one store failure stays one
failure. Raw objects are deleted only after the compressed object is verified
(HEAD size and ffprobe duration); a failed verification deletes nothing.

## THE BUCKET POLICY DENIED EVERY MULTIPART UPLOAD, AND NOBODY COULD HAVE SEEN IT

Read from pilot on 2026-09-24: `DenyUnencryptedObjectUploads` denied
`s3:PutObject` with `StringNotEquals` on `x-amz-server-side-encryption =
aws:kms`, and the bucket held ZERO objects under any prefix. `StringNotEquals`
also matches when the header is ABSENT, and UploadPart and
CompleteMultipartUpload authorize as `s3:PutObject` with no encryption header,
so every part of every recording would have been refused. `video/storage` sent
`AES256`, refused by the same statement, and **`object_storage.put` still does
(resumes, compliance, project intake): a Phase 7 hunk, not yet applied.**

- **The policy is three facts now**: an encryption other than `aws:kms` is
  refused (`StringNotEqualsIfExists`), a KMS key other than this
  environment's is refused, and `aws:kms` without a key id is refused (S3
  would use the AWS-managed key). A request naming nothing (every UploadPart)
  is encrypted by the bucket default, which is this key.
- **`video/storage._sse()` sends `aws:kms` AND `S3_KMS_KEY_ID`, and REFUSES
  while the key id is empty** rather than writing under the wrong key or
  being denied one part at a time. `S3_KMS_KEY_ID` is the key's ARN (the
  policy compares the string), set on the ECS services and the task worker.
- **The application prefixes were missing, the 2026-09-05 defect again.**
  `assessment-raw`, `assessment-compressed` and `voice-answers` joined
  `application_prefixes`, with `s3:AbortMultipartUpload` and
  `s3:ListMultipartUploadParts`.
- **THE TASK WORKER HAD NO OBJECT-STORE GRANT IN ANY ENVIRONMENT**, yet the
  purge, the repair, the closure purge and the erasure reconciler all run
  there. Each would have answered AccessDenied on its first real object.
  `task_worker_s3` attaches the module's access policy.
- **Transcribe moved to the task worker** (spoken answers are a Route.LAMBDA
  task); the ECS agent lost the grant and the three `TRANSCRIBE_*` values.

## THE RECORDING IS THE JOB'S HIRING TEAM'S, NOT THE TENANT'S

`view_review_screen` is SCOPED for the Recruiter, the Hiring Manager and the
Interview Manager, and `require_capability` alone answered "may this role
review at all", so any Recruiter in the tenant could mint a presigned URL for
any job's recording. `assessment_video_access.require_hiring_team` runs
`rbac.authorize` over the job for every recruiter-facing recording route and
the staff retry, before the closure gate (410). The Client Super Admin and the
HR Manager reach every job in their own tenant.

## STILL OPEN, SAID OUT LOUD

- The concat demuxer joining segments from two MediaRecorder runs has been
  exercised only with the media tools stubbed; the first real two-segment
  recording on pilot is the proof.
- `voice_answers` objects are not yet in the enumerators or the repair
  (their table lands with the conversation package); the orchestrator applies
  that hunk at integration.
- Pilot's `transcribe_enabled` is false in `terraform.tfvars` (gitignored);
  turning it on is the deploy stage's apply.
