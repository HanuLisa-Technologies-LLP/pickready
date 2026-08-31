"use client";

// Provider Portal -> Classification (Master Directive Part 3 section 9).
//
// Three surfaces on one page, none of which the customer ever sees:
//
//   * the Review Queue: jobs whose stem-score landed in the section 4.4
//     tentative band (0.30-0.79) or whose classification came from the
//     engine-error fallback, for the Hanulisa team to verify by hand;
//   * Reclassify: the support function, permitted STRICTLY before the first
//     completed assessment (Rule 5). The backend refuses it after; this page
//     surfaces that refusal rather than hiding the button, because a support
//     agent needs to learn "locked" from the attempt, not from silence;
//   * the STEM vs Non-STEM commercial split per customer, read from the
//     ledger's own audit trail.
//
// Types are declared locally rather than in lib/types.ts: this surface is
// Provider-only, and keeping its contract beside its one consumer keeps the
// shared types file about shapes more than one page reads.

import * as React from "react";
import { RefreshCcw } from "lucide-react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  Section,
} from "@/components/page-primitives";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";

interface ReviewItem {
  job_id: string;
  tenant_id: string;
  customer_name: string | null;
  title: string;
  role_classification: string;
  classification_confidence: number;
  classification_signals: string[];
  classification_locked: boolean;
  classification_overridden: boolean;
  created_at: string;
}

interface SplitRow {
  tenant_id: string;
  customer_name: string | null;
  stem_jobs: number;
  non_stem_jobs: number;
  stem_credits_consumed: number;
  non_stem_credits_consumed: number;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function ClassificationAdminPage() {
  const { toast } = useToast();
  const [queue, setQueue] = React.useState<ReviewItem[]>([]);
  const [split, setSplit] = React.useState<SplitRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  // The reclassify dialog. `target` doubles as the open flag.
  const [target, setTarget] = React.useState<ReviewItem | null>(null);
  const [reason, setReason] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [queueRows, splitRows] = await Promise.all([
        apiGet<ReviewItem[]>("/provider/classification/review-queue"),
        apiGet<SplitRow[]>("/provider/classification/split"),
      ]);
      setQueue(queueRows);
      setSplit(splitRows);
    } catch (error) {
      setLoadError(
        error instanceof ApiError || error instanceof Error
          ? error.message
          : "Could not load classification data."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const reclassify = async () => {
    if (!target) return;
    if (!reason.trim()) {
      toast({
        title: "A reason is required",
        description: "The reclassification is audit-logged with it.",
        variant: "destructive",
      });
      return;
    }
    setSaving(true);
    try {
      const next =
        target.role_classification === "STEM" ? "NON_STEM" : "STEM";
      await apiPost(`/provider/jobs/${target.job_id}/reclassify`, {
        role_classification: next,
        reason: reason.trim(),
      });
      toast({
        title: "Job reclassified",
        description: `${target.title} is now ${next === "STEM" ? "STEM (1.5 credits/report)" : "Non-STEM (1.0 credit/report)"}.`,
      });
      setTarget(null);
      setReason("");
      await load();
    } catch (error) {
      // The one refusal that matters: assessments have completed and the
      // classification is locked (Rule 5). The backend's message says to
      // compensate with a credit adjustment instead; show it verbatim.
      toast({
        title: "Could not reclassify",
        description:
          error instanceof ApiError || error instanceof Error
            ? error.message
            : "Try again in a moment.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Classification"
        description="STEM / Non-STEM review queue, reclassification, and the per-customer commercial split."
        actions={
          <Button variant="outline" size="sm" onClick={() => void load()}>
            <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            Refresh
          </Button>
        }
      />

      {loading ? (
        <LoadingRows rows={4} label="Loading classification data" />
      ) : loadError ? (
        <ErrorState
          title="Could not load classification data"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          }
        />
      ) : (
        <div className="space-y-6">
          <Section
            title="Review queue"
            description="Tentative classifications (stem-score 0.30 to 0.79) and engine-error fallbacks, newest first. Verify each against the actual role."
          >
            {queue.length === 0 ? (
              <EmptyState
                title="Nothing to review"
                description="Every recent classification landed outside the tentative band."
              />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Job</TableHead>
                      <TableHead>Customer</TableHead>
                      <TableHead>Classified as</TableHead>
                      <TableHead>Confidence</TableHead>
                      <TableHead>Signals</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {queue.map((row) => (
                      <TableRow key={row.job_id}>
                        <TableCell className="font-semibold">{row.title}</TableCell>
                        <TableCell>{row.customer_name ?? "-"}</TableCell>
                        <TableCell>
                          {row.role_classification === "STEM"
                            ? "STEM, 1.5 credits/report"
                            : "Non-STEM, 1.0 credit/report"}
                          {row.classification_overridden ? " (overridden)" : ""}
                        </TableCell>
                        <TableCell>
                          {(row.classification_confidence * 100).toFixed(0)}%
                        </TableCell>
                        <TableCell className="max-w-[22rem]">
                          <span className="line-clamp-2 text-xs opacity-80">
                            {row.classification_signals.length
                              ? row.classification_signals.join(", ")
                              : "none"}
                          </span>
                        </TableCell>
                        <TableCell>{formatDate(row.created_at)}</TableCell>
                        <TableCell className="text-right">
                          {row.classification_locked ? (
                            <span className="text-xs font-medium opacity-70">
                              Locked (assessments completed)
                            </span>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => {
                                setTarget(row);
                                setReason("");
                              }}
                            >
                              Reclassify
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>

          <Section
            title="STEM vs Non-STEM split by customer"
            description="Job counts by type, and credits actually consumed at each rate, read from the ledger's audit trail."
          >
            {split.length === 0 ? (
              <EmptyState title="No customers yet" description="" />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Customer</TableHead>
                      <TableHead className="text-right">STEM jobs</TableHead>
                      <TableHead className="text-right">Non-STEM jobs</TableHead>
                      <TableHead className="text-right">STEM credits used</TableHead>
                      <TableHead className="text-right">Non-STEM credits used</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {split.map((row) => (
                      <TableRow key={row.tenant_id}>
                        <TableCell className="font-semibold">
                          {row.customer_name ?? row.tenant_id}
                        </TableCell>
                        <TableCell className="text-right">{row.stem_jobs}</TableCell>
                        <TableCell className="text-right">{row.non_stem_jobs}</TableCell>
                        <TableCell className="text-right">
                          {row.stem_credits_consumed.toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right">
                          {row.non_stem_credits_consumed.toFixed(2)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>
        </div>
      )}

      <Dialog
        open={target !== null}
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Reclassify this job</DialogTitle>
            <DialogDescription>
              {target
                ? `${target.title} is currently ${
                    target.role_classification === "STEM" ? "STEM" : "Non-STEM"
                  }. Reclassifying flips it to ${
                    target.role_classification === "STEM" ? "Non-STEM" : "STEM"
                  } and is audit-logged. Only possible before any completed assessment.`
                : null}
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why is the engine's classification wrong for this role?"
            rows={3}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setTarget(null)}>
              Cancel
            </Button>
            <Button onClick={() => void reclassify()} disabled={saving}>
              {saving ? "Saving" : "Confirm reclassification"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
