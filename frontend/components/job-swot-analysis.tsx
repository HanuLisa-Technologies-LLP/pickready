"use client";

/**
 * The Job SWOT Analysis panel (2026-09-13 spec, sections 23 to 33).
 *
 * THE WORKFLOW THIS RENDERS
 * -------------------------
 *     AI drafts -> the team reads it -> an authorized user edits it -> the
 *     final SWOT is theirs.
 *
 * The model's draft is a draft. Everything here is arranged so that reads as
 * true to somebody who has never been told it: the generated state says who
 * wrote it and when, the edit control sits beside the text rather than behind
 * a menu, and a regeneration over edited content asks before it replaces
 * anything and can be undone after it does.
 *
 * PERMISSION-AWARE, FROM THE SERVER'S ANSWER
 * -------------------------------------------
 * `analysis.can_edit` is resolved server-side by the same authorization call
 * the write routes enforce with, so it carries the parts a capability list
 * cannot: whether this person is assigned to THIS job, and whether the job's
 * lifecycle state still permits the edit. The capability on its own is the
 * fallback while the payload is loading, which is what `resolvePermission`
 * does. Neither is a security boundary; both routes re-authorize.
 *
 * The read-only sentence is rendered by `<ReadOnlyNotice>` and by nothing
 * else, so it cannot appear on a surface the user may in fact edit.
 */

import * as React from "react";
import {
  AlertTriangle,
  Loader2,
  Pencil,
  RotateCcw,
  Save,
  Sparkles,
  Undo2,
  X,
} from "lucide-react";

import { apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import type { SwotAnalysis, SwotAnalysisDraft } from "@/lib/types";
import { CAP, resolvePermission } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";
import { ReadOnlyNotice } from "@/components/permission-notice";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FormField } from "@/components/ui/form";
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import { cn } from "@/lib/utils";

const SECTIONS: { key: keyof SwotAnalysisDraft; label: string; hint: string }[] = [
  {
    key: "strengths",
    label: "Strengths",
    hint: "What makes this role attractive and straightforward to hire for.",
  },
  {
    key: "weaknesses",
    label: "Weaknesses",
    hint: "What makes it hard to fill, or hard to succeed in.",
  },
  {
    key: "opportunities",
    label: "Opportunities",
    hint: "What the recruitment team can act on to widen the funnel.",
  },
  {
    key: "threats",
    label: "Threats",
    hint: "What could stall this hire or cost you the candidate.",
  },
];

const EMPTY_DRAFT: SwotAnalysisDraft = {
  strengths: "",
  weaknesses: "",
  opportunities: "",
  threats: "",
};

function draftFrom(analysis: SwotAnalysis | null): SwotAnalysisDraft {
  if (!analysis) return EMPTY_DRAFT;
  return {
    strengths: analysis.strengths ?? "",
    weaknesses: analysis.weaknesses ?? "",
    opportunities: analysis.opportunities ?? "",
    threats: analysis.threats ?? "",
  };
}

function hasContent(analysis: SwotAnalysis | null): boolean {
  if (!analysis) return false;
  return SECTIONS.some((s) => (analysis[s.key] ?? "").trim().length > 0);
}

/** Who last touched the document, as one sentence. */
function provenance(analysis: SwotAnalysis): string | null {
  if (analysis.human_edited) {
    const who = analysis.last_modified_by_name;
    return who
      ? `Reviewed and edited by ${who}.`
      : "Reviewed and edited by your team.";
  }
  if (analysis.generated_by === "ai") {
    return "Drafted by AI. Review it and edit anything that is wrong.";
  }
  return null;
}

export function JobSwotAnalysisPanel({
  jobId,
  className,
}: {
  jobId: string;
  className?: string;
}) {
  const { toast } = useToast();
  const { can } = usePermissions();

  const [analysis, setAnalysis] = React.useState<SwotAnalysis | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [generating, setGenerating] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [restoring, setRestoring] = React.useState(false);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState<SwotAnalysisDraft>(EMPTY_DRAFT);
  const [confirmOverwrite, setConfirmOverwrite] = React.useState(false);

  // The capability answer stands in until the payload arrives with the
  // resource-scoped one, so the Edit control does not flicker on every load.
  const canEdit = resolvePermission(can(CAP.editSwot), analysis?.can_edit);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await apiGet<SwotAnalysis>(`/jobs/${jobId}/swot-analysis`);
      setAnalysis(res);
      setDraft(draftFrom(res));
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "The SWOT could not be loaded."
      );
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const runGeneration = async (overwrite: boolean) => {
    setGenerating(true);
    setConfirmOverwrite(false);
    try {
      const res = await apiPost<SwotAnalysis>(
        `/jobs/${jobId}/swot-analysis/generate`,
        { confirm_overwrite: overwrite }
      );
      setAnalysis(res);
      setDraft(draftFrom(res));
      setEditing(false);
      if (res.status === "failed") {
        // A failed generation is a STATE, not an exception: the panel keeps
        // whatever was already there and says why the new draft did not
        // arrive. It never renders an invented SWOT.
        toast({
          title: "The SWOT could not be generated",
          description: res.generation_error ?? undefined,
          variant: "destructive",
        });
        return;
      }
      toast({
        title: "SWOT drafted",
        description: "Read it through and edit anything that is wrong.",
      });
    } catch (error) {
      // 409 is the confirmation gate, not a failure: the document carries
      // human edits and the server is asking before replacing them.
      if (error instanceof ApiError && error.status === 409) {
        setConfirmOverwrite(true);
        return;
      }
      toast({
        title: "The SWOT could not be generated",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setGenerating(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await apiPut<SwotAnalysis>(`/jobs/${jobId}/swot-analysis`, {
        ...draft,
        expected_version: analysis?.version ?? null,
      });
      setAnalysis(res);
      setDraft(draftFrom(res));
      setEditing(false);
      toast({ title: "SWOT saved", description: "Your edits are the version in force." });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast({
          title: "Someone else saved first",
          description: error.message,
          variant: "destructive",
        });
        void load();
        return;
      }
      toast({
        title: "The SWOT could not be saved",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const restore = async () => {
    setRestoring(true);
    try {
      const res = await apiPost<SwotAnalysis>(
        `/jobs/${jobId}/swot-analysis/restore`
      );
      setAnalysis(res);
      setDraft(draftFrom(res));
      setEditing(false);
      toast({ title: "Earlier version restored" });
    } catch (error) {
      toast({
        title: "Nothing could be restored",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRestoring(false);
    }
  };

  const populated = hasContent(analysis);
  const busy = generating || saving || restoring;

  return (
    <Card className={cn("mb-6", className)}>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4 space-y-0">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            SWOT analysis
            {analysis?.human_edited ? (
              <Badge variant="secondary">Edited by your team</Badge>
            ) : analysis?.generated_by === "ai" && populated ? (
              <Badge variant="secondary">AI draft</Badge>
            ) : null}
          </CardTitle>
          <CardDescription>
            How this role sits in the market, and what it will take to fill it.
          </CardDescription>
        </div>

        {/* Controls exist only for a user the server would let write. */}
        {canEdit && !editing && !loading ? (
          <div className="flex flex-wrap gap-2">
            {populated ? (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                disabled={busy}
                onClick={() => setEditing(true)}
              >
                <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
              </Button>
            ) : null}
            {analysis?.can_restore_previous ? (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                disabled={busy}
                onClick={() => void restore()}
              >
                <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
                {restoring ? "Restoring" : "Undo regeneration"}
              </Button>
            ) : null}
            <Button
              variant={populated ? "outline" : "default"}
              size="sm"
              className="gap-1.5"
              disabled={busy}
              onClick={() => void runGeneration(false)}
            >
              {generating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : populated ? (
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {generating
                ? "Generating"
                : populated
                  ? "Regenerate"
                  : "Generate with AI"}
            </Button>
          </div>
        ) : null}
      </CardHeader>

      <CardContent className="space-y-4 text-sm">
        {loading ? (
          <LoadingRows rows={4} label="Loading the SWOT analysis" />
        ) : loadError ? (
          <ErrorState
            title="This SWOT could not be loaded"
            description={loadError}
            action={
              <Button variant="outline" onClick={() => void load()}>
                Try again
              </Button>
            }
          />
        ) : editing ? (
          <div className="space-y-4">
            {SECTIONS.map((section) => (
              <FormField
                key={section.key}
                label={section.label}
                htmlFor={`swot-${section.key}`}
                hint={section.hint}
              >
                <Textarea
                  id={`swot-${section.key}`}
                  rows={4}
                  value={draft[section.key]}
                  onChange={(e) =>
                    setDraft({ ...draft, [section.key]: e.target.value })
                  }
                />
              </FormField>
            ))}
            <div className="flex flex-wrap gap-2">
              <Button className="gap-1.5" disabled={saving} onClick={() => void save()}>
                {saving ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Save className="h-4 w-4" aria-hidden="true" />
                )}
                {saving ? "Saving" : "Save SWOT"}
              </Button>
              <Button
                variant="outline"
                className="gap-1.5"
                disabled={saving}
                onClick={() => {
                  setDraft(draftFrom(analysis));
                  setEditing(false);
                }}
              >
                <X className="h-4 w-4" aria-hidden="true" /> Cancel
              </Button>
            </div>
          </div>
        ) : (
          <>
            {analysis?.status === "failed" && analysis.generation_error ? (
              <div
                role="alert"
                className="flex items-start gap-3 rounded-xl border border-destructive/40 p-4"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0"
                  aria-hidden="true"
                />
                <div>
                  <p className="font-medium">The last generation did not finish</p>
                  <p className="mt-1">{analysis.generation_error}</p>
                </div>
              </div>
            ) : null}

            {populated ? (
              <div className="space-y-4">
                {SECTIONS.map((section) => {
                  const value = (analysis?.[section.key] ?? "").trim();
                  if (!value) return null;
                  return (
                    <div key={section.key}>
                      <p className="font-semibold">{section.label}</p>
                      <p className="mt-1 whitespace-pre-wrap leading-6">{value}</p>
                    </div>
                  );
                })}
                {analysis ? (
                  <p className="text-xs">{provenance(analysis)}</p>
                ) : null}
              </div>
            ) : (
              <p>
                {canEdit
                  ? "No SWOT yet. Generate a first draft from this job, then edit it into shape."
                  : "No SWOT has been written for this job yet."}
              </p>
            )}

            {/* Nothing at all for a user who may edit. */}
            <ReadOnlyNotice canEdit={canEdit} resource="this SWOT analysis" />
          </>
        )}
      </CardContent>

      {/* Section 32: regeneration never silently destroys human edits. */}
      <Dialog open={confirmOverwrite} onOpenChange={setConfirmOverwrite}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Replace what your team wrote?</DialogTitle>
            <DialogDescription>
              This SWOT has been edited by a person. Regenerating replaces all
              four sections with a fresh AI draft. You can undo it afterwards.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOverwrite(false)}>
              Keep the current version
            </Button>
            <Button onClick={() => void runGeneration(true)}>
              Replace with a new draft
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
