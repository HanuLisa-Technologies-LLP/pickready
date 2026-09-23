"use client";

/**
 * Publishing a job (Vivekium release, Phase 1).
 *
 * THE ONE PLACE A JOB GOES LIVE
 * -----------------------------
 * Create Job used to publish inside the create call under `create_job` alone,
 * so a job went live with no SWOT, no skills, a DRAFT lifecycle state and no
 * JD index. Publishing is now `POST /jobs/{id}/publish`, which requires the
 * `publish_job` capability and a saved JD, a saved SWOT and saved skills, sets
 * the lifecycle state and dispatches the indexing after the commit.
 *
 * THE CHECKLIST IS THE SERVER'S
 * -----------------------------
 * Every tick below comes from `GET /setup`, which reads the tables, and the
 * reason publishing is blocked is the server's own sentence, rendered verbatim.
 * The screen never decides for itself that a job is ready: if it did, it could
 * offer a Publish button the server then refuses, which is the specific way a
 * gate becomes infuriating.
 *
 * The public application link, and its copy control, moved here from the old
 * Create Job popup, because this is now where the link comes into existence.
 */

import * as React from "react";
import {
  Check,
  CircleDashed,
  Copy,
  ExternalLink,
  Loader2,
  Send,
} from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { Job } from "@/lib/types";
import { CAP } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";
import { cn } from "@/lib/utils";
import { ReadOnlyNotice } from "@/components/permission-notice";
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** The assessments router is mounted at /api/v2 ONLY. */
const BASE = "/api/v2/assessments/jobs";

/** `GET /api/v2/assessments/jobs/{id}/setup`. Every field is derived from a
 *  table on the server; none of it is a stamp the client can trust less. */
export interface JobSetupStatus {
  job_id: string;
  jd_ready: boolean;
  swot_status: string;
  swot_saved: boolean;
  skills_draft_status: string;
  skills_saved: boolean;
  skills_locked: boolean;
  /** The grade locks with the skills: it decides every candidate's question
   *  budget, so it cannot move once somebody has started. */
  grade_locked: boolean;
  published: boolean;
  ready_for_candidates: boolean;
  /** Every missing step, in order, in the server's words. Null when none. */
  publish_blocked_reason: string | null;
}

const CHECKLIST: { key: "jd_ready" | "swot_saved" | "skills_saved"; label: string }[] = [
  { key: "jd_ready", label: "Job description" },
  { key: "swot_saved", label: "SWOT saved" },
  { key: "skills_saved", label: "Skills saved" },
];

function publicLink(job: Job | null | undefined): string | null {
  if (!job) return null;
  if (job.public_application_url) return job.public_application_url;
  if (typeof window === "undefined") return null;
  return `${window.location.origin}/apply/${job.id}`;
}

export function JobPublishCard({
  jobId,
  job,
  reloadKey = 0,
  onPublished,
  onSetup,
  className,
}: {
  jobId: string;
  /** The job as the page last read it, for the public link and closure. */
  job?: Job | null;
  /** Bump to re-read the checklist after a JD, SWOT or skills change. */
  reloadKey?: number;
  /** The publish response, so the page shows the job's new state at once. */
  onPublished?: (job: Job) => void;
  /** Every fresh read, so the page can lock the grade field from the same
   *  answer this card shows. */
  onSetup?: (setup: JobSetupStatus) => void;
  className?: string;
}) {
  const { toast } = useToast();
  const { can } = usePermissions();
  const canPublish = can(CAP.publishJob);

  const [setup, setSetup] = React.useState<JobSetupStatus | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [publishing, setPublishing] = React.useState(false);
  const [refusal, setRefusal] = React.useState<string | null>(null);
  const [copied, setCopied] = React.useState(false);

  const onSetupRef = React.useRef(onSetup);
  onSetupRef.current = onSetup;

  const load = React.useCallback(async () => {
    setLoadError(null);
    try {
      const res = await apiGet<JobSetupStatus>(`${BASE}/${jobId}/setup`);
      setSetup(res);
      onSetupRef.current?.(res);
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "The publishing checklist could not be loaded."
      );
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  React.useEffect(() => {
    void load();
  }, [load, reloadKey]);

  const publish = async () => {
    setPublishing(true);
    setRefusal(null);
    try {
      const published = await apiPost<Job>(`/jobs/${jobId}/publish`);
      onPublished?.(published);
      toast({
        title: "Job published",
        description: "Share the application link wherever you post the role.",
      });
      await load();
    } catch (error) {
      // The server names what is still missing, or why this person may not
      // publish. Shown as written.
      setRefusal(error instanceof Error ? error.message : String(error));
      void load();
    } finally {
      setPublishing(false);
    }
  };

  const link = setup?.published && !job?.closed_at ? publicLink(job) : null;

  const copyLink = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast({
        title: "Copy failed",
        description: "Select the link and copy it manually.",
        variant: "destructive",
      });
    }
  };

  return (
    <Card className={cn("mb-6", className)}>
      <CardHeader>
        <CardTitle>Publish</CardTitle>
        <CardDescription>
          A job goes live once its description, SWOT and skills are saved.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {loading ? (
          <LoadingRows rows={3} label="Loading the publishing checklist" />
        ) : loadError || !setup ? (
          <ErrorState
            title="The publishing checklist could not be loaded"
            description={loadError ?? undefined}
            action={
              <Button variant="outline" onClick={() => void load()}>
                Try again
              </Button>
            }
          />
        ) : (
          <>
            <ul aria-label="Publishing checklist" className="space-y-2">
              {CHECKLIST.map((item) => {
                const done = setup[item.key];
                return (
                  <li key={item.key} className="flex items-center gap-2">
                    {done ? (
                      <Check className="h-4 w-4 shrink-0 text-teal-700" aria-hidden="true" />
                    ) : (
                      <CircleDashed className="h-4 w-4 shrink-0" aria-hidden="true" />
                    )}
                    <span className="font-medium">{item.label}</span>
                    <span className="text-xs">{done ? "Done" : "Not yet"}</span>
                  </li>
                );
              })}
            </ul>

            {setup.published ? (
              <div className="space-y-3">
                <p role="status" className="font-medium">
                  {job?.closed_at
                    ? "This job has been closed. New applications have stopped."
                    : "This job is published."}
                </p>
                {!job?.closed_at && !setup.ready_for_candidates ? (
                  <p>
                    It is taking applications, but candidates cannot be invited
                    to the assessment until the skills are saved again.
                  </p>
                ) : null}
                {link ? (
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <Input
                      aria-label="Application link"
                      readOnly
                      value={link}
                      className="font-mono text-sm"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void copyLink()}
                      className="gap-2"
                    >
                      {copied ? (
                        <Check className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <Copy className="h-4 w-4" aria-hidden="true" />
                      )}
                      {copied ? "Copied" : "Copy link"}
                      {/* The label swap is the confirmation for anyone watching
                          it; a screen reader is told through the live region. */}
                      <span role="status" className="sr-only">
                        {copied ? "Link copied to clipboard" : ""}
                      </span>
                    </Button>
                    <Button asChild variant="outline" className="gap-1.5">
                      <a href={link} target="_blank" rel="noopener noreferrer">
                        <ExternalLink className="h-4 w-4" aria-hidden="true" />
                        Preview
                      </a>
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : (
              <>
                {setup.publish_blocked_reason ? (
                  <p role="status">{setup.publish_blocked_reason}</p>
                ) : null}
                {refusal ? (
                  <p role="alert" className="border border-destructive/40 p-3">
                    {refusal}
                  </p>
                ) : null}
                {canPublish ? (
                  <Button
                    className="gap-1.5"
                    disabled={publishing || Boolean(setup.publish_blocked_reason)}
                    onClick={() => void publish()}
                  >
                    {publishing ? (
                      <Loader2
                        className="h-4 w-4 motion-safe:animate-spin"
                        aria-hidden="true"
                      />
                    ) : (
                      <Send className="h-4 w-4" aria-hidden="true" />
                    )}
                    {publishing ? "Publishing" : "Publish job"}
                  </Button>
                ) : (
                  <ReadOnlyNotice
                    canEdit={false}
                    resource="whether this job is published"
                  />
                )}
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
