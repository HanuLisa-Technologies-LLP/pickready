"use client";

/**
 * The Job SWOT Analysis panel (2026-09-13 spec, sections 23 to 33; three-stage
 * presentation and the embedded reporting authority intake, owner ruling
 * 2026-09-19).
 *
 * THE WORKFLOW THIS RENDERS
 * -------------------------
 *     AI drafts -> the team reads it -> an authorized user edits it -> the
 *     final SWOT is theirs.
 *
 * Three stages, decided per panel and rendered per section:
 *
 *   1. Nothing yet: each of the four sections is an editable field with a
 *      placeholder saying what belongs in it, so a team that wants to write
 *      its own SWOT can, and Generate with AI drafts all four at once.
 *   2. Generating: the same four fields, disabled, each saying a draft is on
 *      its way, under a thin animated bar. Nothing is editable mid-draft
 *      because a save would race the generation it is about to replace.
 *   3. Populated: each section is read-only prose with its own provenance
 *      word and an Edit control opening a dialog, so a reader is never handed
 *      four open textareas for a document that is usually only read.
 *
 * The model's draft is a draft: the generated state says who wrote it, a
 * regeneration over edited content asks before it replaces anything, and can
 * be undone after it does.
 *
 * GENERATION IS DISPATCHED WORK (Vivekium release, Phase 1)
 * ---------------------------------------------------------
 * The model call used to run inside the request, which rule 4 forbids. Now
 * Generate answers at once with the document in a `generating` state, and the
 * panel re-reads it until the worker has written `generated` or `failed`. The
 * server serves a draft that outlived its window as `failed`, so the poll ends
 * on its own; the limit here is only a backstop. A 409 is the human-edit
 * confirmation ONLY when the document carries human edits and the overwrite
 * was not yet confirmed; any other 409 (a JD too thin to draft from) is a
 * refusal, shown in the server's words.
 *
 * THE SKILLS FOLLOW THE SWOT, AND NEVER SILENTLY
 * ----------------------------------------------
 * The first human save of a SWOT on a job with no skills starts Sutra's skills
 * draft on the server. A later save only makes a re-draft AVAILABLE: the
 * response says so (`skills_redraft_available`) and this panel offers
 * "Re-draft skills from the updated SWOT", which opens the Skills panel's own
 * confirmation. Nothing is re-drafted without that second click.
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
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import { cn } from "@/lib/utils";

type SectionKey = keyof SwotAnalysisDraft;

const SECTIONS: {
  key: SectionKey;
  label: string;
  placeholder: string;
}[] = [
  {
    key: "strengths",
    label: "Strengths",
    placeholder:
      "What gives this role a market advantage: the mandate, the team, the technology, the growth on offer.",
  },
  {
    key: "weaknesses",
    label: "Weaknesses",
    placeholder:
      "What makes this role harder to fill, or harder to succeed in: a narrow skill profile, a stretched brief, a demanding band.",
  },
  {
    key: "opportunities",
    label: "Opportunities",
    placeholder:
      "What the recruitment team can act on to widen the funnel: adjacent profiles that convert, sourcing channels, timing.",
  },
  {
    key: "threats",
    label: "Threats",
    placeholder:
      "What in the market could stall this hire or cost you the candidate: competing offers, market movement, internal delays.",
  },
];

const GENERATING_PLACEHOLDER =
  "AI is generating a practical assessment for this section...";

const STAGE_ONE_HINT =
  "Write your own assessment, or use Generate with AI to draft this section.";

/** How often a generating SWOT is re-read, and the backstop on how long. */
const GENERATION_POLL_MS = 3000;
const GENERATION_POLL_LIMIT = 120;

/** The assessments router is mounted at /api/v2 ONLY (backend main.py), so
 *  the prefix is written in full: a path relative to API_BASE resolves to
 *  /api/v1/jobs/... and 404s. `api-mount-parity.test.ts` pins this. */
const BASE = "/api/v2/assessments/jobs";

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

/**
 * The per-section status word. One of: Not started, AI drafting, AI
 * generated, Edited, Saved. A word rather than an icon, because the status is
 * provenance and provenance is read, not decoded.
 */
function sectionStatus(
  analysis: SwotAnalysis | null,
  key: SectionKey,
  draftValue: string,
  generating: boolean
): string {
  if (generating) return "AI drafting";
  if (!draftValue.trim()) return "Not started";
  if (draftValue !== (analysis?.[key] ?? "") || analysis?.human_edited) {
    return "Edited";
  }
  if (analysis?.generated_by === "ai") return "AI generated";
  return "Saved";
}

export function JobSwotAnalysisPanel({
  jobId,
  className,
  onSaved,
  canRedraftSkills = false,
  onRequestSkillsRedraft,
}: {
  jobId: string;
  className?: string;
  /** Told after a save or a restore lands, so the skills and the publish
   *  checklist re-read: the first save starts the skills draft. */
  onSaved?: (analysis: SwotAnalysis) => void;
  /** The capability half of "may this person re-draft the skills". The
   *  Skills panel re-checks with the server's per-job answer. */
  canRedraftSkills?: boolean;
  /** Opens the Skills panel's re-draft confirmation. Never drafts by itself. */
  onRequestSkillsRedraft?: () => void;
}) {
  const { toast } = useToast();
  const { can } = usePermissions();

  const [analysis, setAnalysis] = React.useState<SwotAnalysis | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  // The Generate request itself is in flight. What is being drafted after it
  // answers is the DOCUMENT's state, read from `analysis.status` below.
  const [requesting, setRequesting] = React.useState(false);
  const [refusal, setRefusal] = React.useState<string | null>(null);
  const [pollExhausted, setPollExhausted] = React.useState(false);
  const polls = React.useRef(0);
  // Set once this tab asked for the draft, so its arrival is announced here
  // and not on somebody else's generation this tab merely observed.
  const awaitingDraft = React.useRef(false);
  const [saving, setSaving] = React.useState(false);
  // State disables the button after render; this ref closes the same-tick
  // window in which a double click can otherwise submit the same version.
  const saveInFlight = React.useRef(false);
  const [restoring, setRestoring] = React.useState(false);
  const [draft, setDraft] = React.useState<SwotAnalysisDraft>(EMPTY_DRAFT);
  const [confirmOverwrite, setConfirmOverwrite] = React.useState(false);
  // The section whose Edit dialog is open, and the text inside it. The dialog
  // writes into the DRAFT on save; the footer's Save is what reaches the API.
  const [editingSection, setEditingSection] = React.useState<SectionKey | null>(
    null
  );
  const [editValue, setEditValue] = React.useState("");

  // The capability answer stands in until the payload arrives with the
  // resource-scoped one, so the Edit control does not flicker on every load.
  const canEdit = resolvePermission(can(CAP.editSwot), analysis?.can_edit);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await apiGet<SwotAnalysis>(`${BASE}/${jobId}/swot-analysis`);
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

  /** Tell the person who asked how their draft ended. */
  const announce = React.useCallback(
    (res: SwotAnalysis) => {
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
        description: "Read it through, edit anything that is wrong, and save it.",
      });
    },
    [toast]
  );

  // Re-read a generating document until the worker has finished with it.
  const documentGenerating = analysis?.status === "generating";
  React.useEffect(() => {
    if (!documentGenerating) return;
    if (polls.current >= GENERATION_POLL_LIMIT) {
      setPollExhausted(true);
      return;
    }
    const timer = window.setTimeout(async () => {
      polls.current += 1;
      try {
        const res = await apiGet<SwotAnalysis>(`${BASE}/${jobId}/swot-analysis`);
        setAnalysis(res);
        if (res.status !== "generating") {
          setDraft(draftFrom(res));
          if (awaitingDraft.current) {
            awaitingDraft.current = false;
            announce(res);
          }
        }
      } catch (error) {
        // One unreadable poll ends the wait with the reason on screen rather
        // than spinning on a document this tab can no longer read.
        setLoadError(
          error instanceof Error ? error.message : "The SWOT could not be loaded."
        );
      }
    }, GENERATION_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [documentGenerating, analysis, jobId, announce]);

  const runGeneration = async (overwrite: boolean) => {
    setRequesting(true);
    setConfirmOverwrite(false);
    setEditingSection(null);
    setRefusal(null);
    polls.current = 0;
    setPollExhausted(false);
    try {
      const res = await apiPost<SwotAnalysis>(
        `${BASE}/${jobId}/swot-analysis/generate`,
        { confirm_overwrite: overwrite }
      );
      setAnalysis(res);
      setDraft(draftFrom(res));
      if (res.status === "generating") {
        // Accepted and dispatched: the poll above takes it from here.
        awaitingDraft.current = true;
        return;
      }
      announce(res);
    } catch (error) {
      // 409 is the confirmation gate ONLY when there are human edits to lose
      // and the overwrite was not yet confirmed. Any other 409 is a refusal
      // the server words, such as a JD too thin to draft from.
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        !overwrite &&
        analysis?.human_edited
      ) {
        setConfirmOverwrite(true);
        return;
      }
      setRefusal(
        error instanceof Error ? error.message : "The SWOT could not be generated."
      );
    } finally {
      setRequesting(false);
    }
  };

  const save = async () => {
    if (saveInFlight.current || !analysis) return;
    saveInFlight.current = true;
    setSaving(true);
    try {
      const res = await apiPut<SwotAnalysis>(`${BASE}/${jobId}/swot-analysis`, {
        ...draft,
        expected_version: analysis.version,
      });
      setAnalysis(res);
      setDraft(draftFrom(res));
      onSaved?.(res);
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
      saveInFlight.current = false;
      setSaving(false);
    }
  };

  const restore = async () => {
    setRestoring(true);
    try {
      const res = await apiPost<SwotAnalysis>(
        `${BASE}/${jobId}/swot-analysis/restore`
      );
      setAnalysis(res);
      setDraft(draftFrom(res));
      onSaved?.(res);
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

  const generating = requesting || documentGenerating;
  const populated = hasContent(analysis);
  const busy = generating || saving || restoring;
  const offerSkillsRedraft =
    !generating &&
    Boolean(analysis?.skills_redraft_available) &&
    canRedraftSkills &&
    Boolean(onRequestSkillsRedraft);
  const draftHasContent = SECTIONS.some((s) => draft[s.key].trim().length > 0);
  const editingLabel =
    SECTIONS.find((s) => s.key === editingSection)?.label ?? "";

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

        {/* Controls exist only for a user the server would let write. Once
            the SWOT is populated, generation moves to the footer as
            Regenerate, so the header stays quiet over a finished document. */}
        {canEdit && !loading && !loadError && !populated ? (
          <Button
            size="sm"
            className="gap-1.5"
            disabled={busy}
            onClick={() => void runGeneration(false)}
          >
            {generating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {generating ? "Generating" : "Generate with AI"}
          </Button>
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
        ) : (
          <>
            {!generating &&
            analysis?.status === "failed" &&
            analysis.generation_error ? (
              <div
                role="alert"
                className="flex items-start gap-3 border border-destructive/40 p-4"
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

            {refusal ? (
              <p role="alert" className="border border-destructive/40 p-4">
                {refusal}
              </p>
            ) : null}

            {generating && pollExhausted ? (
              <p role="status">
                The draft is taking longer than expected. Refresh the page to
                check on it.
              </p>
            ) : null}

            {generating ? (
              // The bar's motion inherits the reduced-motion rules in
              // globals.css via motion-safe, so it holds still for anyone who
              // asked everything to.
              <div aria-hidden="true" className="h-0.5 w-full bg-muted">
                <div className="h-full w-full bg-teal-600 motion-safe:animate-pulse" />
              </div>
            ) : null}

            {!populated && !canEdit && !generating ? (
              <p>No SWOT has been written for this job yet.</p>
            ) : (
              <div className="space-y-3">
                {SECTIONS.map((section) => {
                  const value = draft[section.key];
                  // Stage 3 is populated-and-not-generating; stage 2 is
                  // generating; everything else is stage 1's open field.
                  const readMode = populated && !generating;
                  return (
                    <div key={section.key} className="border p-4">
                      <div className="flex items-baseline justify-between gap-3">
                        <p className="text-xs font-semibold uppercase tracking-wide">
                          {section.label}
                        </p>
                        <p className="shrink-0 text-xs">
                          {sectionStatus(analysis, section.key, value, generating)}
                        </p>
                      </div>
                      {readMode ? (
                        <div className="mt-2">
                          {value.trim() ? (
                            <p className="whitespace-pre-wrap leading-6">
                              {value}
                            </p>
                          ) : (
                            <p className="text-xs">
                              Nothing written for this section yet.
                            </p>
                          )}
                          {canEdit ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="mt-3 gap-1.5"
                              disabled={busy}
                              onClick={() => {
                                setEditValue(value);
                                setEditingSection(section.key);
                              }}
                            >
                              <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                              Edit
                            </Button>
                          ) : null}
                        </div>
                      ) : (
                        <div className="mt-2 space-y-2">
                          <Textarea
                            rows={3}
                            value={generating ? "" : value}
                            disabled={generating}
                            placeholder={
                              generating
                                ? GENERATING_PLACEHOLDER
                                : section.placeholder
                            }
                            aria-label={section.label}
                            onChange={(e) =>
                              setDraft({
                                ...draft,
                                [section.key]: e.target.value,
                              })
                            }
                          />
                          {generating ? null : (
                            <p className="text-xs">{STAGE_ONE_HINT}</p>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}

                {populated && !generating && analysis ? (
                  <p className="text-xs">{provenance(analysis)}</p>
                ) : null}
              </div>
            )}

            {/* Nothing at all for a user who may edit. */}
            <ReadOnlyNotice canEdit={canEdit} resource="this SWOT analysis" />

            {/* The saved SWOT moved on from the one the skills were drafted
                from. Offered, never done: the click opens the Skills panel's
                confirmation, which names anything the team wrote itself. */}
            {offerSkillsRedraft ? (
              <div
                role="status"
                className="flex flex-wrap items-center justify-between gap-3 border p-4"
              >
                <p>
                  The SWOT changed after the skills were drafted. Sutra can
                  re-draft them from this version; nothing changes until you
                  confirm.
                </p>
                <Button
                  variant="outline"
                  className="gap-1.5"
                  onClick={() => onRequestSkillsRedraft?.()}
                >
                  <RotateCcw className="h-4 w-4" aria-hidden="true" />
                  Re-draft skills from the updated SWOT
                </Button>
              </div>
            ) : null}

            {canEdit && !generating ? (
              <div className="flex flex-wrap gap-2 pt-1">
                {populated && analysis?.can_restore_previous ? (
                  <Button
                    variant="outline"
                    className="gap-1.5"
                    disabled={busy}
                    onClick={() => void restore()}
                  >
                    <Undo2 className="h-4 w-4" aria-hidden="true" />
                    {restoring ? "Restoring" : "Undo regeneration"}
                  </Button>
                ) : null}
                {populated ? (
                  <Button
                    variant="outline"
                    className="gap-1.5"
                    disabled={busy}
                    onClick={() => void runGeneration(false)}
                  >
                    <RotateCcw className="h-4 w-4" aria-hidden="true" />
                    Regenerate with AI
                  </Button>
                ) : null}
                <Button
                  className="gap-1.5"
                  disabled={busy || !draftHasContent}
                  onClick={() => void save()}
                >
                  {saving ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Save className="h-4 w-4" aria-hidden="true" />
                  )}
                  {saving ? "Saving" : "Save SWOT Analysis"}
                </Button>
              </div>
            ) : null}

          </>
        )}
      </CardContent>

      {/* One section at a time, in a dialog rather than four open textareas:
          the populated document is usually read, and an Edit that saves into
          the local draft keeps the footer's Save as the one write. */}
      <Dialog
        open={editingSection !== null}
        onOpenChange={(open) => {
          if (!open) setEditingSection(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit {editingLabel}</DialogTitle>
            <DialogDescription>
              Your change lands in the draft on this page. Save SWOT Analysis
              is what makes it the version in force.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            rows={8}
            value={editValue}
            aria-label={editingLabel}
            onChange={(e) => setEditValue(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditingSection(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                if (editingSection) {
                  setDraft({ ...draft, [editingSection]: editValue });
                }
                setEditingSection(null);
              }}
            >
              Save changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
