"use client";

// The one manual step in the pipeline (spec 10), and it has TWO halves that
// are finalised in ONE setup session:
//
//   1. The Tatva Assessment matrix: Must-have, Nice-to-have and Behavioural
//      Competencies, generated from the JD AND the reporting authority's SWOT
//      intake (spec 5.2, 5.3). The Hiring Manager reviews it with drag and
//      drop and saves it. It is the fixed criteria every candidate on this job
//      is graded against, so a human confirming it is the product's only
//      guarantee that two reports are comparable.
//   2. The job's Matching category list, generated from the JD (spec 3.2). It
//      decides how every sourced resume on this job is ranked, which is a
//      comparability guarantee of the same kind, so it gates too.
//
// Named copy, 2026-08-23: what a recruiter saves here is the Tatva Assessment
// matrix, and running it against a candidate produces a PRISM Report. Only the
// user-visible words changed. The route, the `framework` fields and the `ppi`
// module keep their old names on purpose: a route is quoted in links already
// sent and in traces a rolling deploy is still writing, and every report
// written before today was filed under those names.
//
// The SWOT intake sits above both and gates NEITHER on its own. It is an INPUT
// to the matrix, so an intake nobody completed already shows up as a matrix
// nobody approved; gating separately would give one problem two error messages.
//
// Everything after approval runs without human intervention. This screen
// therefore has one job: make the outstanding work obvious, so the step does
// not become a silent bottleneck. The status strip says exactly what is still
// blocking candidates, and the backend mails a reminder if it is left
// unapproved past the configured threshold.
//
// EVERY BLOCKER THE UI NAMES MUST HAVE A CONTROL THAT SATISFIES IT. The strip
// previously listed the technical question bank, whose control had been deleted
// in the same change, so it was unclearable by construction and read as a
// removed feature still being present.

import * as React from "react";
import { Check, Loader2, Lock, Pencil, Plus, Trash2, Unlock } from "lucide-react";

import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import { RATING_GRADES, type RatingGrade } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "./ui/textarea";
import { Badge } from "@/components/ui/badge";
import { RatingLabel } from "@/components/rating-label";
import { SwotIntakePanel } from "@/components/swot-intake";
import { MatchingCategoriesCard } from "@/components/matching-categories";
import { MonitoringPolicyCard } from "@/components/proctoring/monitoring-policy-card";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const BASE = "/api/v2/assessments/jobs";

type Category = "must_have" | "nice_to_have" | "behavioural";

const CATEGORY_ORDER: Category[] = ["must_have", "nice_to_have", "behavioural"];

const CATEGORY_LABEL: Record<Category, string> = {
  must_have: "Must-have",
  nice_to_have: "Nice-to-have",
  behavioural: "Behavioural Competencies",
};

const CATEGORY_HINT: Record<Category, string> = {
  must_have:
    "Capabilities the role cannot be performed without. Technical depth is assessed here.",
  nice_to_have:
    "Supporting capabilities that strengthen performance without being disqualifying.",
  behavioural: "Observable workplace behaviours the role demands.",
};

/**
 * Where an item may be DROPPED.
 *
 * Behavioural is deliberately absent. Spec 5.3 offers moving items "between
 * Must-have and Nice-to-have", and the server refuses a move into Behavioural:
 * a skill assessed by judgement rather than against a rubric would silently
 * change how every candidate on the job is graded on it. The UI must not offer
 * a drop the server will reject.
 */
const MOVE_TARGETS: Category[] = ["must_have", "nice_to_have"];

/**
 * The three levels a job can require. "Not Matching" is deliberately absent: a
 * job that requires nothing of a competency would not have it in its framework
 * at all, so offering it would be offering a contradiction.
 */
const REQUIREMENT_LEVELS: RatingGrade[] = RATING_GRADES.filter(
  (grade) => grade !== "Not Matching"
);

interface Competency {
  id: string;
  category: Category;
  name: string;
  description: string | null;
  required_level: RatingGrade;
  ordinal: number;
}

interface Framework {
  job_id: string;
  status: string;
  approved: boolean;
  competencies: Competency[];
  /** The most items this matrix may hold: every item is probed at least once,
   *  so the grade's question ceiling is the matrix's ceiling (spec 5.4). */
  maximum_items: number;
  /** How many questions this job's candidates will be asked, resolved from the
   *  grade's range and the matrix size. Shown so the Hiring Manager can see
   *  what adding an item actually costs the candidate. */
  question_target: number;
  /** There is NO minimum item count in Draft v4. Reported as one per aspect
   *  purely because each aspect is graded and charted on every report. */
  minimum_per_category: number;
  blocking_reason: string | null;
}

export interface Setup {
  job_id: string;
  status: string;
  grade: string | null;
  // `questions_approved` is still returned by the API and is deliberately NOT
  // declared here. It now just mirrors `framework_approved`, and leaving it off
  // the type is what stops it being wired back into a blocking message by
  // someone reading the payload rather than this file.
  framework_approved: boolean;
  /** The second half of the setup session (spec 3.2). */
  matching_categories_finalized?: boolean;
  /** Whether the reporting authority has finished the SWOT intake. Reported,
   *  never a gate on its own: see the header. */
  swot_complete?: boolean;
  ready_for_candidates: boolean;
  /**
   * The framework has not been generated yet and the backend has just enqueued
   * one. Distinct from "generated and short of a minimum": 19 of 35 live jobs
   * were in this state with nothing retrying, and the screen rendered an empty
   * list indistinguishable from a finished, empty framework.
   */
  framework_pending?: boolean;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

// ── Status strip ─────────────────────────────────────────────────────────────

export function SetupStatus({ setup }: { setup: Setup }) {
  if (setup.ready_for_candidates) {
    return (
      <div className="rounded-lg border border-emerald-700 bg-emerald-50 p-4 dark:bg-emerald-950/40">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <Check className="h-4 w-4" aria-hidden />
          Ready for candidates
        </p>
        <p className="mt-1 text-xs">
          Both halves of the setup are saved. Candidates you invite can now take
          the assessment, and everything after that runs on its own.
        </p>
      </div>
    );
  }

  // EVERY BLOCKER NAMED HERE HAS A CONTROL ON THIS PAGE THAT CLEARS IT.
  //
  // `setup.questions_approved` is deliberately NOT read: the backend stopped
  // gating on it and the button that set it is gone, so naming it would state a
  // blocker nothing can clear. The SWOT intake is not named as a blocker for a
  // different reason: it does not gate. It is an input to the matrix, so an
  // unfinished intake already shows up as a matrix nobody approved, and naming
  // it separately would give one problem two error messages.
  const outstanding: string[] = [];
  if (!setup.framework_approved) outstanding.push("save the evaluation matrix");
  if (setup.matching_categories_finalized === false) {
    outstanding.push("save the matching categories");
  }

  return (
    <div className="rounded-lg border border-amber-600 bg-amber-50 p-4 dark:bg-amber-950/40">
      <p className="text-sm font-semibold">Job setup pending review</p>
      <p className="mt-1 text-xs">
        No candidate can be invited to this job until you{" "}
        {outstanding.length > 0
          ? outstanding.join(" and ")
          : "finish the setup review"}{" "}
        below. Applications still arrive in the meantime.
      </p>
      {setup.swot_complete === false ? (
        <p className="mt-2 text-xs">
          The role intake is unfinished. It is not a blocker on its own, but the
          matrix is written from it, so answering it first is worth the two
          minutes.
        </p>
      ) : null}
      {setup.framework_pending ? (
        <p className="mt-2 text-xs">
          We are still writing the criteria for this role. This normally takes
          under a minute; refresh the page shortly.
        </p>
      ) : (
        <Button asChild size="sm" className="mt-3">
          <a href="#ppi-framework">Review and save</a>
        </Button>
      )}
    </div>
  );
}

// ── Framework editor (spec §6.3) ─────────────────────────────────────────────

function CompetencyRow({
  competency,
  frozen,
  onSave,
  onRemove,
}: {
  competency: Competency;
  frozen: boolean;
  onSave: (next: Omit<Competency, "id" | "ordinal">) => Promise<void>;
  onRemove: () => Promise<void>;
}) {
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState(competency.name);
  const [description, setDescription] = React.useState(competency.description ?? "");
  const [level, setLevel] = React.useState<RatingGrade>(competency.required_level);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    setName(competency.name);
    setDescription(competency.description ?? "");
    setLevel(competency.required_level);
  }, [competency]);

  if (!editing) {
    return (
      <div className="flex items-start justify-between gap-3 rounded-md border p-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">{competency.name}</p>
          {competency.description ? (
            <p className="mt-0.5 text-xs">{competency.description}</p>
          ) : null}
          <p className="mt-1 text-xs">
            This role requires: <RatingLabel label={competency.required_level} />
          </p>
        </div>
        {frozen ? null : (
          <div className="flex shrink-0 gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(true)}
              aria-label={`Edit ${competency.name}`}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await onRemove();
                } finally {
                  setBusy(false);
                }
              }}
              aria-label={`Remove ${competency.name}`}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" />
      <Textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="What this measures"
        rows={2}
      />
      <Select value={level} onValueChange={(value) => setLevel(value as RatingGrade)}>
        <SelectTrigger>
          <SelectValue placeholder="This role requires" />
        </SelectTrigger>
        <SelectContent>
          {REQUIREMENT_LEVELS.map((grade) => (
            <SelectItem key={grade} value={grade}>
              {grade}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={busy || !name.trim()}
          onClick={async () => {
            setBusy(true);
            try {
              await onSave({
                category: competency.category,
                name: name.trim(),
                description: description.trim() || null,
                required_level: level,
              });
              setEditing(false);
            } finally {
              setBusy(false);
            }
          }}
        >
          Save
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function AddCompetency({
  category,
  onAdd,
  onBulkAdd,
}: {
  category: Category;
  onAdd: (next: { category: Category; name: string; description: string | null; required_level: RatingGrade }) => Promise<void>;
  onBulkAdd: (next: { category: Category; names: string[]; required_level: RatingGrade }) => Promise<void>;
}) {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [level, setLevel] = React.useState<RatingGrade>("Matching");
  const [busy, setBusy] = React.useState(false);
  const names = Array.from(
    new Set(name.split(/[\n,]+/).map((value) => value.trim()).filter(Boolean))
  );

  if (!open) {
    return (
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
        Add to {CATEGORY_LABEL[category]}
      </Button>
    );
  }
  return (
    <div className="space-y-2 rounded-md border border-dashed p-3">
      <Textarea
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={
          category === "behavioural"
            ? "Paste one behaviour per line"
            : "Paste one skill per line (10+ supported)"
        }
        rows={4}
      />
      <Textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="What this measures"
        rows={2}
      />
      <Select value={level} onValueChange={(value) => setLevel(value as RatingGrade)}>
        <SelectTrigger>
          <SelectValue placeholder="This role requires" />
        </SelectTrigger>
        <SelectContent>
          {REQUIREMENT_LEVELS.map((grade) => (
            <SelectItem key={grade} value={grade}>
              {grade}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={busy || names.length === 0}
          onClick={async () => {
            setBusy(true);
            try {
              if (names.length > 1) {
                await onBulkAdd({ category, names, required_level: level });
              } else {
                await onAdd({
                  category,
                  name: names[0],
                  description: description.trim() || null,
                  required_level: level,
                });
              }
              setName("");
              setDescription("");
              setOpen(false);
            } finally {
              setBusy(false);
            }
          }}
        >
          Add {names.length > 1 ? `${names.length} entries` : ""}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function JobSetupReview({ jobId }: { jobId: string }) {
  const { toast } = useToast();
  const [setup, setSetup] = React.useState<Setup | null>(null);
  const [framework, setFramework] = React.useState<Framework | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  // Drag-and-drop review (spec 5.3). Native HTML5 drag events rather than a
  // drag library: the interaction is a single-column reorder with two drop
  // zones, and a dependency for that is a build risk with nothing to show for
  // it.
  const [dragging, setDragging] = React.useState<string | null>(null);
  const [dropTarget, setDropTarget] = React.useState<Category | null>(null);

  /**
   * The two halves are fetched INDEPENDENTLY.
   *
   * A single Promise.all here meant that one rejection took the other down: a
   * job whose framework has not been generated yet answers 404, which is the
   * NORMAL state for a job created moments ago, and the whole component then
   * had null state and returned null. The entire assessment surface disappeared
   * from the job page, which reads to a customer as "assessments are not
   * available" rather than "not generated yet". Each part now degrades to
   * absent on its own and the card below says so in place.
   */
  const load = React.useCallback(async () => {
    const [setupRes, frameworkRes] = await Promise.allSettled([
      apiGet<Setup>(`${BASE}/${jobId}/setup`),
      apiGet<Framework>(`${BASE}/${jobId}/framework`),
    ]);
    setSetup(setupRes.status === "fulfilled" ? setupRes.value : null);
    setFramework(frameworkRes.status === "fulfilled" ? frameworkRes.value : null);
    // Only a total failure is worth interrupting the recruiter for. One missing
    // half is explained in place by the card it belongs to.
    if (setupRes.status === "rejected" && frameworkRes.status === "rejected") {
      toast({
        title: "Couldn't load the assessment setup",
        description: errorMessage(
          setupRes.reason,
          "Please refresh and try again."
        ),
        variant: "destructive",
      });
    }
    setLoading(false);
  }, [jobId, toast]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const mutate = React.useCallback(
    async (action: () => Promise<unknown>, failureTitle: string) => {
      setBusy(true);
      try {
        await action();
        await load();
        return true;
      } catch (error) {
        toast({
          title: failureTitle,
          description: errorMessage(error, "Please try again."),
          variant: "destructive",
        });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [load, toast]
  );

  /**
   * Apply one drag gesture: reorder inside an aspect, or move an item between
   * Must-have and Nice-to-have (spec 5.3).
   *
   * The WHOLE ordered list for each CHANGED aspect is sent, not a (from, to)
   * pair. A pair has to be replayed against whatever the server currently
   * holds, and two hiring managers dragging at once would interleave into an
   * order neither of them saw; a full list is idempotent and always describes a
   * state a human actually looked at. An aspect the client omits is left
   * untouched, so a gesture inside one list cannot renumber another.
   *
   * `beforeId` is the item the drop landed ON, so the dragged item takes its
   * place. A drop on the empty part of a list has no such anchor and appends,
   * which is what dropping into a gap looks like to the person doing it.
   */
  const handleDrop = React.useCallback(
    async (target: Category, beforeId: string | null) => {
      const moved = dragging;
      setDragging(null);
      setDropTarget(null);
      if (!moved || !framework || moved === beforeId) return;

      const source = framework.competencies.find((row) => row.id === moved);
      if (!source) return;

      const groups = new Map<Category, string[]>();
      for (const category of CATEGORY_ORDER) {
        groups.set(
          category,
          framework.competencies
            .filter((row) => row.category === category && row.id !== moved)
            .map((row) => row.id)
        );
      }
      const destination = groups.get(target) ?? [];
      const at = beforeId ? destination.indexOf(beforeId) : -1;
      if (at >= 0) destination.splice(at, 0, moved);
      else destination.push(moved);

      const changed: Category[] = [target];
      if (source.category !== target) changed.push(source.category);

      await mutate(
        () =>
          apiPost(`${BASE}/${jobId}/framework/reorder`, {
            groups: changed.map((category) => ({
              category,
              competency_ids: groups.get(category) ?? [],
            })),
          }),
        "Couldn't move that item"
      );
    },
    [dragging, framework, jobId, mutate]
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      </div>
    );
  }
  // Nothing came back at all. Rendering null here is what made the one manual
  // step in the pipeline look like a feature the customer had not been given;
  // say what the state is and offer a way to look again.
  if (!setup && !framework) {
    return (
      <Card id="ppi-framework" className="scroll-mt-24">
        <CardHeader>
          <CardTitle>Assessment setup</CardTitle>
          <CardDescription>
            The Tatva Assessment matrix for this job is not available yet. It is
            generated from the job description shortly after a job is created,
            and has to be saved before any candidate can be invited.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="outline"
            onClick={() => {
              setLoading(true);
              void load();
            }}
          >
            Check again
          </Button>
        </CardContent>
      </Card>
    );
  }

  const frozen = Boolean(framework?.approved);

  return (
    <div className="space-y-5">
      {setup ? <SetupStatus setup={setup} /> : null}

      {/* The intake comes FIRST because it is an input to everything below it:
          the matrix is generated from the job description and this together. */}
      <SwotIntakePanel jobId={jobId} />

      {/* The other half of the one setup session (spec 3.2). */}
      <MatchingCategoriesCard jobId={jobId} />

      {/* One question about monitoring, set once per job (proctoring spec 6).
          It sits here rather than on the Create Job form because it is a
          decision about how candidates are assessed, which is what this
          screen is for, and because it can be changed after the job is live
          without reopening the matrix. It gates NOTHING: a job with the
          default answer is ready for candidates like any other. */}
      <MonitoringPolicyCard jobId={jobId} />

      {/* ── The Tatva Assessment matrix ─────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Tatva Assessment matrix</CardTitle>
              <CardDescription>
                Generated from this job&apos;s description and the role intake above. Once
                saved it becomes the fixed evaluation criteria for every candidate who
                applies, which is what makes their reports comparable.
              </CardDescription>
            </div>
            {framework?.approved ? (
              <Badge variant="brand" className="gap-1">
                <Lock className="h-3 w-3" aria-hidden />
                Saved
              </Badge>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {!framework ? (
            // The framework alone is missing (still generating, or its read
            // failed). Explain that rather than showing three empty category
            // headings with "0 of at least 3, more needed", which reads as a
            // broken screen.
            <p className="text-sm">
              The framework for this job is still being prepared. It is written
              from the job description, so it appears here a few moments after
              the job is created. Refresh to check.
            </p>
          ) : (
            <>
            <p className="rounded-md border bg-muted/30 p-3 text-xs">
              {framework.competencies.length} item
              {framework.competencies.length === 1 ? "" : "s"} in this matrix, at
              most {framework.maximum_items} for this grade. Candidates will be
              asked {framework.question_target} question
              {framework.question_target === 1 ? "" : "s"}. There is no minimum:
              keep only what this role genuinely needs, because every item here
              is probed at least once.
              {frozen
                ? null
                : " Drag an item to reorder it, or drop it on the other list to move it between Must-have and Nice-to-have."}
            </p>

            {CATEGORY_ORDER.map((category) => {
              const rows = framework.competencies.filter((row) => row.category === category);
              const empty = rows.length === 0;
              const droppable = !frozen && MOVE_TARGETS.includes(category);
              return (
                <section
                  key={category}
                  className={
                    "space-y-2 rounded-md p-2 transition-colors " +
                    (dropTarget === category ? "bg-muted ring-1 ring-inset" : "")
                  }
                  onDragOver={(event) => {
                    if (!droppable || !dragging) return;
                    // Default is "no drop"; preventing it is what makes this a
                    // valid drop zone at all in the HTML5 drag API.
                    event.preventDefault();
                    setDropTarget(category);
                  }}
                  onDragLeave={() => {
                    if (dropTarget === category) setDropTarget(null);
                  }}
                  onDrop={(event) => {
                    if (!droppable) return;
                    event.preventDefault();
                    void handleDrop(category, null);
                  }}
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h4 className="text-sm font-semibold">{CATEGORY_LABEL[category]}</h4>
                    <span className="text-xs">
                      {rows.length} item{rows.length === 1 ? "" : "s"}
                      {empty ? ", at least one needed" : ""}
                    </span>
                  </div>
                  <p className="text-xs">{CATEGORY_HINT[category]}</p>
                  {rows.map((competency) => (
                    <div
                      key={`drag-${competency.id}`}
                      draggable={!frozen}
                      onDragStart={() => setDragging(competency.id)}
                      onDragEnd={() => {
                        setDragging(null);
                        setDropTarget(null);
                      }}
                      onDragOver={(event) => {
                        if (frozen || !dragging || dragging === competency.id) return;
                        event.preventDefault();
                        event.stopPropagation();
                        setDropTarget(category);
                      }}
                      onDrop={(event) => {
                        if (frozen) return;
                        event.preventDefault();
                        event.stopPropagation();
                        void handleDrop(category, competency.id);
                      }}
                      className={
                        (frozen ? "" : "cursor-grab active:cursor-grabbing ") +
                        (dragging === competency.id ? "opacity-50" : "")
                      }
                    >
                    <CompetencyRow
                      key={competency.id}
                      competency={competency}
                      frozen={frozen}
                      onSave={(next) =>
                        mutate(
                          () =>
                            apiPut(`${BASE}/${jobId}/framework/${competency.id}`, next),
                          "Couldn't save that change"
                        ).then(() => undefined)
                      }
                      onRemove={() =>
                        mutate(
                          () => apiDelete(`${BASE}/${jobId}/framework/${competency.id}`),
                          "Couldn't remove that entry"
                        ).then(() => undefined)
                      }
                    />
                    </div>
                  ))}
                  {frozen ? null : (
                    <AddCompetency
                      category={category}
                      onAdd={(next) =>
                        mutate(
                          () => apiPost(`${BASE}/${jobId}/framework`, next),
                          "Couldn't add that entry"
                        ).then(() => undefined)
                      }
                      onBulkAdd={(next) =>
                        mutate(
                          () => apiPost(`${BASE}/${jobId}/framework/bulk`, next),
                          "Couldn't add those entries"
                        ).then(() => undefined)
                      }
                    />
                  )}
                </section>
              );
            })}

            {framework.blocking_reason ? (
              <p className="rounded-md border border-amber-600 p-3 text-xs">
                {framework.blocking_reason}
              </p>
            ) : null}

            <div className="flex flex-wrap gap-2">
              {framework.approved ? (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void mutate(
                      () => apiPost(`${BASE}/${jobId}/framework/reopen`),
                      "Couldn't reopen the matrix"
                    )
                  }
                >
                  <Unlock className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  Reopen for editing
                </Button>
              ) : (
                <Button
                  disabled={busy || Boolean(framework.blocking_reason)}
                  onClick={async () => {
                    const ok = await mutate(
                      () => apiPost(`${BASE}/${jobId}/framework/finalize`),
                      "Couldn't save the matrix"
                    );
                    if (ok) toast({ title: "Matrix saved" });
                  }}
                >
                  Save matrix
                </Button>
              )}
            </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* The Technical questions card was REMOVED on 2026-08-06 (client
          decision, and the routes behind it went in the same change).

          A company can no longer create, edit, store or assign technical
          questions. They are written per candidate DURING the assessment,
          from the job description, that candidate's resume and the live
          transcript, and each one carries the rubric generated with it.

          Nothing replaces the card. A screen listing questions that do not
          exist yet -- because they are written when they are asked, and
          differ per candidate -- would be a screen that is empty for every
          job forever. What a recruiter can now see instead is what each
          candidate was ACTUALLY asked, on the candidate's own row: see
          `components/assessment-transcript.tsx`. */}
    </div>
  );
}
