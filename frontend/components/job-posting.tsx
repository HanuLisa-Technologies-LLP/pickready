"use client";

/**
 * The job posting as a candidate reads it (CONTRACT v10, owner ruling
 * 2026-09-28; shapes in `docs/release/2026-09-vivekium/s4-api-shapes.md`).
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
 * `FinalJobPostingPreview` is the recruiter's last look before Publish. It
 * reads `GET .../posting-preview`, so every word it shows (the grade label,
 * the experience band, the skill names and their order, the narrative) is the
 * server's, and it renders the JD with the same `JdDocument` the public apply
 * page uses for the same markdown.
 *
 * NAMES ONLY
 * ----------
 * A skill reaches a candidate as its name under its bucket's label. The
 * hidden evidence line, the per-bucket priority and the role summary never
 * ride `skill_buckets`, and `postingSkillsFrom` keeps a label and names and
 * drops anything else, so a payload that carried more still renders less.
 */

import * as React from "react";

import { apiGet } from "@/lib/api";
import type { PostingPreview, PostingSkillBucket } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { JdBlock } from "@/components/job-description";
import { JdDocument } from "@/components/jd-document";
import { ErrorState, LoadingRows } from "@/components/page-primitives";

type BucketKey = PostingSkillBucket["bucket"];

/** Posting order, and the heading used only if the server sent no label. */
const BUCKET_ORDER: { key: BucketKey; label: string }[] = [
  { key: "must_have", label: "Must-have skills" },
  { key: "nice_to_have", label: "Nice-to-have skills" },
  { key: "behavioural", label: "Behavioural competencies" },
];

function namesFrom(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const names = value
    .map((item) => (typeof item === "string" ? item.trim() : ""))
    .filter(Boolean);
  return Array.from(new Set(names));
}

/**
 * A payload's `skill_buckets` as the buckets worth rendering, or null.
 *
 * Null is the honest reading of a legacy job with no saved skills (the server
 * sends `[]`, or an older server sends nothing): the surface then renders no
 * skills section at all. A bucket with no names is skipped. Only the label
 * and the names are kept, in posting order.
 */
export function postingSkillsFrom(value: unknown): PostingSkillBucket[] | null {
  if (!Array.isArray(value)) return null;
  const buckets: PostingSkillBucket[] = [];
  for (const { key, label } of BUCKET_ORDER) {
    const entry = value.find(
      (item) =>
        item && typeof item === "object" && (item as { bucket?: unknown }).bucket === key
    ) as { label?: unknown; names?: unknown } | undefined;
    if (!entry) continue;
    const names = namesFrom(entry.names);
    if (names.length === 0) continue;
    buckets.push({
      bucket: key,
      label: typeof entry.label === "string" && entry.label.trim() ? entry.label : label,
      names,
    });
  }
  return buckets.length > 0 ? buckets : null;
}

/**
 * The job's skills under their bucket labels, names only, in the order the
 * server sent them. Renders nothing for a job with no skills.
 */
export function PostingSkillsList({
  buckets,
  headingLevel = 3,
  headingClassName,
  className,
}: {
  buckets: PostingSkillBucket[] | null | undefined;
  /** The heading level that fits the surrounding document outline. */
  headingLevel?: 3 | 4;
  headingClassName?: string;
  className?: string;
}) {
  const id = React.useId();
  if (!buckets || buckets.length === 0) return null;
  const Heading = headingLevel === 4 ? "h4" : "h3";
  return (
    <div className={cn("space-y-4", className)}>
      {buckets.map((bucket) => {
        const headingId = `${id}-${bucket.bucket}`;
        return (
          <section
            key={bucket.bucket}
            aria-labelledby={headingId}
            className="space-y-2"
          >
            <Heading
              id={headingId}
              className={
                headingClassName ?? "type-eyebrow"
              }
            >
              {bucket.label}
            </Heading>
            <ul className="flex flex-wrap gap-1.5">
              {bucket.names.map((name) => (
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
export const POSTING_NARRATIVE: {
  key: "about_company" | "work_life" | "benefits";
  title: string;
}[] = [
  { key: "about_company", title: "About the company" },
  { key: "work_life", title: "Work life" },
  { key: "benefits", title: "Benefits" },
];

const PREVIEW_BASE = "/api/v2/assessments/jobs";

/**
 * The recruiter's Final Job Posting: the posting exactly as candidates will
 * read it, placed between the Skills step and Publish.
 *
 * Everything inside the bordered sheet is what a candidate sees. The grade is
 * shown OUTSIDE it, labelled as such: it sizes the assessment and no
 * candidate surface prints it.
 */
export function FinalJobPostingPreview({
  jobId,
  reloadKey = "",
  className,
}: {
  jobId: string;
  /** Changes whenever the JD, the job's details or the skills may have. */
  reloadKey?: string | number;
  className?: string;
}) {
  const [preview, setPreview] = React.useState<PostingPreview | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoadError(null);
    try {
      setPreview(
        await apiGet<PostingPreview>(`${PREVIEW_BASE}/${jobId}/posting-preview`)
      );
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "The posting preview could not be loaded."
      );
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  React.useEffect(() => {
    void load();
  }, [load, reloadKey]);

  const buckets = postingSkillsFrom(preview?.skill_buckets);
  const meta = [preview?.department, preview?.experience_band]
    .filter(Boolean)
    .join(" · ");
  const narrative = POSTING_NARRATIVE.filter((section) =>
    (preview?.[section.key] ?? "").trim()
  );

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
        {loading ? (
          <LoadingRows rows={4} label="Loading the posting preview" />
        ) : loadError || !preview ? (
          <ErrorState
            title="The posting preview could not be loaded"
            description={loadError ?? undefined}
            action={
              <Button variant="outline" onClick={() => void load()}>
                Try again
              </Button>
            }
          />
        ) : (
          <>
            <p>
              <span className="font-semibold">Grade:</span> {preview.grade_label}.
              It sets the assessment and is not shown on the posting.
            </p>

            <article
              aria-label="Posting preview"
              className="space-y-6 rounded-xl border border-border p-5 sm:p-6"
            >
              <header className="space-y-1.5">
                {preview.company_name ? (
                  <p className="type-eyebrow">
                    {preview.company_name}
                  </p>
                ) : null}
                <p className="text-balance text-lg font-semibold leading-snug">
                  {preview.title}
                </p>
                {meta ? <p>{meta}</p> : null}
              </header>

              {(preview.jd_markdown ?? "").trim() ? (
                <JdDocument markdown={preview.jd_markdown ?? ""} />
              ) : (
                <p>No job description has been written for this job yet.</p>
              )}

              {buckets ? (
                <div className="space-y-2">
                  <PostingSkillsList buckets={buckets} />
                  {!preview.skills_saved ? (
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
                      value={preview[section.key]}
                    />
                  ))}
                </div>
              ) : null}
            </article>
          </>
        )}
      </CardContent>
    </Card>
  );
}
