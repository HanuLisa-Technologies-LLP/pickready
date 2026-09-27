# S4 flow: the API shapes the frontend builds against (CONTRACT v10)

Owner ruling v10 (2026-09-28): JD, then Skills, then the Final Job Posting,
then Publish. The SWOT is separate internal hiring intelligence. The FIRST
GENUINE APPLICATION freezes the JD and the Skills.

This page is the contract between the backend package (`wip/s4-flow-backend`)
and the frontend package. Every field below is served by the backend; the
frontend decides nothing it describes. Sentences marked "verbatim" are the
server's own words and are rendered exactly as they arrive.

## 1. The order, as the server enforces it

```
POST /api/v1/jobs                    create a DRAFT with a JD
    -> Sutra's skills draft is dispatched after the commit (JD only; the
       SWOT is optional context when one is saved)
PATCH /api/v1/jobs/{id}/jd           edit the JD; when the job has NO skill
                                     row of any kind, the draft is dispatched
POST  .../skills/draft               the explicit "Draft skills" action
Skills step (add, paste, rename, move, remove)  unchanged
POST  .../skills/save                Save Skills, unchanged
GET   .../posting-preview            the Final Job Posting preview (NEW)
POST /api/v1/jobs/{id}/publish       needs the JD and saved skills ONLY
candidate applies (portal or /apply) FREEZES the JD and the skills
SWOT                                 editable at any time, never a gate,
                                     never drafts skills
```

## 2. `GET /api/v2/assessments/jobs/{id}/setup` (`JobSetupOut`)

Additive. Every existing field keeps its name and meaning, except that
`publish_blocked_reason` no longer names the SWOT.

```jsonc
{
  "job_id": "uuid",
  "jd_ready": true,
  "swot_status": "not_generated | generating | generated | edited | failed",
  "swot_saved": false,                 // informational ONLY; never blocks publish
  "skills_draft_status": "not_started | drafting | drafted | failed",
  "skills_saved": true,
  "skills_locked": true,               // == frozen (kept for existing readers)
  "grade_locked": true,                // == frozen (kept for existing readers)
  "published": false,
  "ready_for_candidates": true,
  "publish_blocked_reason": null,      // verbatim, or null
  // NEW (v10)
  "frozen": true,                      // a snapshot row exists for the job
  "frozen_at": "2026-09-28T10:15:00Z", // when the FIRST snapshot was taken, or null
  "frozen_reason": "The job description and skills are frozen because a candidate has applied. Frozen since 28 Sep 2026."
                                       // verbatim banner text, null when not frozen
}
```

`frozen` is true from the first genuine application (or, for a legacy job
whose first application predated saved skills, from the first assessment
start). Frozen means: the JD document, the title, the experience band, the
grade and the skills are read-only. The company narrative sections (About,
Work Life, Benefits), the proctoring warning policy, department and
requirement period, the compensation and the SWOT stay editable.

## 3. `GET /api/v2/assessments/jobs/{id}/posting-preview` (NEW, `PostingPreviewOut`)

Capability: `view_company_jobs` (the same as every job read). Reads only.
What the candidate-facing posting will say, for the recruiter, before and
after publishing. Skill NAMES only: never an evidence line, a priority or the
role summary. Names inside a bucket are in alphabetical order (the internal
priority order is not revealed).

```jsonc
{
  "job_id": "uuid",
  "title": "Senior Data Engineer",
  "department": "Data",                       // or null
  "grade": "managerial",                      // code
  "grade_label": "Managerial",                // word to render
  "experience_band": "3 to 5 years",          // words, or null when no band
  "jd_markdown": "## About the role ...",
  "company_name": "Acme",
  "about_company": "...",                     // resolved: job override, else Company Profile
  "work_life": "...",
  "benefits": "...",
  "skill_buckets": [                          // ALWAYS three, in this order
    {"bucket": "must_have",    "label": "Must-have skills",        "names": ["Kafka stream processing", "SQL query optimisation"]},
    {"bucket": "nice_to_have", "label": "Nice-to-have skills",     "names": []},
    {"bucket": "behavioural",  "label": "Behavioural competencies","names": ["Production incident ownership"]}
  ],
  "skills_saved": true,                       // false: the names are the team's unsaved working set
  "published": false,
  "public_application_url": null,             // the absolute link once published
  "publish_blocked_reason": null,             // verbatim, same sentence as /setup
  "frozen": false,
  "frozen_at": null,
  "frozen_reason": null                       // verbatim
}
```

Preview skills: once frozen, the snapshot's names; otherwise the job's
current active skills (saved or not; `skills_saved` says which).

## 4. Public posting payloads: `skill_buckets`

Added to:

* `GET /api/v1/jobs/public/{id}` (`PublicJobOut`, the `/apply/{job}` page),
* `GET /api/v1/portal/jobs/{id}` and every job on `GET /api/v1/portal/jobs`
  (`PortalJobOut`),
* every `open_roles[]` entry of `GET /api/v1/employers/{slug}`
  (`EmployerOpenRoleOut`).

```jsonc
"skill_buckets": [
  {"bucket": "must_have",    "label": "Must-have skills",         "names": ["..."]},
  {"bucket": "nice_to_have", "label": "Nice-to-have skills",      "names": []},
  {"bucket": "behavioural",  "label": "Behavioural competencies", "names": ["..."]}
]
```

* `[]` (empty list) when the job has no SAVED skills (a legacy published job,
  or skills edited after publishing and not yet saved again). Render no skills
  section at all.
* Otherwise exactly three entries in that order; a bucket may have an empty
  `names` list, which the page skips.
* Once frozen, the names come from the frozen snapshot.
* Alphabetical within a bucket. Nothing else about a skill crosses.

## 5. Refusals, each rendered verbatim (`detail`)

| When | Status | `detail` |
|---|---|---|
| Any edit of the JD (`PATCH /jobs/{id}/jd`) once frozen | 409 | `The job description and skills are frozen because a candidate has applied. Frozen since 28 Sep 2026.` |
| `PATCH /jobs/{id}` changing `title`, `experience_min_years`, `experience_max_years` or `grade` once frozen (a value equal to the current one is not a change) | 409 | the same sentence |
| Every skills write (add, paste, rename, move, remove), Save Skills and Draft skills once frozen | 409 | the same sentence |
| `POST /jobs/{id}/publish` missing steps | 409 | `Before this job can be published, write and save the job description and save the skills.` (only the missing steps are named: `write and save the job description`, `save the skills`) |
| `POST .../skills/draft` when the JD is too thin to draft from | 409 | `Write the job description first. The skills are drafted from it, so it needs a title and a few paragraphs describing the role.` |

The date in the frozen sentence is the date of the first snapshot, `%d %b
%Y` (for example `28 Sep 2026`). It is a date, not an assessment number.

`GRADE_LOCKED_DETAIL` ("The grade is locked because a candidate has started
the assessment ...") and `SKILLS_LOCKED_DETAIL` ("The skills are locked because
a candidate has started ...") are RETIRED: the frozen sentence replaces both.
The frontend must not hard-code either; render `frozen_reason` from `/setup`
for the banner and the 409 `detail` for a refused write.

## 6. `GET /api/v2/assessments/jobs/{id}/skills` (`SkillsOut`)

Additive:

```jsonc
{
  "...": "every existing field unchanged",
  "locked": true,                  // == frozen
  "frozen_reason": "The job description and skills are frozen ...",  // verbatim, null when not frozen
  "draft_blocked_reason": null     // verbatim: why a draft cannot be asked for now
                                   // (the JD is too thin), null when it can
}
```

A failed draft that never started because the JD was too thin reads
`draft_status: "failed"` with `draft_error` carrying the thin-JD sentence
above.

## 7. The SWOT (`/swot-analysis`)

Unchanged shapes. A SWOT save or restore NEVER drafts skills now; while the
job is not frozen it can only make `skills_redraft_available` true (an offer:
the team presses Draft skills and confirms). After the freeze the SWOT stays
editable and `skills_redraft_available` is false.

## 8. The apply response

`POST /api/v1/portal/jobs/{id}/apply` (`ApplyOut`) is unchanged. The freeze
happens inside the same transaction; nothing new is returned to the candidate.
