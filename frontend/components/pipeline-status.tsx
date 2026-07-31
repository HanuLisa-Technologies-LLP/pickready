"use client";

// Pipeline status display and actions (spec §3.3, §4.2, §8.2).
//
// The action menu is driven ENTIRELY by `allowed_transitions` from the API. The
// frontend does not know the transition rules and deliberately does not try to
//, rendering a button the backend would refuse is how a recruiter learns the
// rules by hitting 409s.

import * as React from "react";
import { ChevronDown, Loader2 } from "lucide-react";

import { apiPost } from "@/lib/api";
import {
  PIPELINE_LABELS,
  PIPELINE_SHORT_LABELS,
  type ApplicationStatusResponse,
  type CandidatePipeline,
  type PipelineStage,
  type TransitionOption,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/** Monochrome except the two decided outcomes, which earn a colour. */
const STAGE_STYLES: Partial<Record<PipelineStage, string>> = {
  applied: "border-border bg-muted text-foreground",
  assessment_invited: "border-border bg-muted text-foreground",
  assessment_in_progress: "border-border bg-muted text-foreground",
  assessment_completed: "border-foreground bg-background text-foreground",
  shortlisted: "border-emerald-700 bg-emerald-700 text-white",
  interview_scheduled: "border-foreground bg-foreground text-background",
  interview_completed: "border-foreground bg-foreground text-background",
  offer_extended: "border-emerald-800 bg-emerald-800 text-white",
  joined: "border-emerald-800 bg-emerald-800 text-white",
  hold: "border-amber-600 bg-amber-100 text-amber-950",
  rejected: "border-red-700 bg-red-100 text-red-950",
};

export function StageBadge({
  status,
  short = false,
  className,
}: {
  status: PipelineStage | string;
  short?: boolean;
  className?: string;
}) {
  const stage = status as PipelineStage;
  const label = short
    ? PIPELINE_SHORT_LABELS[stage] ?? status
    : PIPELINE_LABELS[stage] ?? status;
  return (
    <span
      className={cn(
        "inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-semibold",
        STAGE_STYLES[stage] ?? "border-border bg-muted text-foreground",
        className
      )}
    >
      {label}
    </span>
  );
}

/**
 * Per-row status actions. `hold` and `rejected` collect a remark first, a
 * paused or closed application with no reason is not useful to whoever picks
 * it up next.
 */
export function StatusActions({
  linkId,
  status,
  allowed,
  options,
  candidateName,
  onChanged,
}: {
  linkId: string;
  status: PipelineStage;
  /** Legacy shape, kept so existing callers keep working. */
  allowed: PipelineStage[];
  /**
   * The server's list of legal moves WITH their labels. Prefer this: the
   * backend decides which stages a human may pick, so removing one (the client
   * asked for Shortlisted to go, 2026-07-28) is a server change and this menu
   * follows without an edit here.
   */
  options?: TransitionOption[];
  candidateName: string;
  onChanged?: () => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);
  const [pending, setPending] = React.useState<PipelineStage | null>(null);
  const [remarks, setRemarks] = React.useState("");

  const NEEDS_REMARK: PipelineStage[] = ["hold", "rejected"];

  // One menu source of truth. When the server sends labelled options we use
  // them verbatim; otherwise we fall back to the bare stage list.
  const menu: TransitionOption[] =
    options && options.length > 0
      ? options
      : allowed.map((s) => ({ status: s, label: PIPELINE_LABELS[s] ?? s }));

  const commit = async (target: PipelineStage, note?: string) => {
    setBusy(true);
    try {
      const res = await apiPost<ApplicationStatusResponse>(
        `/pipeline/applications/${linkId}/change-status`,
        { status: target, remarks: note || null },
      );
      toast({
        title: `${candidateName}, ${res.stage_label}`,
        description: res.email_queued
          ? "The candidate has been emailed."
          : "No email was sent for this change.",
      });
      setPending(null);
      setRemarks("");
      onChanged?.();
    } catch (e) {
      toast({
        title: "Couldn't update the status",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  if (menu.length === 0) {
    return (
      <span className="text-xs">
        {status === "joined" ? "Hired" : "Closed"}
      </span>
    );
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="gap-1" disabled={busy}>
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
            Move to
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {menu.map((option) => (
            <DropdownMenuItem
              key={option.status}
              onSelect={() => {
                if (NEEDS_REMARK.includes(option.status)) setPending(option.status);
                else void commit(option.status);
              }}
            >
              {option.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pending ? PIPELINE_LABELS[pending] : ""}, {candidateName}
            </DialogTitle>
            <DialogDescription>
              A short internal note. It is recorded against the application and
              is never shown to the candidate.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="status-remarks">Reason</Label>
            <Textarea
              id="status-remarks"
              rows={3}
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Headcount not confirmed until next quarter…"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button
              disabled={busy || !remarks.trim()}
              onClick={() => pending && void commit(pending, remarks.trim())}
            >
              {busy ? "Saving" : "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/**
 * The funnel (spec §8.2). Every stage is rendered even at zero, so the shape
 * does not shift as candidates move, an empty column is information.
 */
export function PipelineFunnel({
  jobId,
  reloadKey = 0,
}: {
  jobId: string;
  reloadKey?: number;
}) {
  const [data, setData] = React.useState<CandidatePipeline | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    import("@/lib/api").then(({ apiGet }) =>
      apiGet<CandidatePipeline>(`/pipeline/jobs/${jobId}/candidate-pipeline`)
        .then((res) => !cancelled && setData(res))
        .catch(() => !cancelled && setData(null))
    );
    return () => {
      cancelled = true;
    };
  }, [jobId, reloadKey]);

  if (!data || data.total === 0) return null;
  const peak = Math.max(...data.stages.map((s) => s.count), 1);

  return (
    <section aria-label="Candidate pipeline" className="mb-6">
      <h3 className="mb-2 text-sm font-semibold">
        Pipeline, {data.total} candidate{data.total === 1 ? "" : "s"}
      </h3>
      <ol className="flex flex-wrap gap-2">
        {data.stages.map((stage) => (
          <li
            key={stage.status}
            className={cn(
              "min-w-[104px] flex-1 rounded-md border p-2",
              stage.count === 0 && "opacity-55"
            )}
          >
            <p className="text-lg font-semibold tabular-nums">{stage.count}</p>
            <p className="text-[11px] leading-tight">
              {PIPELINE_SHORT_LABELS[stage.status] ?? stage.label}
            </p>
            <div
              aria-hidden
              className="mt-1.5 h-1 rounded bg-foreground/80"
              style={{ width: `${Math.round((stage.count / peak) * 100)}%` }}
            />
          </li>
        ))}
      </ol>
    </section>
  );
}
