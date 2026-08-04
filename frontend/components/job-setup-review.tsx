"use client";

// The single manual step in the pipeline (spec §11).
//
// Two things are generated in parallel when a job is created, and BOTH must be
// finalised by a human before any candidate can be invited:
//
//   1. The technical question bank, generated from the JD (spec §5).
//   2. The PPI evaluation framework: Primary Skills, Secondary Skills and
//      Behavioural Competencies, generated from the JD (spec §6.2, §6.3).
//
// Everything else in the pipeline runs without human intervention. This screen
// therefore has one job: make the outstanding work obvious, so the step does
// not become a silent bottleneck. The status strip at the top says exactly what
// is still blocking candidates, and the backend mails a reminder if it is left
// unapproved past the configured threshold.

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

interface TechnicalQuestion {
  id: string;
  ordinal: number;
  skill: string;
  prompt: string;
  rubric: Record<string, string>;
  is_active: boolean;
}

interface QuestionBank {
  job_id: string;
  status: string;
  grade: string | null;
  questions: TechnicalQuestion[];
  approved: boolean;
}

interface Setup {
  job_id: string;
  status: string;
  grade: string | null;
  questions_approved: boolean;
  framework_approved: boolean;
  ready_for_candidates: boolean;
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
          Both the technical questions and the PPI framework are finalised. Candidates you
          invite can now take the assessment.
        </p>
      </div>
    );
  }
  const outstanding = [
    setup.questions_approved ? null : "the technical questions",
    setup.framework_approved ? null : "the PPI framework",
  ].filter(Boolean);
  return (
    <div className="rounded-lg border border-amber-600 bg-amber-50 p-4 dark:bg-amber-950/40">
      <p className="text-sm font-semibold">Questions pending review</p>
      <p className="mt-1 text-xs">
        No candidate can be invited to this job until you finalise {outstanding.join(" and ")}.
        Applications still arrive in the meantime.
      </p>
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
  const [bank, setBank] = React.useState<QuestionBank | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState(false);

  /**
   * The three halves are fetched INDEPENDENTLY.
   *
   * A single Promise.all here meant that one rejection took all three down: a
   * job whose framework or question bank has not been generated yet answers
   * 404, which is the NORMAL state for a job created moments ago, and the whole
   * component then had null state and returned null. The entire assessment
   * surface disappeared from the job page, which reads to a customer as
   * "assessments are not available" rather than "not generated yet". Each part
   * now degrades to absent on its own and the cards below say so in place.
   */
  const load = React.useCallback(async () => {
    const [setupRes, frameworkRes, bankRes] = await Promise.allSettled([
      apiGet<Setup>(`${BASE}/${jobId}/setup`),
      apiGet<Framework>(`${BASE}/${jobId}/framework`),
      apiGet<QuestionBank>(`${BASE}/${jobId}/questions`),
    ]);
    setSetup(setupRes.status === "fulfilled" ? setupRes.value : null);
    setFramework(frameworkRes.status === "fulfilled" ? frameworkRes.value : null);
    setBank(bankRes.status === "fulfilled" ? bankRes.value : null);
    // Only a total failure is worth interrupting the recruiter for. One missing
    // half is explained in place by the card it belongs to.
    if (
      setupRes.status === "rejected" &&
      frameworkRes.status === "rejected" &&
      bankRes.status === "rejected"
    ) {
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
  if (!setup && !framework && !bank) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Assessment setup</CardTitle>
          <CardDescription>
            The PPI framework and the technical questions for this job are not
            available yet. Both are generated from the job description shortly
            after a job is created, and this step has to be finalised before any
            candidate can be invited.
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
  const activeQuestions = (bank?.questions ?? []).filter(
    (question) => question.is_active
  );

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

      {/* ── Technical question bank ───────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Technical questions</CardTitle>
              <CardDescription>
                Generated once for this job from its required skills. Every candidate answers
                the same set, which is what keeps their technical scores comparable. Each
                question is scored against its own rubric, never open-ended judgement.
              </CardDescription>
            </div>
            {bank?.approved ? (
              <Badge variant="brand" className="gap-1">
                <Check className="h-3 w-3" aria-hidden />
                Finalised
              </Badge>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {activeQuestions.length === 0 ? (
            <p className="text-sm">
              {bank
                ? "These questions are still being prepared. They are written from the job's required skills, and appear here a few moments after the job is created. Refresh to check."
                : "The question bank for this job is not available yet. It is written from the job's required skills shortly after the job is created. Refresh to check."}
            </p>
          ) : (
            <ol className="space-y-2">
              {activeQuestions.map((question) => (
                <QuestionRow
                  key={question.id}
                  jobId={jobId}
                  question={question}
                  busy={busy}
                  mutate={mutate}
                />
              ))}
            </ol>
          )}
          {/* The "Finalise questions" button was REMOVED on 2026-08-04 (client
              decision). Technical questions are usable the moment they are
              generated, so there is nothing here for a recruiter to unblock.
              Editing and removing individual questions above still works and
              still takes effect immediately.

              The PPI framework's Save step is a different control and is
              deliberately still present: the framework is the fixed criteria
              every candidate on this job is graded against, and a human
              confirming it is what makes two reports comparable. */}
          {activeQuestions.length > 0 ? (
            <p className="text-sm">
              These questions are live. Any edit you make here applies to
              candidates who have not answered them yet.
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

function QuestionRow({
  jobId,
  question,
  busy,
  mutate,
}: {
  jobId: string;
  question: TechnicalQuestion;
  busy: boolean;
  mutate: (action: () => Promise<unknown>, failureTitle: string) => Promise<boolean>;
}) {
  const [editing, setEditing] = React.useState(false);
  const [skill, setSkill] = React.useState(question.skill);
  const [prompt, setPrompt] = React.useState(question.prompt);

  React.useEffect(() => {
    setSkill(question.skill);
    setPrompt(question.prompt);
  }, [question]);

  return (
    <li className="rounded-md border p-3">
      {editing ? (
        <div className="space-y-2">
          <Input value={skill} onChange={(e) => setSkill(e.target.value)} placeholder="Skill" />
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder="Question"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={busy || !skill.trim() || prompt.trim().length < 10}
              onClick={async () => {
                const ok = await mutate(
                  () =>
                    apiPut(`${BASE}/${jobId}/questions/${question.id}`, {
                      skill: skill.trim(),
                      prompt: prompt.trim(),
                      // The rubric travels unchanged: it is what makes the
                      // score defensible when a client asks why a candidate was
                      // rated a certain way, and it is not editable here.
                      rubric: question.rubric,
                    }),
                  "Couldn't save that question"
                );
                if (ok) setEditing(false);
              }}
            >
              Save
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide">{question.skill}</p>
            <p className="mt-1 text-sm">{question.prompt}</p>
          </div>
          <div className="flex shrink-0 gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(true)}
              aria-label={`Edit question ${question.ordinal}`}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() =>
                void mutate(
                  () => apiDelete(`${BASE}/${jobId}/questions/${question.id}`),
                  "Couldn't remove that question"
                )
              }
              aria-label={`Remove question ${question.ordinal}`}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
