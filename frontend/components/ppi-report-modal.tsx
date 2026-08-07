"use client";

// The PPI Assessment Report modal (spec §10): opened from the PPI Report button
// in the job page's candidate table.
//
// The report is IMMUTABLE: there is no edit control and no delete control
// anywhere in this component, and the backend answers PATCH/PUT/DELETE on the
// report route with 403. The UI is not the enforcement, it just never offers
// something the server would refuse.
//
// Section order is fixed (spec §10.3): AI Score -> Overall Assessment ->
// Primary Skills -> Secondary Skills -> Behavioural Competencies -> Validation
// -> Suggested interview questions.

import * as React from "react";
import { Download, Loader2, Lock } from "lucide-react";

import { apiGet } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  FunctionalSkillsReportView,
  type FunctionalReport,
} from "@/components/functional-skills-report";
import { Button } from "@/components/ui/button";

export function PPIReportModal({
  open,
  onOpenChange,
  linkId,
  candidateName,
  jobTitle,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  linkId: string | null;
  candidateName: string;
  jobTitle?: string | null;
}) {
  const { toast } = useToast();
  const [report, setReport] = React.useState<FunctionalReport | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open || !linkId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setReport(null);
    // Absolute /api/ path: the fetch wrapper routes these past API_BASE
    // (which is pinned to /api/v1) to the origin, so a v2 route is reachable.
    apiGet<FunctionalReport>(`/api/v2/assessments/reports/links/${linkId}`)
      .then((res) => {
        if (!cancelled) setReport(res);
      })
      .catch((e) => {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : "Couldn't load the report";
        setError(message);
        toast({ title: "Report unavailable", description: message, variant: "destructive" });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, linkId, toast]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-5xl overflow-y-auto">
        <DialogHeader>
          <div className="flex flex-wrap items-start justify-between gap-3 pr-8">
            <DialogTitle>
              PPI Assessment Report, {candidateName}
              {jobTitle ? ` (${jobTitle})` : ""}
            </DialogTitle>
            {linkId && report ? (
              <Button asChild size="sm" variant="outline">
                <a
                  href={`/api/v2/assessments/reports/links/${linkId}/pdf`}
                  download
                >
                  <Download className="mr-2 h-4 w-4" aria-hidden />
                  Download PDF
                </a>
              </Button>
            ) : null}
          </div>
          <DialogDescription className="flex items-center gap-1.5">
            <Lock className="h-3.5 w-3.5" aria-hidden />
            This report is a permanent record and cannot be edited or deleted.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : error ? (
          <p className="py-10 text-center text-sm">{error}</p>
        ) : report ? (
          <FunctionalSkillsReportView report={report} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
