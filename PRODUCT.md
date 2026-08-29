# ReadyPick — PRODUCT.md

Product context for design and review tooling. This is the "what and for whom"
that `DESIGN.md` is the "how it looks" of.

**Surface type: PRODUCT.** App UI, dashboards and tools — not a marketing site.
Impeccable's `init` asks this question and the answer changes what its detectors
expect: a marketing site is allowed a hero and a mood, while a product surface
is judged on whether somebody can do their job in it.

---

## What ReadyPick is

An AI-native, multi-tenant hiring-intelligence platform. A client posts a role;
six named agents build a fixed evaluation framework for it, run one continuous
conversation with each candidate, score against that framework, and produce a
**PRISM Report** — *Predictive Role Intelligence & Suitability Mapping*.

The claim it is sold on is not "we screen faster". It is **"we can tell you
why"**: every grade traces to evidence, and the report says what the evidence
was. That claim is the reason for most of the design constraints — a surface
that obscures the evidence undercuts the only thing that differentiates the
product.

## Who uses it

| Portal | Who | What they are doing |
|---|---|---|
| **Customer** (`/org`) | CHRO, Recruitment Manager, Recruiter, Hiring Manager | Posting roles, reviewing candidates, reading reports, making a decision about a person |
| **Candidate** (`/portal`) | Applicants | Applying, and answering one long conversational assessment — **usually on a phone** |
| **Provider** (`/admin`) | ReadyPick's own owner | Customers, compliance, billing. Read-only over customer data by design |
| **Business Development** (`/bd`) | ReadyPick's sales team | Leads, AI Reach, converting a signed agreement into a tenant |

The two that matter most for design are **Customer** and **Candidate**, and they
pull in opposite directions:

- A **recruiter** works at a desk, scans a dense table, and wants information
  density. Padding is not kindness to them.
- A **candidate** answers on a phone, is nervous, is being assessed, and cannot
  see how many questions remain (deliberately). Their surface is calm, one
  question at a time, and mobile-first.

## What is at stake on each screen

This is the sentence that should govern every design decision: **a person's
career is on the other side of it.**

Practically:

- A grade shown wrongly is a hiring decision made wrongly.
- A report that reads as more certain than it is causes a rejection that should
  have been a conversation.
- A flag rendered as an accusation, rather than as "held for review", is the
  platform making a claim about a candidate that it is in no position to make.
- A number shown to a client is a false precision they will quote back.

## Hard product constraints a reviewer should know

These are not style preferences and none of them is negotiable:

1. **No number ever reaches a client.** Not a score, percentage, rank, band
   index, confidence or weight — in the UI, in an API response, or in an email.
   Grades are four words: Highly Matching, Matching, Moderately Matching, Not
   Matching. The one documented exception is the radar chart's radius, which is
   a rendering coordinate and is never displayed as a number.
2. **Any Must-have graded Not Matching caps Overall at Moderately Matching**,
   with no override, and the report says it was capped.
3. **Reports are immutable.** No edit or delete affordance; a retake produces a
   new report beside the old one.
4. **Text is never grey.** Enforced at the CSS token, not per component.
5. **No em dashes in any string**, including seeded and generated content.
6. **No flag ever auto-rejects.** Every flag routes to a human with the evidence
   attached, and a person's decision is recorded.
7. **The Validation section is the candidate's own words, exactly as
   submitted.** Never re-worded, never summarised, never scored.
8. **Never name a storage vendor in user-facing copy.** Candidates are told the
   file limits, not where the bytes land.

## Tone

Plain, specific, and never chirpy. This product tells people they did not match
a role. Copy that is upbeat about that is copy that reads as unkind.

- Say what happened and what to do: "Held for review — two sources disagree
  about the dates" beats "Something needs attention!"
- No exclamation marks in an assessment or a report.
- Never congratulate a candidate on a grade, and never commiserate. Both imply
  the platform has an opinion about them beyond what it measured.
- The candidate never learns how many questions remain. Copy must not leak it.

## Where this file is used

`/impeccable critique`, `/impeccable audit` and `/impeccable polish` read this
alongside `DESIGN.md`. It is deliberately about CONSTRAINTS AND STAKES rather
than features: a reviewer who knows a career is on the other side of the screen
catches things a feature list would not surface.
