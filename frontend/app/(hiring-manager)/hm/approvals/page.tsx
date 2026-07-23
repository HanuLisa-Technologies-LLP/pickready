"use client";

// My Approvals queue: jobs awaiting this approver's decision at the current
// level. Approve / Reject with optional remarks (FR-3.2).

import * as React from "react";
import { Check, X } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { ApprovalTransition, Job } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { StatusBadge } from "@/components/status-badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const PENDING_STATUSES = ["requested", "recommended", "approved"];

export default function ApprovalsPage() {
  const { toast } = useToast();
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [target, setTarget] = React.useState<{
    job: Job;
    decision: "approved" | "rejected";
  } | null>(null);
  const [remarks, setRemarks] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [history, setHistory] = React.useState<ApprovalTransition[]>([]);
  const [historyJob, setHistoryJob] = React.useState<Job | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<Job[] | { jobs: Job[] }>("/jobs");
      const all = Array.isArray(res) ? res : res.jobs ?? [];
      setJobs(all.filter((j) => PENDING_STATUSES.includes(j.status)));
    } catch (e) {
      toast({
        title: "Failed to load approvals",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const decide = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await apiPost(`/jobs/${target.job.id}/approve`, {
        decision: target.decision,
        ...(remarks.trim() ? { remarks: remarks.trim() } : {}),
      });
      toast({
        title:
          target.decision === "approved" ? "Job approved" : "Job rejected",
        description: target.job.title,
      });
      setTarget(null);
      setRemarks("");
      void load();
    } catch (e) {
      toast({
        title: "Decision failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const showHistory = async (job: Job) => {
    setHistoryJob(job);
    try {
      const res = await apiGet<
        ApprovalTransition[] | { approvals: ApprovalTransition[] }
      >(`/jobs/${job.id}/approvals`);
      setHistory(Array.isArray(res) ? res : res.approvals ?? []);
    } catch {
      setHistory([]);
    }
  };

  return (
    <div>
      <PageHeader
        title="My Approvals"
        description="Jobs currently in the approval chain. Act on the ones assigned to you at their current level."
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Title</TableHead>
            <TableHead>Department</TableHead>
            <TableHead>Current level</TableHead>
            <TableHead className="text-right">Decision</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          ) : jobs.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                Nothing awaiting approval.
              </TableCell>
            </TableRow>
          ) : (
            jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell className="font-medium">
                  <button
                    className="underline-offset-2 hover:underline"
                    onClick={() => void showHistory(job)}
                  >
                    {job.title}
                  </button>
                </TableCell>
                <TableCell>{job.department}</TableCell>
                <TableCell>
                  <StatusBadge status={job.status} />
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button
                      size="sm"
                      className="gap-1"
                      onClick={() => setTarget({ job, decision: "approved" })}
                    >
                      <Check className="h-4 w-4" /> Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-1"
                      onClick={() => setTarget({ job, decision: "rejected" })}
                    >
                      <X className="h-4 w-4" /> Reject
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      {/* Decision dialog with remarks */}
      <Dialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) {
            setTarget(null);
            setRemarks("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {target?.decision === "approved" ? "Approve" : "Reject"} “
              {target?.job.title}”
            </DialogTitle>
            <DialogDescription>
              The transition is logged with your identity, timestamp and
              remarks.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="Remarks (optional)"
            rows={3}
            value={remarks}
            onChange={(e) => setRemarks(e.target.value)}
          />
          <DialogFooter>
            <Button
              variant={target?.decision === "rejected" ? "outline" : "default"}
              onClick={() => void decide()}
              disabled={busy}
            >
              {busy
                ? "Submitting…"
                : target?.decision === "approved"
                  ? "Confirm approval"
                  : "Confirm rejection"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Approval history dialog */}
      <Dialog
        open={historyJob !== null}
        onOpenChange={(open) => {
          if (!open) setHistoryJob(null);
        }}
      >
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Approval history — {historyJob?.title}</DialogTitle>
          </DialogHeader>
          {history.length === 0 ? (
            <p className="text-sm text-muted-foreground">No transitions yet.</p>
          ) : (
            <ul className="space-y-2">
              {history.map((h, i) => (
                <li
                  key={h.id ?? i}
                  className="flex items-start justify-between rounded-md border p-3 text-sm"
                >
                  <div>
                    <p className="font-medium capitalize">
                      {h.level}
                      {h.skipped || h.decision === "skipped"
                        ? " — skipped (inactive level)"
                        : ` — ${h.decision ?? "pending"}`}
                    </p>
                    {h.remarks ? (
                      <p className="text-muted-foreground">“{h.remarks}”</p>
                    ) : null}
                    <p className="text-xs text-muted-foreground">
                      {h.actor_name ?? h.actor ?? ""}
                    </p>
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {h.created_at
                      ? new Date(h.created_at).toLocaleString()
                      : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
