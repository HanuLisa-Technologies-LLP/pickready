"use client";

// The single manual step in the pipeline (spec §11).
//
// Two things are generated in parallel when a job is created, and exactly ONE
// of them gates candidates (client decision, 2026-08-04):
//
//   1. The PPI evaluation framework: Primary Skills, Secondary Skills and
//      Behavioural Competencies, generated from the JD (spec §6.2, §6.3).
//      A human MUST save it. It is the fixed criteria every candidate on this
//      job is graded against, so a human confirming it is the product's only
//      guarantee that two reports are comparable.
//   2. The technical question bank, generated from the JD (spec §5). It gates
//      NOTHING. Questions are live the moment they are generated; a weak one
//      costs one item on one report rather than making two reports
//      incomparable, which is why only the framework half survived.
//
// Everything else in the pipeline runs without human intervention. This screen
// therefore has one job: make the outstanding work obvious, so the step does
// not become a silent bottleneck. The status strip at the top says exactly what
// is still blocking candidates, and the backend mails a reminder if it is left
// unapproved past the configured threshold.
//
// THE STATUS STRIP MUST NAME ONLY THE FRAMEWORK. It previously built its
// outstanding list from `questions_approved` as well, so it told every
// recruiter that "the technical questions" were blocking candidates while the
// control that would have cleared them had been deleted in the same change.
// The banner was unclearable by construction and read as the removed feature
// still being present. A blocker the UI names must have a control that
// satisfies it.

import * as React from "react";
import { Check, Loader2, Lock, Pencil, Plus, Trash2, Unlock } from "lucide-react";

import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import { RATING_GRADES, type RatingGrade } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { RatingLabel } from "@/components/rating-label";
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

type Category = "primary_skill" | "secondary_skill" | "behavioural";

const CATEGORY_ORDER: Category[] = ["primary_skill", "secondary_skill", "behavioural"];

const CATEGORY_LABEL: Record<Category, string> = {
  primary_skill: "Primary Skills",
  secondary_skill: "Secondary Skills",
  behavioural: "Behavioural Competencies",
};

const CATEGORY_HINT: Record<Category, string> = {
  primary_skill: "Capabilities the role cannot be performed without.",
  secondary_skill: "Supporting capabilities that strengthen performance without being disqualifying.",
  behavioural: "Observable workplace behaviours the role demands.",
};

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
  minimum_per_category: number;
  blocking_reason: string | null;
}

interface Setup {
  job_id: string;
  status: string;
  grade: string | null;
  // `questions_approved` is still returned by the API and is deliberately NOT
  // declared here. It now just mirrors `framework_approved`, and leaving it off
  // the type is what stops it being wired back into a blocking message by
  // someone reading the payload rather than this file.
  framework_approved: boolean;
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

function SetupStatus({ setup }: { setup: Setup }) {
  if (setup.ready_for_candidates) {
    return (
      <div className="rounded-lg border border-emerald-700 bg-emerald-50 p-4 dark:bg-emerald-950/40">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <Check className="h-4 w-4" aria-hidden />
          Ready for candidates
        </p>
        <p className="mt-1 text-xs">
          The PPI framework is saved. Candidates you invite can now take the
          assessment.
        </p>
      </div>
    );
  }
  // Only the framework is named, because only the framework can be acted on.
  // `setup.questions_approved` is deliberately NOT read here: the backend
  // stopped gating on it and the button that set it is gone, so naming it
  // would state a blocker no control on this page can clear.
  return (
    <div className="rounded-lg border border-amber-600 bg-amber-50 p-4 dark:bg-amber-950/40">
      <p className="text-sm font-semibold">Framework pending review</p>
      <p className="mt-1 text-xs">
        No candidate can be invited to this job until you save the PPI framework
        below. Applications still arrive in the meantime.
      </p>
      {setup.framework_pending ? (
        <p className="mt-2 text-xs">
          We are still writing the criteria for this role. This normally takes
          under a minute; refresh the page shortly.
        </p>
      ) : null}
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
}: {
  category: Category;
  onAdd: (next: { category: Category; name: string; description: string | null; required_level: RatingGrade }) => Promise<void>;
}) {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [level, setLevel] = React.useState<RatingGrade>("Matching");
  const [busy, setBusy] = React.useState(false);

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
      <Input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={category === "behavioural" ? "Behaviour" : "Skill"}
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
          disabled={busy || !name.trim()}
          onClick={async () => {
            setBusy(true);
            try {
              await onAdd({
                category,
                name: name.trim(),
                description: description.trim() || null,
                required_level: level,
              });
              setName("");
              setDescription("");
              setOpen(false);
            } finally {
              setBusy(false);
            }
          }}
        >
          Add
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
      <Card>
        <CardHeader>
          <CardTitle>Assessment setup</CardTitle>
          <CardDescription>
            The PPI framework for this job is not available yet. It is generated
            from the job description shortly after a job is created, and has to
            be saved before any candidate can be invited.
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

      {/* ── PPI framework ─────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>PPI evaluation framework</CardTitle>
              <CardDescription>
                Generated from this job&apos;s description. Once saved it becomes the fixed
                evaluation criteria for every candidate who applies, which is what makes their
                reports comparable.
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
            {CATEGORY_ORDER.map((category) => {
              const rows = framework.competencies.filter((row) => row.category === category);
              const short = rows.length < framework.minimum_per_category;
              return (
                <section key={category} className="space-y-2">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h4 className="text-sm font-semibold">{CATEGORY_LABEL[category]}</h4>
                    <span className="text-xs">
                      {rows.length} of at least {framework.minimum_per_category}
                      {short ? ", more needed" : ""}
                    </span>
                  </div>
                  <p className="text-xs">{CATEGORY_HINT[category]}</p>
                  {rows.map((competency) => (
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
                      "Couldn't reopen the framework"
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
                      "Couldn't save the framework"
                    );
                    if (ok) toast({ title: "Framework saved" });
                  }}
                >
                  Save framework
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
