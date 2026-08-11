# ADR 0001: The assessment invitation is a signed token resolved by a public page

Date: 2026-08-11
Status: Accepted

## Context

The assessment invitation email carried `{frontend}/portal/assessments/{link_id}`
-- a raw application id inside a route the candidate portal shell guards. Three
things followed from that shape, and all three were visible to candidates:

1. **Nothing bound the link to the person it was sent to.** The id was the whole
   of the authorization story, and it never expired.
2. **An unauthenticated click bounced to `/login` with no destination.** The
   portal shell redirected without `next`, so signing in landed the candidate on
   the jobs board. They had been sent to the right page and the app discarded
   the destination on the way out.
3. **Every refusal looked the same.** Expired, already submitted, wrong account
   and "the recruiter has not invited you" all arrived as either a redirect or a
   generic error.

## Decision

The email carries a **signed JWT** (`services/assessment_invite`) bound to both
the application link and the invited email, under its own audience
(`pickready:assessment-invite`) and purpose claim. It resolves through a
**public landing page** at `/assessments/invite/{token}`, which asks
`GET /api/v2/assessments/invitations/{token}` and does nothing but route.

The endpoint answers **200 for every outcome**, including refusals, and puts the
outcome in a `state` field.

The order of the checks is fixed and tested:

    token validity -> application exists -> anyone signed in -> the RIGHT
    person -> posting window -> already submitted -> actually invited

## Consequences and the alternatives that were rejected

**Why not a backend 302?** The interesting states are not redirects. "You are
signed in as someone else" and "you already submitted this" have to be READ, and
a 302 has nowhere to put an explanation. A page can render the explanation; a
redirect can only choose a destination.

**Why not 401/404/410 per state?** Because that is how five different situations
become "something went wrong". This codebase has repeatedly had to undo
generic errors, and the engineering constraint is explicit: every error surfaces
its real cause. A status code cannot distinguish "your link expired" from "this
job closed" from "you are not invited", and the page has to.

**Why identity before state?** Checking "already submitted" before "wrong
account" would tell anybody holding the link whether that particular candidate
had finished their assessment. The ordering is a disclosure boundary, not a
style preference, so it is pinned by a test rather than left to the next
refactor.

**Why must the landing page be public?** Gating it would bounce the visitor to a
login before the token had been read at all, which is precisely the behaviour
being replaced. It renders no candidate data of its own: only a routing
decision and, on the wrong-account screen, a MASKED form of the invited address.

**Why a JWT rather than a bare HMAC?** The product already signs stateless links
this way (`deps.make_outreach_token`), and `exp` is then enforced by the library
rather than by hand. The audience is disjoint from all three portal audiences,
and both directions are asserted: a session token cannot open an invitation, and
an invitation cannot authenticate a session.

**Why a 36-day TTL?** The posting window (30) plus its grace tail (5) plus a
day. The signature must never be the first thing to expire: if a link is dead,
the reason should be a product rule the candidate can be told about, not a
cryptographic lifetime nobody can explain.

**What this does NOT change.** Report reuse stays retired. Under PPI both the
framework and the technical questions come from each job's own JD, so nothing is
portable between jobs and the six-month rule remains explanatory only: the
candidate is told why they are answering questions again, and then they answer
them.

## Compliance

- `backend/app/services/assessment_invite.py` -- minting, verification, masking,
  and the single URL builder both email paths call.
- `backend/app/api/assessments.py::resolve_invitation` -- the resolver and the
  check order.
- `backend/tests/test_assessment_invitation_link.py` -- 35 assertions.
- `frontend/app/assessments/invite/[token]/page.tsx` -- the landing page.
- `frontend/lib/next-destination.ts` -- the one same-origin `next` guard, shared
  by the login flow, the register flow and the portal shell.
