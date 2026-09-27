"use client";

/**
 * The job posting as a candidate reads it (CONTRACT v10, owner ruling
 * 2026-09-28).
 *
 *     JD -> Skills -> Final Job Posting -> Publish
 *
 * TWO THINGS LIVE HERE, AND THEY SHARE ONE RENDERER ON PURPOSE
 * ------------------------------------------------------------
 * `PostingSkillsList` is how every candidate-facing surface shows a job's
 * skills: the public apply page, the portal's apply dialog, the employer
 * page's open roles, and the recruiter's Final Job Posting preview. One
 * renderer, so the preview cannot promise a posting the candidate never sees.
 *
 * `FinalJobPostingPreview` is the recruiter's last look before Publish. It is
 * built from the SAME candidate-facing pieces (`JobDescriptionSummary`, the
 * narrative blocks, `PostingSkillsList`), not from the recruiter's editors.
 *
 * NAMES ONLY
 * ----------
 * A skill reaches a candidate as its name under its bucket's heading. The
 * hidden evidence line, the per-bucket priority and the role summary are what
 * the assessment knows about each skill, and they never ride this shape:
 * `postingSkillsFrom` keeps a name and drops everything else, so a payload
 * that carried more still renders less.
 */

import * as React from "react";

import type { Job, PostingSkills } from "@/lib/types";
import { jobGradeLabel, jobJd } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { JdBlock, JobDescriptionSummary } from "@/components/job-description";

export type PostingBucket = keyof PostingSkills;

/** The three buckets in posting order, with the heading a candidate reads. */
export const POSTING_BUCKETS: { key: PostingBucket; heading: string }[] = [
  { key: "must_have", heading: "Must-have skills" },
  { key: "nice_to_have", heading: "Nice-to-have skills" },
  { key: "behavioural", heading: "Behavioural competencies" },
];

function namesFrom(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const names = value
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (item && typeof item === "object" && "name" in item) {
        const name = (item as { name: unknown }).name;
        return typeof name === "string" ? name.trim() : "";
      }
      return "";
    })
    .filter(Boolean);
  return Array.from(new Set(names));
}

/**
 * A payload's skills as names by bucket, or null when there are none.
 *
 * Null is the honest reading of a legacy job whose skills were never saved:
 * the surface then renders no skills section at all rather than three empty
 * headings. Accepts a bucket list of names or of objects carrying a `name`,
 * and keeps the name ONLY.
 */
export function postingSkillsFrom(value: unknown): PostingSkills | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const skills: PostingSkills = {
    must_have: namesFrom(record.must_have),
    nice_to_have: namesFrom(record.nice_to_have),
    behavioural: namesFrom(record.behavioural),
  };
  const any = POSTING_BUCKETS.some((bucket) => skills[bucket.key].length > 0);
  return any ? skills : null;
}

/**
 * The job's skills under their bucket headings, names only. Renders nothing
 * for a job with no skills, and omits an empty bucket.
 */
export function PostingSkillsList({
  skills,
  headingLevel = 3,
  headingClassName,
  className,
}: {
  skills: PostingSkills | null | undefined;
  /** The heading level that fits the surrounding document outline. */
  headingLevel?: 3 | 4;
  headingClassName?: string;
  className?: string;
}) {
  const id = React.useId();
  if (!skills) return null;
  const buckets = POSTING_BUCKETS.filter(
    (bucket) => (skills[bucket.key] ?? []).length > 0
  );
  if (buckets.length === 0) return null;
  const Heading = headingLevel === 4 ? "h4" : "h3";
  return (
    <div className={cn("space-y-4", className)}>
      {buckets.map((bucket) => {
        const headingId = `${id}-${bucket.key}`;
        return (
          <section
            key={bucket.key}
            aria-labelledby={headingId}
            className="space-y-2"
          >
            <Heading
              id={headingId}
              className={
                headingClassName ?? "text-xs font-semibold uppercase tracking-wide"
              }
            >
              {bucket.heading}
            </Heading>
            <ul className="flex flex-wrap gap-1.5">
              {skills[bucket.key].map((name) => (
                <li key={name}>
                  <Badge variant="secondary">{name}</Badge>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

/** The experience band in the employer page's words ("3 to 5 years
 *  experience"). A job fact the recruiter typed, not an assessment signal. */
export function experienceBandText(
  min: number | null | undefined,
  max: number | null | undefined
): string | null {
  if (min == null && max == null) return null;
  if (min != null && max != null) return `${min} to ${max} years experience`;
  if (min != null) return `${min}+ years experience`;
  return `Up to ${max} years experience`;
}

/** The company narrative in posting order, shared by the preview and the
 *  public apply page so the two cannot disagree. */
export const POSTING_NARRATIVE: { key: "about_company" | "work_life" | "benefits"; title: string }[] = [
  { key: "about_company", title: "About the company" },
  { key: "work_life", title: "Work life" },
  { key: "benefits", title: "Benefits" },
];

/**
 * The recruiter's Final Job Posting: the posting exactly as candidates will
 * read it, placed between the Skills step and Publish.
 *
 * Everything inside the bordered sheet is what a candidate sees. The grade is
 * shown OUTSIDE it, labelled as such: it sizes the assessment and no candidate
 * surface prints it.
 */
export function FinalJobPostingPreview({
  job,
  skills,
  skillsSaved,
  companyName,
  className,
}: {
  job: Job;
  /** The job's current skills, names by bucket; null when there are none. */
  skills: PostingSkills | null;
  /** Whether the skills shown are the saved set. */
  skillsSaved: boolean;
  companyName?: string | null;
  className?: string;
}) {
  const band = experienceBandText(job.experience_min_years, job.experience_max_years);
  const meta = [job.department, band].filter(Boolean).join(" Ã‚Â· ");
  // The job-side JD object, read the way every other job screen reads it.
  const jd = { ...jobJd(job) } as Record<string, unknown>;
  const narrative = POSTING_NARRATIVE.filter((section) => (job[section.key] ?? "").trim());

  return (
    <Card id="final-job-posting" className={cn("mb-6 scroll-mt-6", className)}>
      <CardHeader>
        <CardTitle>Final job posting</CardTitle>
        <CardDescription>
          What candidates will read once the job is published. What the
          assessment knows about each skill stays hidden.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p>
          <span className="font-semibold">Grade:</span> {jobGradeLabel(job.grade)}.
          It sets the assessment and is not shown on the posting.
        </p>

        <article
          aria-label="Posting preview"
          className="space-y-6 rounded-xl border border-border p-5 sm:p-6"
        >
          <header className="space-y-1.5">
            {companyName ? (
              <p className="text-xs font-semibold uppercase tracking-wide">
                {companyName}
              </p>
            ) : null}
            <p className="text-balance text-lg font-semibold leading-snug">
              {job.title}
            </p>
            {meta ? <p>{meta}</p> : null}
          </header>

          <JobDescriptionSummary jd={jd} />

          {skills ? (
            <div className="space-y-2">
              <PostingSkillsList skills={skills} />
              {!skillsSaved ? (
                <p className="text-xs">
                  These skills are not saved yet. Save them above before
                  publishing.
                </p>
              ) : null}
            </div>
          ) : (
            <p>
              No skills yet. The skills you add above appear here by name,
              under Must-have skills, Nice-to-have skills and Behavioural
              competencies.
            </p>
          )}

          {narrative.length > 0 ? (
            <div className="space-y-4">
              {narrative.map((section) => (
                <JdBlock
                  key={section.key}
                  title={section.title}
                  value={job[section.key]}
                />
              ))}
            </div>
          ) : null}
        </article>
      </CardContent>
    </Card>
  );
}
