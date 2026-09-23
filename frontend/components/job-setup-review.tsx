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
// The reporting authority SWOT intake moved to the JD tab (owner ruling,
// 2026-09-19): it renders inside the Job SWOT Analysis panel, beside the JD it
// is about. It still gates NOTHING on its own. It is an INPUT to the matrix,
// so an intake nobody completed already shows up as a matrix nobody approved;
// gating separately would give one problem two error messages.
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
//
// ── THE MATRIX IS A LIST OF SKILLS, SO IT LOOKS LIKE ONE (owner, 2026-09-20) ──
//
// This editor used to render every entry as a horizontal CARD carrying a name,
// a free-text "What this measures" box, a "This role requires:" caption and a
// grade badge, in a row that scrolled sideways. Five skills filled the screen,
// the aspects sat one under another so the three were never visible together,
// and adding one meant a four-field form. What a recruitment team is actually
// doing here is naming skills.
//
// So: an entry is a CHIP -- its name, the grade word beside it, and a remove
// control -- the three aspects sit side by side, and the add control is one
// line with a "Paste a list" escape hatch for the common case of typing out a
// dozen at once.
//
// THE DESCRIPTION INPUT IS GONE, deliberately and in both places (add and
// edit). It was optional, unexplained and never asked for; what a competency
// MEANS is `observable_evidence`, which Sutra derives and nobody hand-types.
// The COLUMN and the generated text survive: an edit sends the stored
// description straight back, because a field disappearing from a form must not
// be a field being erased from the record.

import * as React from "react";
import { Check, Loader2, Lock, Pencil, Plus, Unlock, X } from "lucide-react";

import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import { RATING_GRADES, type RatingGrade } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "./ui/textarea";
import { Badge } from "@/components/ui/badge";
import { RatingLabel } from "@/components/rating-label";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  must_have: "The role cannot be performed without these.",
  nice_to_have: "Helpful, but not disqualifying.",
  behavioural: "Observable workplace behaviours the role demands.",
};

/** What the one-line add control asks for, per aspect. */
const CATEGORY_PLACEHOLDER: Record<Category, string> = {
  must_have: "Skill or capability",
  nice_to_have: "Skill or capability",
  behavioural: "Behaviour",
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

/** One pasted block to a list of names: newline or comma, blanks and repeats out. */
function parseNames(raw: string): string[] {
  return Array.from(
    new Set(
      raw
        .split(/[\n,]+/)
        .map((value) => value.trim())
        .filter(Boolean)
    )
  );
}

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
  swot_analysis_ready?: boolean;
  ready_for_candidates: boolean;
  /**
   * The framework has not been generated yet and the backend has just enqueued
   * one. Distinct from "generated and short of a minimum": 19 of 35 live jobs
   * were in this state with nothing retrying, and the screen rendered an empty
   * list indistinguishable from a finished, empty framework.
   *
   * It is also distinct from "a reviewer deleted everything", which it could
   * not tell apart until 2026-09-20 and therefore reported as generation still
   * running. See `_framework_repair_pending`.
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
      {setup.swot_analysis_ready === false ? (
        <p className="mt-2 text-xs">
          Save the Job SWOT Analysis on the job description tab to supply the
          evaluation matrix with this role&apos;s context.
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

// ── One entry ────────────────────────────────────────────────────────────────

/**
 * A matrix entry as a chip: the name, the grade word, edit and remove.
 *
 * Edit swaps the chip in place rather than opening anything, because the only
 * two things editable here are a short name and a three-option grade. The
 * stored `description` rides along untouched (see the header note).
 */
function CompetencyChip({
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
  const [level, setLevel] = React.useState<RatingGrade>(competency.required_level);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    setName(competency.name);
    setLevel(competency.required_level);
  }, [competency]);

  if (editing) {
    return (
      <div className="flex w-full min-w-[280px] max-w-full flex-wrap items-center gap-2 rounded-md border border-dashed p-2">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label={`Rename ${competency.name}`}
          className="h-8 min-w-0 flex-1 text-sm"
        />
        <Select value={level} onValueChange={(value) => setLevel(value as RatingGrade)}>
          <SelectTrigger className="h-8 w-[168px] text-sm" aria-label="Required level">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {REQUIREMENT_LEVELS.map((grade) => (
              <SelectItem key={grade} value={grade}>
                {grade}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          disabled={busy || !name.trim()}
          onClick={async () => {
            setBusy(true);
            try {
              await onSave({
                category: competency.category,
                name: name.trim(),
                // Sent back exactly as stored. The input is gone; the record
                // is not.
                description: competency.description,
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
    );
  }

  return (
    <span
      className={
        "inline-flex max-w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm " +
        (frozen ? "" : "cursor-grab active:cursor-grabbing")
      }
      title={competency.description ?? undefined}
    >
      {/* Never truncated: a skill name a reviewer cannot read in full is a
          criterion they cannot confirm. The chip wraps instead. */}
      <span className="min-w-0 break-words font-medium">{competency.name}</span>
      <RatingLabel label={competency.required_level} />
      {frozen ? null : (
        <span className="flex shrink-0 items-center">
          <button
            type="button"
            onClick={() => setEditing(true)}
            aria-label={`Edit ${competency.name}`}
            className="rounded p-0.5 hover:bg-muted"
          >
            <Pencil className="h-3.5 w-3.5" aria-hidden />
          </button>
          <button
            type="button"
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
            className="rounded p-0.5 hover:bg-muted"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </span>
      )}
    </span>
  );
}

// ── Adding entries ───────────────────────────────────────────────────────────

/**
 * One line to add one entry, and a modal for adding many.
 *
 * The two are not two implementations: both end at `onAdd(names, level)`, which
 * always posts the bulk route. A single name is the one-element case, which is
 * also what makes re-adding a name idempotent on the server rather than a 500
 * (see `_rows_by_name` in the API).
 */
function AddCompetency({
  category,
  disabled,
  onAdd,
}: {
  category: Category;
  disabled: boolean;
  onAdd: (names: string[], level: RatingGrade) => Promise<boolean>;
}) {
  const [name, setName] = React.useState("");
  const [level, setLevel] = React.useState<RatingGrade>("Matching");
  const [pasting, setPasting] = React.useState(false);
  const [pasted, setPasted] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const submit = React.useCallback(
    async (raw: string, after: () => void) => {
      const names = parseNames(raw);
      if (names.length === 0) return;
      setBusy(true);
      try {
        if (await onAdd(names, level)) after();
      } finally {
        setBusy(false);
      }
    },
    [level, onAdd]
  );

  return (
    <div className="mt-3 rounded-md border border-dashed p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={name}
          disabled={disabled || busy}
          placeholder={CATEGORY_PLACEHOLDER[category]}
          aria-label={`Add to ${CATEGORY_LABEL[category]}`}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            void submit(name, () => setName(""));
          }}
          className="h-9 min-w-[140px] flex-1 text-sm"
        />
        <Select
          value={level}
          onValueChange={(value) => setLevel(value as RatingGrade)}
          disabled={disabled || busy}
        >
          <SelectTrigger
            className="h-9 w-[168px] text-sm"
            aria-label={`Required level for the next ${CATEGORY_LABEL[category]} entry`}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {REQUIREMENT_LEVELS.map((grade) => (
              <SelectItem key={grade} value={grade}>
                {grade}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          disabled={disabled || busy || parseNames(name).length === 0}
          onClick={() => void submit(name, () => setName(""))}
        >
          <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
          Add
        </Button>
      </div>
      <p className="mt-2 text-xs">
        Adding many?{" "}
        <button
          type="button"
          disabled={disabled || busy}
          className="font-semibold underline underline-offset-2"
          onClick={() => setPasting(true)}
        >
          Paste a list
        </button>
      </p>

      <Dialog open={pasting} onOpenChange={setPasting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add to {CATEGORY_LABEL[category]}</DialogTitle>
            <DialogDescription>
              One per line. They all come in at the level selected above, and
              you can change any of them afterwards.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            aria-label={`Paste a list for ${CATEGORY_LABEL[category]}`}
            rows={8}
            placeholder={"Python\nSQL\nSystem design\nREST APIs"}
          />
          <DialogFooter>
            <Button variant="ghost" size="sm" onClick={() => setPasting(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={busy || parseNames(pasted).length === 0}
              onClick={() =>
                void submit(pasted, () => {
                  setPasted("");
                  setPasting(false);
                })
              }
            >
              Add {parseNames(pasted).length || ""}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
   * Add a list of names to one aspect.
   *
   * The server is idempotent per name, so what the reviewer is TOLD has to be
   * worked out here: the names already sitting in that aspect are the ones the
   * request will not change, and saying "added 3" when one of the four was
   * already present would be a count they can see is wrong.
   */
  const addNames = React.useCallback(
    async (category: Category, names: string[], level: RatingGrade) => {
      const present = new Set(
        (framework?.competencies ?? [])
          .filter((row) => row.category === category)
          .map((row) => row.name.toLowerCase())
      );
      const already = names.filter((name) => present.has(name.toLowerCase()));
      const ok = await mutate(
        () =>
          apiPost(`${BASE}/${jobId}/framework/bulk`, {
            category,
            names,
            required_level: level,
          }),
        names.length > 1 ? "Couldn't add those entries" : "Couldn't add that entry"
      );
      if (ok && already.length > 0) {
        toast({
          title: `Added ${names.length - already.length} to ${CATEGORY_LABEL[category]}`,
          description: `${already.join(", ")} ${
            already.length === 1 ? "was" : "were"
          } already there and ${already.length === 1 ? "was" : "were"} left as ${
            already.length === 1 ? "it is" : "they are"
          }.`,
        });
      }
      return ok;
    },
    [framework, jobId, mutate, toast]
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
      <Card id="ppi-framework" className="scroll-mt-24">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Tatva Assessment matrix</CardTitle>
              <CardDescription>
                The capabilities every candidate for this job is assessed on.
                Drafted from the job description and the Job SWOT Analysis, and
                yours to change until you save it.
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
        <CardContent className="space-y-4">
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
                <span className="font-semibold">
                  {framework.competencies.length} of at most{" "}
                  {framework.maximum_items} entries.
                </span>{" "}
                Every one of them is asked about, so this job&apos;s candidates
                will answer {framework.question_target} question
                {framework.question_target === 1 ? "" : "s"}. Keep only what the
                role genuinely needs.
                {frozen
                  ? null
                  : " Drag an entry onto the other list to move it between Must-have and Nice-to-have."}
              </p>

              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {CATEGORY_ORDER.map((category) => {
                  const rows = framework.competencies.filter(
                    (row) => row.category === category
                  );
                  const droppable = !frozen && MOVE_TARGETS.includes(category);
                  return (
                    <section
                      key={category}
                      className={
                        "flex flex-col rounded-lg border p-3 transition-colors " +
                        (dropTarget === category ? "bg-muted ring-1 ring-inset" : "")
                      }
                      onDragOver={(event) => {
                        if (!droppable || !dragging) return;
                        // Default is "no drop"; preventing it is what makes
                        // this a valid drop zone at all in the HTML5 drag API.
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
                        <h4 className="text-sm font-semibold">
                          {CATEGORY_LABEL[category]}
                        </h4>
                        <span className="text-xs">
                          {rows.length} item{rows.length === 1 ? "" : "s"}
                          {rows.length === 0 ? ", at least one needed" : ""}
                        </span>
                      </div>
                      <p className="mt-0.5 text-xs">{CATEGORY_HINT[category]}</p>

                      {rows.length > 0 ? (
                        <div className="mt-3 flex flex-wrap gap-2">
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
                                if (frozen || !dragging || dragging === competency.id)
                                  return;
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
                                "max-w-full " +
                                (dragging === competency.id ? "opacity-50" : "")
                              }
                            >
                              <CompetencyChip
                                competency={competency}
                                frozen={frozen}
                                onSave={(next) =>
                                  mutate(
                                    () =>
                                      apiPut(
                                        `${BASE}/${jobId}/framework/${competency.id}`,
                                        next
                                      ),
                                    "Couldn't save that change"
                                  ).then(() => undefined)
                                }
                                onRemove={() =>
                                  mutate(
                                    () =>
                                      apiDelete(
                                        `${BASE}/${jobId}/framework/${competency.id}`
                                      ),
                                    "Couldn't remove that entry"
                                  ).then(() => undefined)
                                }
                              />
                            </div>
                          ))}
                        </div>
                      ) : null}

                      {frozen ? null : (
                        <div className="mt-auto">
                          <AddCompetency
                            category={category}
                            disabled={busy}
                            onAdd={(names, level) =>
                              addNames(category, names, level)
                            }
                          />
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>

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
