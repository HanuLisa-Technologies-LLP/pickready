"""Application settings, sourced from environment variables (see /.env.example)."""

#: The wall clock the product is operated on, and the one every rendered time is
#: shown in. India, because that is where the product is sold and where the
#: recruiter reading "10:32" is standing.
#:
#: It lives HERE rather than on whatever happens to schedule work, which is
#: where it used to live: it was `celery_app.conf.timezone`, and the proctoring
#: report read it off the Celery app object so the two could not drift. That
#: coupling was right and the anchor was wrong, because it made a rendering
#: decision a property of the job runner. The scheduler now reads it too
#: (`workers/schedule`), so the same single definition still holds both.
PLATFORM_TIMEZONE = "Asia/Kolkata"

#: What an unpopulated secret holds in Secrets Manager, and what this module
#: turns back into "" before anything reads it.
#:
#: WHY IT EXISTS AT ALL. ECS fetches every secret in a task definition before
#: the container starts, so a secret with no version stops the whole service
#: rather than degrading one feature. Secrets Manager will not accept an empty
#: string, so `infra/modules/secrets` seeds each one with this instead.
#:
#: WHY IT IS NORMALISED HERE AND NOWHERE ELSE. Every "is this configured"
#: check in the product tests for a falsy value: `missing_delivery_keys`,
#: `llm_router.key_for_model`, `checkout_ready`, the Tavily gate. Handing any of
#: them a non-empty sentinel would make an unconfigured credential look
#: configured and fail later, at a provider, with a 401 that reads like a
#: revoked key. Turning it into "" at the boundary means the code that runs is
#: exactly the code that runs when the variable was never set.
#:
#: It is NOT a credential and cannot authenticate to anything. Pinned against
#: the Terraform default by `tests/test_placeholder_secret.py`.
PLACEHOLDER_SECRET = "PLACEHOLDER_NOT_CONFIGURED"

#: The development JWT signing key. A module constant rather than a field, so a
#: production guard can refuse it BY IDENTITY rather than guessing at length or
#: entropy -- and so the default and the thing that rejects it cannot drift.
DEV_JWT_SECRET = "dev-only-secret-change-me"

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://pickready:pickready@localhost:5432/pickready"
    postgres_rls_app_role: str = "pickready_app"
    # The role migrations SET ROLE to before running DDL, or empty to skip.
    #
    # WHY THIS EXISTS (2026-09-11). `DATABASE_URL` used to carry the RDS MASTER
    # credential, which `manage_master_user_password = true` hands to Secrets
    # Manager to ROTATE on a schedule. AWS rotated it seven days after the pilot
    # instance was created, the hand-composed DSN kept the old password, and
    # every database connection in the product failed at once: the API, the
    # health probe, and therefore sign-in. The DSN now carries the least
    # privileged application role (`postgres_rls_app_role`), which owns no
    # object and whose password nothing else rotates, exactly as
    # `infra/modules/rds` has documented the design from the start.
    #
    # That role deliberately has no DDL rights, so the migration job, and ONLY
    # the migration job, escalates to the object owner for the length of its
    # connection. It can, because the login role is a NOINHERIT member of the
    # owner: membership permits `SET ROLE` while NOINHERIT means an ordinary
    # application session holds none of the owner's privileges. Left unset in
    # every runtime that serves traffic, so the escalation is reachable from one
    # container role and not from the API.
    postgres_migration_role: str = ""

    # Connection pool (app/core/db.get_engine). The SQLAlchemy defaults (5 + 10)
    # are small enough that a few concurrent tabs queue for a connection and
    # every request in the queue reads as "slow".
    #
    # THE CEILING IS THE DATABASE'S, AND THE AUTOSCALER COULD EXCEED IT.
    # ------------------------------------------------------------------
    # `db.t4g.micro` has 1 GiB, and RDS derives `max_connections` as
    # LEAST(DBInstanceClassMemory/9531392, 5000), which is about 112, of which
    # three are reserved for the superuser: roughly 109 usable.
    #
    # At 20 + 10 the previous values, four API tasks alone want 120. The ECS
    # target-tracking policy scales to `local.service_count * 2` = 4 on CPU, so
    # the failure was reachable BY LOAD: the autoscaler's response to traffic
    # was what took the database out. And it does not fail as a pool timeout
    # that sheds one request -- Postgres answers `FATAL: sorry, too many
    # connections`, `/health` probes the database too, all four tasks leave the
    # target group, and the product is fully down.
    #
    # 12 + 3 gives 4 x 15 = 60 at the autoscaler's own maximum, leaving room
    # for the Lambda workers (one engine each, account concurrency 10) and the
    # on-demand Fargate agents, which are unbounded and take one engine each.
    #
    # These are the numbers for THIS instance class. Moving to a larger one is
    # the other half of the trade and raises them; the rule is that the product
    # of tasks and (pool_size + max_overflow) stays under the usable ceiling
    # with headroom for the workers, not that these two integers are sacred.
    db_pool_size: int = 12
    db_max_overflow: int = 3
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # Redis. Cache, rate limiting, the proctoring warning counter and the
    # background run-status record. It is no longer a message broker: there is
    # no queue in it, and nothing consumes from it.
    redis_url: str = "redis://localhost:6379/0"
    technical_review_reminder_hours: int = 48

    # -- Background task dispatch --------------------------------------------
    #
    # Three real deployments, never a fallback chain (see workers/dispatch):
    #   aws     invoke Lambda, which runs short work directly and starts an
    #           on-demand Fargate task for long work. Staging and production.
    #   local   run the task in a thread in this process. The docker-compose
    #           stack, which has no Lambda.
    #   record  accept the dispatch, run nothing, remember it. The test suite,
    #           where executing a task would make every route test depend on a
    #           model provider. Refused outright in production.
    task_dispatch_backend: str = "aws"

    # How long a synchronous agent invoke may take before the CLIENT gives up.
    # Deliberately longer than the functions' own timeouts, so the function's
    # ceiling is what stops the work: a client that times out first abandons a
    # call that is still running and, with retries enabled, starts a second.
    agent_invoke_read_timeout_seconds: int = 660

    # Auth
    #: Refused in production by `_refuse_an_unconfigured_jwt_secret`.
    jwt_secret: str = DEV_JWT_SECRET
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    firebase_service_account_json: str = ""

    # Auth-cookie attributes (app/api/deps.py is the single writer).
    #
    # "strict" is correct whenever the browser page and the API are same-site,
    # which covers local dev (localhost:3000 -> localhost:8000; cookies ignore
    # the port) and a production deployment on one registrable domain
    # (app.example.com + api.example.com). It is WRONG when the two live on
    # different registrable domains (a Vercel frontend calling a Railway API):
    # the browser then withholds the cookie on every call and the user can never
    # stay signed in. That deployment must set COOKIE_SAMESITE=none, which the
    # validator below forces to be paired with a secure (https) cookie.
    #
    # COOKIE_DOMAIN is normally empty (host-only cookie, the safest default).
    # Set it to ".example.com" only when the frontend and API are on different
    # subdomains of one domain.
    cookie_samesite: str = "strict"
    cookie_domain: str = ""

    # OTP
    otp_ttl_minutes: int = 5
    otp_max_attempts: int = 5
    otp_cooldown_minutes: int = 15

    # ── LLM and embeddings ──────────────────────────────────────────────────
    #
    # THREE CREDENTIALS PLATFORM-WIDE, ONE PER MODEL. This replaced a 21-slot
    # roster (7 keys each for Groq, Gemini and OpenRouter) that existed to route
    # around three free tiers' failure modes -- a retired model id, an exhausted
    # prepaid balance, an 8000-token-per-minute organisation pool, a withdrawn
    # free-tier model. One paid vendor removes that whole class of problem, so
    # it removes the roster with it.
    #
    # THE MODEL VENDOR CHANGED ON 2026-08-31, from Anthropic to OpenAI, by owner
    # decision. `ANTHROPIC_API_KEY` is gone rather than deprecated: a retained
    # credential is a credential something eventually reads. The embedding
    # VENDOR did not change; the variable its key is read from did.
    #
    # TWO KEYS FOR ONE MODEL VENDOR is unusual and is what the owner has: the
    # reasoning tier and the extraction tier are billed separately. Which model
    # is called with which is DATA in `config/llm_providers.SETTINGS_ATTR_FOR_MODEL`,
    # never a branch in the router, and an absent key for the model being called
    # raises rather than falling back to the other one.
    #
    # All three are mounted from AWS Secrets Manager in a deployed environment
    # and are never composed into a loggable env var, continuing the discipline
    # established when DATABASE_URL was hardened.
    openai_gpt_terra: str = ""
    openai_gpt_luna: str = ""

    # THE EMBEDDING CREDENTIAL IS NAMED AFTER THE MODEL IT UNLOCKS:
    # `VOYAGE_CONTEXT_4` for `voyage-4`. Same convention as the two
    # model keys above, and it replaces `VOYAGE_API_KEY` outright rather than
    # sitting beside it as an alias. One name per thing.
    #
    # READING THE WRONG NAME HERE WOULD NOT RAISE, which is why
    # `tests/test_embeddings.py` pins this exact variable as a literal.
    # `services/embeddings` falls back to deterministic pseudo-random unit
    # vectors when the key is absent, so a mistyped name leaves every retrieval
    # returning meaningless vectors of the right width, with no exception, no
    # log line and no empty result to notice.
    voyage_context_4: str = ""

    # RETAINED AS KEY MATERIAL, AND NOTHING IN THIS TREE DECRYPTS WITH IT.
    #
    # The previous comment here said "this is what decrypts them", which was
    # false: a tree-wide search finds no reader of this setting outside the
    # deploy secret-hygiene test that asserts which services may hold it. The
    # single-vendor consolidation deleted the router that used it along with
    # the three retired providers.
    #
    # It is kept rather than deleted because `llm_provider_keys` still holds
    # encrypted rows, and the key that opens them is not recoverable once the
    # secret is dropped. What a rollback would need is this VALUE plus a
    # decryptor somebody writes; what it must not need is a value nobody
    # thought to keep. Stating that plainly is the difference between a
    # deliberate retention and a grant that looks live and is not.
    llm_key_encryption_secret: str = ""

    # Embedding output width. Pinned to 1024 because `profiles.embedding`,
    # `jobs.embedding` and `context_chunks.embedding` are vector(1024) columns
    # already holding vectors. Changing this is a re-embed of every row, not a
    # config change.
    embedding_dimensions: int = 1024

    # Advanced web search for the BD Portal's AI Reach agent
    # (services/web_research.py). OPTIONAL: with no key the "from the internet"
    # segment returns an empty list with status "unconfigured" and a plain
    # message, while "similar to our customers" keeps working.
    tavily_api_key: str = ""

    # Gmail SMTP only. Values come from the environment and are validated below.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@pickready.app"
    smtp_from_name: str = "ReadyPick"
    smtp_starttls: bool = True
    smtp_ssl: bool = False

    msg91_api_key: str = ""
    msg91_sender_id: str = "PCKRDY"

    # ── Outbound email transport (Corporate Email System spec section 6) ────
    #
    # ONE transport per deployment, selected by DATA, never a fallback chain
    # (the same discipline the model layer keeps). "smtp" is the existing Gmail
    # path, unchanged; "ses" sends through Amazon SES via boto3 with the same
    # permanent/transient failure taxonomy. Owner decision 2026-09-05: this
    # narrows "Gmail SMTP is the only outbound mail path" to
    # transport-as-deployment-data.
    email_transport: str = "smtp"
    #: The SNS topic SES publishes delivery/bounce/complaint events to. The
    #: webhook refuses any message whose TopicArn differs (spec section 8).
    #: Empty means the event endpoint refuses everything.
    ses_sns_topic_arn: str = ""
    #: The SES configuration set every send is attached to. THIS IS WHAT MAKES
    #: DELIVERY TRACKING EXIST: SES publishes an event only for a message sent
    #: under a configuration set carrying an event destination, so a send
    #: without this name attached is one whose outcome nobody ever learns.
    #: Empty sends without one, which is correct for a deployment that has not
    #: provisioned the set: the message is still delivered and the row simply
    #: stays at `sent` rather than pretending to a delivery it cannot observe.
    ses_configuration_set: str = ""

    # ── Corporate sender registration (Corporate Email System spec) ─────────
    #
    # Free/personal mail providers may never be registered as a corporate
    # sender (spec section 2). Comma-separated so an operator can extend it
    # from the environment without a deploy; matching also refuses SUBDOMAINS
    # of a blocked domain, so mail.yahoo.com cannot slip past.
    sender_domain_blocklist: str = (
        "gmail.com,googlemail.com,yahoo.com,yahoo.co.in,ymail.com,outlook.com,"
        "hotmail.com,live.com,msn.com,icloud.com,me.com,mac.com,proton.me,"
        "protonmail.com,aol.com,gmx.com,gmx.net,mail.com,rediffmail.com,"
        "rediff.com,zoho.com,zohomail.com,yandex.com,yandex.ru,fastmail.com,"
        "tutanota.com,tuta.com,hey.com,mail.ru,inbox.com,hushmail.com"
    )
    # The three sender-OTP knobs (ttl, max attempts, resend cooldown) were
    # REMOVED with the mailbox OTP on 2026-09-08, not left as dead settings. A
    # setting nothing reads is one an operator will eventually tune expecting
    # an effect; SES identity verification is the ownership check now.

    def sender_blocked_domains(self) -> frozenset[str]:
        """The blocklist, parsed once per call: lowercased, trimmed, non-empty."""
        return frozenset(
            part.strip().lower()
            for part in self.sender_domain_blocklist.split(",")
            if part.strip()
        )

    # ── Private file storage (S3) ───────────────────────────────────────────
    #
    # Durable values stored in the database are s3:// object references; a raw
    # bucket URL is never returned to a browser. Access always passes through an
    # authenticated, tenant-scoped, capability-checked endpoint rather than a
    # presigned link, because a presigned URL is a bearer token that leaves no
    # audit trail once it has been copied out of a page.
    #
    # No access key or secret lives here. In a deployed environment boto3
    # resolves the ECS task role, which is scoped to exactly this bucket by the
    # per-service IAM policy in `infra/modules/secrets`. A long-lived key in an
    # env var is precisely what that scoping exists to avoid.
    s3_bucket: str = ""
    # ap-south-2 (Hyderabad). The deployment region is a locked decision;
    # every bucket, function, cluster and secret this product addresses
    # lives there, so a wrong default here is a set of NoSuchBucket and
    # ResourceNotFound errors that read like missing resources.
    aws_region: str = "ap-south-2"
    #: Localstack / MinIO only. None in every real environment, where boto3
    #: resolves the real regional endpoint.
    s3_endpoint_url: str = ""
    resume_signed_url_ttl_seconds: int = 300

    # ── Proctoring (proctoring-spec-doc.md) ─────────────────────────────────
    #
    # EVERY THRESHOLD THE PROCTORING SYSTEM USES IS HERE, AND NOWHERE ELSE.
    # The specification's "defaults" are starting configuration, not
    # constants, so each one is a setting an operator can move without a code
    # change. `services/proctoring/config.py` reads them into one frozen
    # object and serves the browser-side subset to the client at session
    # start, which is what keeps the client and the server working from the
    # same numbers. No module in the proctoring pipeline may carry a literal.
    #
    # Proctoring is MANDATORY (principle P4). There is no enable flag: a
    # candidate who declines the consent screen does not take the assessment.
    # The one feature flag below governs the AI-text detector only, because
    # that signal is documented as unreliable and ships disabled.
    proctoring_max_warnings: int = 3
    # Object detection (section 3.1, 4.2).
    proctoring_object_confidence_threshold: float = 0.65
    proctoring_object_consecutive_frames: int = 3
    proctoring_object_cooldown_seconds: int = 30
    proctoring_second_person_cooldown_seconds: int = 30
    # Face identity (section 3.3, 4.1).
    proctoring_face_distance_threshold: float = 0.6
    proctoring_identity_check_interval_seconds: int = 30
    proctoring_identity_consecutive_mismatches: int = 2
    # Camera obstruction versus face absence (section 4.1, 4.2, 4.6).
    proctoring_obstruction_seconds: int = 60
    #: Per-frame greyscale standard deviation below which a frame with no face
    #: is an obstruction (covered lens, tape, closed shutter) rather than an
    #: absence. Computed in the browser from the frame it then discards.
    proctoring_obstruction_variance_threshold: float = 12.0
    proctoring_face_absent_moderate_seconds: int = 20
    proctoring_face_absent_moderate_cooldown_seconds: int = 60
    proctoring_face_absent_extended_seconds: int = 90
    # Browser lockdown and focus (section 4.2).
    proctoring_focus_loss_ignore_under_seconds: float = 2.0
    proctoring_display_check_interval_seconds: int = 60
    # Audio (section 3.4, 4.2).
    proctoring_audio_chunk_seconds: int = 15
    proctoring_audio_max_chunk_bytes: int = 2 * 1024 * 1024
    proctoring_second_voice_consecutive_chunks: int = 2
    #: The analysis service (speaker diarization, AI-text detection). Empty
    #: means audio analysis is UNAVAILABLE, which the report states plainly;
    #: it is never silently treated as "no second voice".
    proctoring_analysis_service_url: str = ""
    proctoring_analysis_timeout_seconds: float = 20.0
    # Anti-tamper (section 9).
    proctoring_heartbeat_interval_seconds: int = 10
    proctoring_heartbeat_gap_seconds: int = 30
    proctoring_integrity_failure_termination_seconds: int = 60
    proctoring_camera_recovery_seconds: int = 60
    # In-browser inference performance (section 3.6).
    proctoring_sampling_fps_normal: int = 2
    proctoring_sampling_fps_confirming: int = 6
    proctoring_confirming_window_seconds: int = 5
    proctoring_sampling_fps_degraded: int = 1
    proctoring_low_light_luminance_threshold: float = 40.0
    proctoring_low_light_cooldown_seconds: int = 300
    # Behavioural capture (section 4.5). Thresholds compare the candidate
    # against THEIR OWN baseline from their first answers, never a population.
    proctoring_baseline_answers: int = 2
    proctoring_fast_entry_multiplier: float = 3.5
    proctoring_fast_entry_sustained_seconds: int = 10
    proctoring_uniform_span_chars: int = 200
    proctoring_uniform_max_corrections: int = 5
    proctoring_uniform_max_pause_seconds: float = 1.0
    proctoring_low_ratio_min_length: int = 150
    proctoring_low_ratio_threshold: float = 0.85
    proctoring_pause_gap_seconds: float = 2.0
    proctoring_burst_window_seconds: int = 5
    proctoring_mouse_sample_hz: int = 10
    proctoring_max_keystroke_samples: int = 20_000
    proctoring_event_batch_max: int = 200
    # AI-generated-text detection (section 3.5). INFORMATIONAL ONLY and
    # disabled by default: the detectors are unreliable against current
    # models, and the signal never contributes to a warning, a termination, a
    # score or a ranking whatever this flag says.
    proctoring_ai_text_detection_enabled: bool = False
    proctoring_ai_text_threshold: float = 0.9
    proctoring_ai_text_min_chars: int = 200
    #: Event retention (section 5). ZERO means "the platform's existing
    #: candidate-data policy", which is deletion by cascade when the candidate
    #: or application is deleted; the platform has no time-based purge and
    #: this setting does not invent one. A positive value enables the hourly
    #: purge of events older than that many days. Owner decision.
    proctoring_event_retention_days: int = 0

    # ── Assessment question formats (assessment-spec-doc.md) ────────────────
    #
    # Composition is enforced in code, not suggested in a prompt: evidence
    # questions must be the majority of the assessment's time and weight, the
    # supporting formats the minority, and the whole thing must fit the
    # role's duration. These are the bounds. `services/assessment_formats/
    # config.py` reads them into one object; nothing else carries a literal.
    #: Evidence-based questions' minimum share of total weight AND of total
    #: time allocation. Above one half by definition of "majority", with a
    #: margin so a rounding effect cannot tip a valid assessment over.
    assessment_evidence_min_share: float = 0.55
    #: The supporting formats' (MCQ, fill-blank, coding) maximum share of the
    #: QUESTION COUNT, by seniority. Senior roles skew further toward
    #: evidence and away from recall-style questions.
    assessment_supporting_max_share: float = 0.25
    assessment_supporting_max_share_senior: float = 0.15
    #: The assessment's total suggested duration per grade, in minutes. The
    #: sum of every question's time allocation must fit inside it.
    assessment_duration_minutes_non_managerial: int = 100
    assessment_duration_minutes_managerial: int = 85
    assessment_duration_minutes_leadership: int = 70
    assessment_duration_minutes_cxo: int = 50
    #: Suggested time per question, by format, in seconds.
    assessment_time_evidence_seconds: int = 240
    assessment_time_short_answer_seconds: int = 180
    assessment_time_mcq_single_seconds: int = 60
    assessment_time_mcq_multi_seconds: int = 90
    assessment_time_fill_blank_seconds: int = 60
    assessment_time_coding_seconds: int = 600
    #: INTERNAL weight per format, within a matrix item. What makes evidence
    #: dominance structural rather than stated.
    assessment_weight_evidence: float = 1.0
    assessment_weight_short_answer: float = 1.0
    assessment_weight_mcq_single: float = 0.4
    assessment_weight_mcq_multi: float = 0.5
    assessment_weight_fill_blank: float = 0.4
    assessment_weight_coding: float = 0.8
    #: How many times the composer may regenerate a mix that fails validation
    #: before it falls back to an all-evidence allocation for the supporting
    #: slots, which is always valid.
    assessment_composition_attempts: int = 3
    #: The fewest words an AI evaluation's reasoning may carry. A bare verdict
    #: with a sentence attached is not a reasoning a recruiter can act on.
    assessment_evaluation_min_reasoning_words: int = 40
    #: The shortest quotable resume item an evidence question may anchor to.
    #: Below this an "anchor" is a single word, which anchors nothing.
    assessment_anchor_min_chars: int = 12
    #: The fewest words a distractor's misconception rationale may carry
    #: before the option counts as a real misconception rather than filler.
    assessment_misconception_min_words: int = 4

    # ── Dual-mode assessment: consent + video interview (2026-09-05 spec) ───
    #
    # CONSENT IS A HARD PREREQUISITE FOR BOTH MODES (spec section 3). The
    # wording is CONFIGURABLE, never hardcoded in a handler (spec 3.2: "the
    # exact legal wording should be configurable"), and the versions below are
    # stamped onto every consent row so a dispute is settled by which wording
    # was accepted. The defaults are complete and honest: they state
    # collection, storage, processing, speech-to-text, AI analysis and the
    # PRISM Report destination in plain language, with no em dash.
    assessment_consent_version: str = "2026-09-05"
    assessment_privacy_policy_version: str = "2026-09-05"
    assessment_terms_version: str = "2026-09-05"
    assessment_consent_text_video: str = (
        "Before you begin the video interview, please understand and agree to "
        "the following. Your interview will be recorded: both your video and "
        "your audio are captured for the full session. The recording is "
        "stored securely and is processed for assessment purposes. Your "
        "speech is converted into text, and the resulting information is "
        "analyzed by AI as part of your evaluation. Information derived from "
        "this assessment may be included in the report the hiring team "
        "receives about your candidacy. If you do not agree, you will not be "
        "able to take the video interview; you may choose the conversational "
        "assessment instead, which has its own consent terms."
    )
    assessment_consent_text_conversational: str = (
        "Before you begin the assessment, please understand and agree to the "
        "following. ReadyPick collects and processes what you submit during "
        "the assessment: your written answers, your questions, your responses "
        "to multiple-choice and coding questions, and session data such as "
        "timings and interaction records. This information is stored, is "
        "analyzed by AI as part of your evaluation, and may be included in "
        "the report the hiring team receives about your candidacy. If you do "
        "not agree, you will not be able to take the assessment."
    )
    # ── Video interview ceilings and processing knobs ───────────────────────
    # Every ceiling is a setting, never a literal in the pipeline (same rule
    # as proctoring and projects). Sized for an hour-long interview recorded
    # by MediaRecorder at browser defaults.
    video_max_upload_bytes: int = 1024 * 1024 * 1024
    video_max_duration_seconds: int = 2 * 3600
    #: Amazon Transcribe. DISABLED by default because it needs an AWS account
    #: with the service enabled in the deployment region; when disabled a
    #: recording lands in `transcription_failed` with a message saying speech
    #: to text is not configured. HONEST AND RETRYABLE, never a fake
    #: transcript (no silent fallback).
    transcribe_enabled: bool = False
    transcribe_language_code: str = "en-IN"
    #: THE TRANSCRIBE REGION IS NOT NECESSARILY THE DEPLOYMENT REGION, and that
    #: is a fact about AWS rather than a preference: ap-south-2 (Hyderabad) has
    #: no Transcribe endpoint at all, so a pilot deployed there must call the
    #: service in ap-south-1 (Mumbai). Empty means `aws_region`, which is
    #: correct wherever Transcribe exists in the deployment region.
    transcribe_region: str = ""
    #: A Transcribe job reads its media from, and writes its output to, a
    #: bucket in ITS OWN region: a job running in ap-south-1 cannot read
    #: s3://bucket when that bucket lives in ap-south-2. This names the working
    #: bucket in `transcribe_region`. The pipeline copies the extracted audio
    #: in, runs the job, copies the transcript back to `s3_bucket` and deletes
    #: both working objects, so nothing accumulates here. Empty means
    #: `s3_bucket`, which is correct only when the two regions agree.
    transcribe_bucket: str = ""
    video_transcribe_timeout_seconds: int = 1800
    video_transcribe_poll_seconds: int = 15
    #: ffmpeg transcode settings for the long-term compressed mp4 (video spec
    #: section 8: codec and quality are configurable, storage optimization,
    #: not destructive compression). H.264 + AAC for browser playability.
    video_compression_crf: int = 28
    video_compression_preset: str = "medium"
    video_compression_audio_bitrate_kbps: int = 96
    #: The compressed object's duration must be within this many seconds of
    #: the raw recording's before the raw is deleted (video spec section 10:
    #: verify before delete).
    video_duration_tolerance_seconds: float = 3.0
    video_ffmpeg_timeout_seconds: int = 1800
    #: Presigned delivery-URL lifetimes for the client portal (video spec
    #: sections 16 and 18). A presigned URL is a bearer token once it leaves
    #: the page, so both are short by default. Preview must outlive a full
    #: watch-through of the longest permitted interview because the player's
    #: seek issues range requests against the same URL; download only needs to
    #: cover the click and a slow connection's head start.
    video_preview_url_ttl_seconds: int = 3 * 3600
    video_download_url_ttl_seconds: int = 900

    # ── Project Evidence Intelligence limits ────────────────────────────────
    #
    # Candidate project submissions are UNTRUSTED input processed into derived
    # evidence; the original artifact is temporary by product decision (the
    # 2026-09-01 Project Evidence master brief) and is deleted once evidence is
    # persisted. These are the safe processing ceilings, DATA here rather than
    # literals in the pipeline so an operator can widen them without a deploy
    # of new code paths. Sized against the resume path's own ceilings (10 MB a
    # file) and the worker's 600 s soft time limit: the whole pipeline for one
    # project must finish comfortably inside one task slot.
    project_max_projects_per_candidate: int = 10
    project_max_files: int = 20
    project_max_file_bytes: int = 25 * 1024 * 1024
    project_max_total_bytes: int = 100 * 1024 * 1024
    #: Archive extraction guards (zip bombs, nesting, entry floods).
    project_max_archive_depth: int = 2
    project_max_archive_entries: int = 2000
    project_max_extracted_bytes: int = 200 * 1024 * 1024
    #: A compressed entry claiming to inflate past this ratio is refused as a
    #: decompression bomb before a single byte is extracted.
    project_max_compression_ratio: int = 120
    #: Per-file ceiling on text promoted into deterministic parsing.
    project_max_text_chars_per_file: int = 60_000
    #: Evidence reduction: the most units one project may persist, and the most
    #: characters of evidence-pack context one AI reasoning call may receive.
    project_max_evidence_units: int = 120
    project_max_ai_context_chars: int = 24_000
    #: Public-repository ingestion caps: how many meaningful files are fetched
    #: after tree classification, and the largest single file fetched.
    project_repo_max_files: int = 40
    project_repo_max_file_bytes: int = 512_000
    #: OPTIONAL. Raises the public GitHub API rate limit; grants no private
    #: access and is never required. Public repositories only, by product
    #: decision: no private-repository OAuth or token intake exists.
    github_api_token: str = ""

    # ── Retrieval, RPN-AI-UP-001 W2 ─────────────────────────────────────────
    #
    # FILTERED ANN RECALL IS THE DANGEROUS ONE, AND IT FAILS SILENTLY.
    #
    # An HNSW scan returns its top K by vector distance and the tenant
    # predicate filters AFTERWARDS. In a multi-tenant table the scan can
    # traverse mostly other tenants' vectors and return almost nothing for the
    # calling tenant. It does not error. It returns a short list that reads as
    # a legitimately sparse result, and recall degrades as tenant count grows
    # -- worst for the smallest tenants, which are the newest customers.
    #
    # `hnsw.iterative_scan` makes the index keep pulling candidates until
    # enough rows pass the predicate. `strict_order` additionally guarantees
    # exact distance ordering, which matters here because RRF fusion reads
    # ORDER and nothing else: a relaxed order would corrupt the one signal
    # fusion consumes.
    #
    # Requires pgvector 0.8.0 or later. Measured 0.8.1 on the pilot cluster and
    # 0.8.5 on the test image (2026-09-09), so it is available on both. It is a
    # SETTING rather than a literal so an environment on an older pgvector can
    # turn it off explicitly, and `off` is then a recorded deployment decision
    # rather than a silent fallback.
    #: `strict_order` | `relaxed_order` | `off`.
    retrieval_hnsw_iterative_scan: str = "strict_order"
    #: Bounds a runaway iterative scan. Without a ceiling, a query for a tenant
    #: with no matching rows scans the whole index before returning empty.
    retrieval_hnsw_max_scan_tuples: int = 20_000
    #: Candidates the HNSW layer considers per query. pgvector's default is 40,
    #: which is below the depth the fusion stage asks for.
    retrieval_hnsw_ef_search: int = 100
    #: How many documents one `reconcile_context_index` pass repairs. The sweep
    #: dispatches one indexing task per document, so this bounds the fan-out of
    #: a single hourly run rather than the work itself.
    retrieval_index_sweep_batch: int = 200

    # ── Retrieval intelligence (RPN-AI-UP-001 W6) ───────────────────────────
    #
    # Deployment data, one value per deployment, never a fallback chain: the
    # same shape as TASK_DISPATCH_BACKEND and email_transport. Read through a
    # validator that RAISES on an unrecognised value, because the failure mode
    # of a wrong one is SILENT -- retrieval keeps working and simply gets
    # worse, which is indistinguishable from a tenant with thin evidence.
    #: `voyage` (the cross-encoder) or `lexical` (the deterministic pass).
    #: Defaults to `lexical` deliberately: an environment that has not made the
    #: decision runs the behaviour it runs today, and turning the cross-encoder
    #: on is an explicit act.
    retrieval_reranker: str = "lexical"
    #: Whether a situating prefix is generated at index time.
    retrieval_contextual_prefix: bool = True
    #: The reranker's credential, named after the model it unlocks (rerank-2.5),
    #: the same convention as VOYAGE_CONTEXT_4 and OPENAI_GPT_TERRA. A separate
    #: variable from the embedding key even where the Voyage account issues one
    #: string: it makes "the reranker is not configured" distinguishable from
    #: "embedding is not configured", so an unset value here is a RECORDED
    #: degradation rather than an embedding outage wearing a reranker's name.
    voyage_rerank_2_5: str = ""

    # Payments  -  Razorpay Subscriptions. The Key ID is public (Checkout needs it
    # in the browser and reads it from GET /billing/config); the Key Secret and
    # the webhook secret are server-side only and never reach a response body,
    # a log line, or the frontend bundle.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    # ReadyPick's own GST registration number, printed on every credit-pack
    # invoice (Master Directive Part 5 §5.2). Configuration, not code: it is a
    # legal identifier that changes with registration, never with a release.
    readypick_gstin: str = ""

    # App
    environment: str = "development"

    @model_validator(mode="after")
    def _refuse_an_unconfigured_jwt_secret(self) -> "Settings":
        """In production, refuse to boot without a real signing key.

        ONE SECRET KEYS EVERYTHING, which is what makes this worth a boot
        refusal rather than a warning. `jwt_secret` signs the portal session
        cookies, the OTP hashes, the outreach links, the assessment invite
        tokens and the signed resume URLs. A known value lets anyone mint
        `{"aud": "pickready:owner", "role": "super_admin"}` and reach
        `get_superadmin_db`, which is the RLS bypass scope. That is total
        platform compromise from a default string.

        AND IT COULD ARRIVE EMPTY. `_drop_placeholder_secrets` below rewrites
        `PLACEHOLDER_NOT_CONFIGURED` to "", and PyJWT signs HS256 with an empty
        key without complaint -- so an unprovisioned secret does not fail, it
        silently signs. That is the exact shape of the 2026-09-06 Firebase
        incident, where a secret CONTAINER was mistaken for a configured
        secret, on the one credential whose blast radius is everything.

        Ordered BEFORE the placeholder rewrite so the refusal can name which of
        the two states it found. Development and test are unaffected: the
        default is what lets a fresh clone run.
        """
        if (self.environment or "").strip().lower() != "production":
            return self
        value = (self.jwt_secret or "").strip()
        if not value or value == PLACEHOLDER_SECRET or value == DEV_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET is not configured in production. It signs the "
                "session cookies, the OTP hashes and every signed link, so a "
                "default or empty value is a full platform compromise. Set it "
                "in Secrets Manager and redeploy."
            )
        return self

    @model_validator(mode="after")
    def _drop_placeholder_secrets(self) -> "Settings":
        """Turn every placeholder back into "not configured".

        Applied to EVERY string field rather than a named list, because the
        list would be the thing that drifts: a secret added to
        `infra/modules/secrets` without a matching entry here would arrive as a
        non-empty sentinel and read as configured.
        """
        for name, field in type(self).model_fields.items():
            if getattr(self, name, None) == PLACEHOLDER_SECRET:
                object.__setattr__(self, name, "")
        return self
    frontend_url: str = "http://localhost:3000"

    # Platform Owner  -  the ONLY identity permitted to hold the owner
    # (super_admin) role. Enforced in the API layer, not just seed/UI.
    owner_email: str = "manjuchro@gmail.com"

    # Gmail's authenticated mailbox is always the From address.

    # Outbound-delivery retry cap. Email retries transient failures after a
    # fixed 60-second delay; SMS retains exponential backoff. Permanent
    # failures never retry.
    delivery_max_retries: int = 3  # initial attempt + up to 3 retries

    @model_validator(mode="after")
    def validate_gmail_smtp(self) -> "Settings":
        configured = bool(self.smtp_host or self.smtp_user or self.smtp_password)
        if not configured:
            return self
        if self.smtp_host != "smtp.gmail.com":
            raise ValueError("SMTP_HOST must be smtp.gmail.com")
        if self.smtp_port != 587 or not self.smtp_starttls or self.smtp_ssl:
            raise ValueError(
                "Gmail SMTP requires port 587 with STARTTLS enabled and SSL disabled"
            )
        if self.smtp_user and not self.smtp_user.lower().endswith("@gmail.com"):
            raise ValueError("SMTP_USER must be a Gmail address")
        if self.smtp_user and self.smtp_from_email.lower() != self.smtp_user.lower():
            raise ValueError("SMTP_FROM_EMAIL must match SMTP_USER")
        return self

    @property
    def effective_transcribe_region(self) -> str:
        """The region the Transcribe client is built in."""
        return (self.transcribe_region or self.aws_region or "").strip()

    @property
    def effective_transcribe_bucket(self) -> str:
        """The bucket a Transcribe job reads and writes, in that region."""
        return (self.transcribe_bucket or self.s3_bucket or "").strip()

    @model_validator(mode="after")
    def validate_transcribe_colocation(self) -> "Settings":
        """Refuse the cross-region misconfiguration rather than fail per job.

        Transcribe is region-local over S3, so pointing the client at another
        region while leaving the bucket behind produces a BadRequestException
        on EVERY recording, one at a time, hours after the deploy. Naming it
        here makes it a boot failure a deploy can see.
        """
        if not self.transcribe_enabled:
            return self
        deployment = (self.aws_region or "").strip()
        region = (self.transcribe_region or "").strip()
        if region and region != deployment and not (self.transcribe_bucket or "").strip():
            raise ValueError(
                "TRANSCRIBE_REGION differs from AWS_REGION, so TRANSCRIBE_BUCKET "
                "must name a bucket in TRANSCRIBE_REGION: an Amazon Transcribe "
                "job cannot read or write a bucket in another region."
            )
        return self

    @model_validator(mode="after")
    def validate_email_transport(self) -> "Settings":
        """Exactly one of the two real transports; a typo must not silently
        select anything (Corporate Email System spec section 6)."""
        value = (self.email_transport or "").strip().lower()
        if value not in {"smtp", "ses"}:
            raise ValueError("EMAIL_TRANSPORT must be smtp or ses")
        object.__setattr__(self, "email_transport", value)
        return self

    @model_validator(mode="after")
    def validate_cookie_policy(self) -> "Settings":
        value = (self.cookie_samesite or "").strip().lower()
        if value not in {"strict", "lax", "none"}:
            raise ValueError("COOKIE_SAMESITE must be strict, lax or none")
        object.__setattr__(self, "cookie_samesite", value)
        if value == "none" and self.environment != "production":
            # SameSite=None is only honoured on a Secure (https) cookie, and
            # `secure` is tied to the production flag. Refuse the combination
            # rather than shipping a cookie every browser silently drops.
            raise ValueError(
                "COOKIE_SAMESITE=none requires ENVIRONMENT=production so the "
                "cookie is marked Secure"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def serves_over_https(self) -> bool:
        """Whether the browser reaches this deployment over TLS.

        THE AUTH COOKIE'S `Secure` FLAG READS THIS, not `is_production`, and the
        difference is a real one: `Secure` is a property of the ORIGIN, not a
        production feature. Tying it to `is_production` meant a cookie issued by
        the pilot or by staging went out without it over a genuine HTTPS origin.

        Derived from `frontend_url` because that is the same string the load
        balancer actually serves and `jobs.public_job_url` already builds
        candidate links from, so this cannot drift from reality. It is https in
        every deployed environment, http on a laptop, and right in an
        environment nobody has thought about yet.
        """
        return self.frontend_url.lower().startswith("https://")

    def missing_delivery_keys(self) -> list[str]:
        """Names of unset outbound-delivery credentials (for startup preflight).

        Gmail SMTP (host/user/password) is the email credential set; MSG91
        remains the SMS credential set.
        """
        checks = {
            "SMTP_HOST": self.smtp_host,
            "SMTP_USER": self.smtp_user,
            "SMTP_PASSWORD": self.smtp_password,
            "MSG91_API_KEY": self.msg91_api_key,
            "MSG91_SENDER_ID": self.msg91_sender_id,
        }
        return [name for name, value in checks.items() if not value]


def preflight_delivery_config() -> list[str]:
    """Log a loud WARNING for any missing email/SMS credential at startup.

    ASSUMPTION: a missing key must NOT hard-crash the container in development
     -  local dev without SMTP/MSG91 keys has to remain possible (the sprint
    brief only requires that a missing key not fail *silently*). In production
    the same warning is emitted; enforcement/alerting on it is an ops concern,
    not a process-exit here. Returns the list of missing key names so callers
    (or tests) can assert on it.
    """
    import logging

    settings = get_settings()
    missing = settings.missing_delivery_keys()
    if missing:
        logging.getLogger(__name__).warning(
            "delivery.preflight MISSING outbound credentials: %s  -  emails/SMS "
            "using these will fail. Set them in the environment. env=%s",
            ", ".join(missing),
            settings.environment,
        )
    else:
        logging.getLogger(__name__).info(
            "delivery.preflight ok  -  SMTP + MSG91 credentials present"
        )
    return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
