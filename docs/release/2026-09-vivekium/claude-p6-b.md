# CLAUDE.md section draft: Phase 6 WP6-B (candidate portal backend)

Draft for the orchestrator to fold into the release's single top section.
Written in the file's own voice; nothing here edits `claude.md` directly.
No migration of its own. The `external_link` value needs the CHECK swap in
the phase 6 migration (WP6-C owns the file; step 5 of PLAN-p6 3.13).

## THERE IS ONE WAY TO APPLY, AND APPLYING IS NOT BEING INVITED

`POST /portal/jobs/{id}/apply` is used by the portal's New Jobs board AND the
public `/apply/{job}` page. It takes a resume (a fresh upload or
`reuse_previous`), the six validation fields and `application_source`, keyword
only after `job_id`, and nothing else. `tests/test_aspect_form_removed.py`
pins the exact parameter set, because a bound form field is a field somebody
eventually writes.

- **It creates NO `assessment_conversations` row and dispatches NO question
  generation.** That row IS the invitation; inviting is the recruiter's act
  (`select-candidates`). Creating it at apply handed every applicant a
  half-made invitation the assessment page refused (no `invitation_sent_at`)
  and counted as an issued contract before anybody was invited. Proven on a
  second connection by `test_portal.py` and `test_public_apply_unified.py`.
  The `retake.decide` call went with it, and `ApplyOut` is now
  `link_id, job_id, profile_id, resume_reused` only: an application answer that
  talked about "the assessment" pointed at a page that would refuse them.
- **It writes no age, gender, name or city, and no consent.** Age and gender
  were written and read by nothing. The databank consent was re-derived from
  whatever the apply form carried on EVERY application, which could silently
  REVOKE a consent given on My Profile. **The databank consent is written by
  `PUT /portal/me/profile-form` and nowhere else**, from the declaration whose
  wording ("I consent to my profile being shared with prospective employers")
  is on that form. `aspects_json` still snapshots `profile_form_json`.
- **`parse_resume` and the confirmation email are `dispatch_after_commit`**, so
  a refused or rolled-back application sends nothing. Every other bare
  dispatch in `api/portal.py` moved too (edit, main resume, projects, Delete
  My Profile) except `dispatch_bgv_inquiry`, whose refusal path depends on the
  enqueue raising in the request.
- **A sourced link is converted, never duplicated, and never claimed by an
  unverified address.** The applicant's own sourced link moves
  `sourced -> applied` through the FSM (unchanged). When the sourced link sits
  on an OLDER unlinked record with the applicant's address,
  `candidate_identity.rehome_sourced_link` re-points it first, and only when
  `users.email_verified_at` is set. `tests/test_sourced_conversion.py`.

## `application_source` IS WHERE THEY CLICKED, NEVER WHAT THEY ARE

`direct` (the portal board) or `external_link` (the public page); anything
else reads as `direct`. `_derive_source_type` no longer reads
`application_source` at all: it mapped `application_source = 'sourced'` onto
`source_type = sourced`, so a person who read the job and APPLIED through a
shared link was labelled as a recruiter's upload who never had, the exact
confusion Gate 5 exists to prevent. `sourced` stays in the CHECK only for rows
migration 0018 backfilled and is never written. A public-link applicant is
`source_type = applied`.

## A SOURCED LINK IS NOT AN APPLICATION ON ANY CANDIDATE SCREEN

`GET /portal/applications` lists applications only; `PortalJobOut` carries
`already_applied` and `application_id` computed over NON-sourced links, the
same rule `apply_context` already followed. A recruiter's databank entry shown
as "your application" was both false and a dead end. The free-text `level` is
gone from `PortalJobOut`; the grade is the one answer (the column stays, S4).

## DELETE MY PROFILE DELETES THE SIGN-IN, INLINE, BEFORE THE COMMIT

`cascade_erasure` deleted the `users` row and left the Firebase account alive,
so the next sign-in silently created a fresh, empty profile for somebody just
told they were erased. `delete_my_profile` now calls
`firebase_auth.delete_identity` in a threadpool AFTER the rows are erased and
BEFORE the transaction commits.

- **A failure is 503 "We could not remove your sign-in. Nothing was deleted,
  please try again." and the WHOLE transaction rolls back**: rows, deletion
  request and audit row, and the after-commit dispatches are discarded with
  it. A live sign-in over erased rows is the state being closed; "try again"
  must be true advice. The window left is a commit failing after Firebase
  answered, which leaves rows with no sign-in: the safe direction.
- **A shared address keeps the identity.** When another `users` row still
  signs in through the same uid or address (a recruiter who once applied),
  the identity is that person's staff door and is kept;
  `DeleteMeOut.sign_in_identity_deleted = False` with the server's
  `sign_in_identity_note`.
- **The worker erasures keep the identity, deliberately.** The consent and
  dormancy sweeps call the same `cascade_erasure` and never touch Firebase:
  workers hold no Firebase key by design, and a person who never asked to
  leave keeps the door back. Said beside the users delete in
  `services/erasure`.
- `tests/test_account_deletion_identity.py`, all three cases, second
  connection.

## "KEEP MY PROFILE" HAS A STATUS ROUTE

`GET /portal/me/consent/renewal` answers `consented_at`, `renewal_due_at`,
`stage`, `renewal_needed` and a server `message`, derived by
`consent_lifecycle` over the columns and settings the sweep reads, so the card
cannot disagree with the sweep. Reading renews nothing; the existing POST is
the act. The copy carries no number (dates travel as fields, rendered by the
client).

## A FEED LINK IS CHECKED AGAINST THE ROUTE TREE

`ASSESSMENT_INVITED` and `ASSESSMENT_STARTED` linked to `/portal/assessments`,
which is no page (only `/portal/assessments/[link_id]` exists). They link to
the application's own assessment page now, and
`tests/test_candidate_update_links.py` resolves EVERY catalogue `link_path`
against `frontend/app` the way the App Router does (route groups flattened,
`[param]` matches a segment). Mutation-checked with the old path. Stored rows
carrying the old path are served by WP6-E's redirect page.

## THE 40-ASPECT QUESTIONNAIRE IS GONE

`ASPECT_DEFINITIONS` (thirty-five placeholder prompts), `_PERSONAL_ASPECTS`,
`MAX_EMPLOYER_EMAILS`, `GET/POST /portal/outreach/{token}`, `AspectOut`,
`OutreachInfoOut`, `OutreachSubmitOut` and the never-sent `outreach` email
default are deleted. `tests/test_aspect_form_removed.py` sweeps `backend/app`,
`backend/scripts`, `frontend/app|components|lib` and `proxy.ts` with
whitespace normalised, and carries a `PENDING` ratchet of files other packages
still clean (verification `send_outreach`, the deps token helpers, the
`candidate_outreach` default, D's `/status` refusal, E's pages, and a handful
of comments). A cleaned file left in `PENDING` fails as stale; the list must
be empty at the end of the release.

## SUPERSESSIONS TO MARK IN PLACE

- 2026-07-27 "Unified candidate profile": the apply route snapshotting the
  profile form stands; its old consent re-derivation is gone.
- 2026-09-04 "Gate 5": "the apply path finds the sourced link rather than
  refusing it" gains the rehome of a sourced link on an older unlinked record.
- The Procurement-type comment block (2026-07-28 BD Portal section): `sourced`
  no longer means "arrived through a third-party link"; it means a recruiter
  put the resume on the job. Where an applicant clicked is
  `application_source`.
- 2026-09-06 / feature 7 ("the erasure deletes the users row, so the next
  sign-in starts from scratch"): the candidate's own deletion now also deletes
  the Firebase identity; the worker erasures still do not.
- General rule 7's "40-aspect flow" wording: the flow no longer exists.
