"use client";

// Leadership Intelligence (owner spec 2026-09-29, sections 16, 17 and 28),
// which replaced Drishti.
//
// ONE SIMPLE FORM PER LEADER. A CEO or MD writes company-wide hiring
// requirements and one expectation per department; a Functional Head writes
// their own department's requirements and the ideal new joiner, for the
// department the SERVER says is theirs (it is never a field here, and the
// save route refuses one). Every Save is a new version, attributed and dated.
//
// THE AI DRAFT IS NEVER SAVED BY ITSELF (rule 37.11). "Generate draft with
// AI" asks the server for a draft, fills the fields, and marks them as an AI
// draft that is NOT saved until the leader presses Save. The sources the
// draft read are listed, from the server's own words.
//
// Nothing here compiles, filters or judges a line: the server does, and after
// a Save the lines it did not use come back with the reason, verbatim.

import * as React from "react";
import { Check, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/app-shell";
import { ReadOnlyNotice } from "@/components/permission-notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import { CAP } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";

export interface DepartmentRef {
  id: string;
  name: string;
}

export interface LeadershipProfile {
  id: string;
  author_role: string;
  author_label: string;
  department_id: string | null;
  version: number;
  saved_by: string | null;
  saved_at: string;
  company_requirements: string;
  department_requirements: string;
  ideal_employee_expectations: string;
  department_expectations: Record<string, string>;
  lines_not_used: { text: string; reason: string }[];
  used_ai_draft: boolean;
}

export interface LeadershipMe {
  can_author: boolean;
  refusal: string | null;
  author_role: string | null;
  author_label: string | null;
  department: DepartmentRef | null;
  departments: DepartmentRef[];
  profile: LeadershipProfile | null;
}

export interface LeadershipDraft {
  status: "pending" | "drafted" | "empty" | "failed";
  fields: {
    company_requirements?: string;
    department_requirements?: string;
    ideal_employee_expectations?: string;
    department_expectations?: Record<string, string>;
  };
  sources: { key: string; label: string }[];
  message: string | null;
  generated_by_ai: boolean;
}

export interface LeadershipCompany {
  ceo: LeadershipProfile | null;
  md: LeadershipProfile | null;
  functional_heads: { department: DepartmentRef; profile: LeadershipProfile }[];
  departments: DepartmentRef[];
}

/** Why a line was not used, in words. Keyed by the server's reason word. */
export const REASON_WORDS: Record<string, string> = {
  protected_attribute:
    "It names a personal characteristic, which can never be a hiring criterion.",
  culture_fit: "It names culture fit, which an assessment cannot judge.",
  not_observable:
    "It describes a quality rather than something a person could be seen doing.",
  too_long: "It is longer than one line may be. Split it into shorter sentences.",
  over_limit: "It is past the number of lines one section may hold.",
};

/** How long the screen waits for a draft before saying it could not be read. */
const DRAFT_POLL_MS = 2000;
const DRAFT_POLL_LIMIT = 45;

interface Fields {
  company_requirements: string;
  department_requirements: string;
  ideal_employee_expectations: string;
  department_expectations: Record<string, string>;
}

const EMPTY: Fields = {
  company_requirements: "",
  department_requirements: "",
  ideal_employee_expectations: "",
  department_expectations: {},
};

function fieldsFrom(profile: LeadershipProfile | null): Fields {
  if (!profile) return EMPTY;
  return {
    company_requirements: profile.company_requirements,
    department_requirements: profile.department_requirements,
    ideal_employee_expectations: profile.ideal_employee_expectations,
    department_expectations: { ...profile.department_expectations },
  };
}

function savedLine(profile: LeadershipProfile): string {
  const when = new Date(profile.saved_at).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  return `Version ${profile.version}, saved by ${profile.saved_by ?? "a former team member"} on ${when}.`;
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function LeadershipEditor({ me, onSaved }: { me: LeadershipMe; onSaved: (next: LeadershipMe) => void }) {
  const { toast } = useToast();
  const [fields, setFields] = React.useState<Fields>(() => fieldsFrom(me.profile));
  const [draft, setDraft] = React.useState<LeadershipDraft | null>(null);
  const [drafting, setDrafting] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const head = me.author_role === "functional_head";
  const departmentName = me.department?.name ?? "your department";

  const generate = async () => {
    setDrafting(true);
    setDraft(null);
    try {
      const { task_id } = await apiPost<{ task_id: string }>("/leadership/me/draft");
      for (let attempt = 0; attempt < DRAFT_POLL_LIMIT; attempt += 1) {
        await wait(DRAFT_POLL_MS);
        const result = await apiGet<LeadershipDraft>(`/leadership/me/draft/${task_id}`);
        if (result.status === "pending") continue;
        setDraft(result);
        if (result.status === "drafted") {
          setFields((current) => ({
            company_requirements: result.fields.company_requirements ?? current.company_requirements,
            department_requirements:
              result.fields.department_requirements ?? current.department_requirements,
            ideal_employee_expectations:
              result.fields.ideal_employee_expectations ?? current.ideal_employee_expectations,
            department_expectations: {
              ...current.department_expectations,
              ...(result.fields.department_expectations ?? {}),
            },
          }));
        }
        return;
      }
      setDraft({
        status: "failed",
        fields: {},
        sources: [],
        message: "The draft could not be read in time. Write it yourself, or try again.",
        generated_by_ai: false,
      });
    } catch (error) {
      toast({
        title: "The draft could not be started",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setDrafting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const body = head
        ? {
            department_requirements: fields.department_requirements,
            ideal_employee_expectations: fields.ideal_employee_expectations,
          }
        : {
            company_requirements: fields.company_requirements,
            department_expectations: fields.department_expectations,
          };
      const next = await apiPut<LeadershipMe>("/leadership/me", {
        ...body,
        ai_draft_sources:
          draft?.status === "drafted" ? draft.sources.map((source) => source.key) : null,
      });
      setDraft(null);
      setFields(fieldsFrom(next.profile));
      onSaved(next);
      toast({ title: "Leadership Intelligence saved" });
    } catch (error) {
      toast({
        title: "Not saved",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {head ? `Leadership Intelligence, ${departmentName}` : "Leadership Intelligence"}
        </CardTitle>
        <CardDescription>
          {head
            ? `What ${departmentName} needs from its people. Jobs in ${departmentName} are drafted and assessed with it once your hiring team saves their skills.`
            : "What the company needs from the people it hires, across the company and for each department."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="outline" onClick={generate} disabled={drafting || saving}>
            <Sparkles className="mr-2 h-4 w-4" aria-hidden="true" />
            {drafting ? "Drafting" : "Generate draft with AI"}
          </Button>
          {draft?.status === "drafted" ? (
            <Badge variant="outline" data-testid="ai-draft-badge">
              AI draft, not saved
            </Badge>
          ) : null}
        </div>

        {draft && draft.status !== "drafted" && draft.message ? (
          <p role="status" className="text-sm">
            {draft.message}
          </p>
        ) : null}

        {draft && draft.sources.length > 0 ? (
          <div className="rounded-lg border p-3 text-sm" data-testid="draft-sources">
            <p className="font-medium">Draft prepared using:</p>
            <ul className="mt-1 space-y-1">
              {draft.sources.map((source) => (
                <li key={source.key} className="flex items-center gap-2">
                  <Check className="h-4 w-4 text-teal-700" aria-hidden="true" />
                  {source.label}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {head ? (
          <>
            <Field
              id="department-requirements"
              label="Department requirements"
              hint={`What does ${departmentName} need from its people and future hires?`}
              value={fields.department_requirements}
              onChange={(value) => setFields((f) => ({ ...f, department_requirements: value }))}
            />
            <Field
              id="ideal-employee"
              label={`What should a person joining ${departmentName} be able to do?`}
              hint="What they should be able to do, demonstrate or achieve."
              value={fields.ideal_employee_expectations}
              onChange={(value) => setFields((f) => ({ ...f, ideal_employee_expectations: value }))}
            />
          </>
        ) : (
          <>
            <Field
              id="company-requirements"
              label="Company-wide hiring requirements"
              hint="What kind of people should this company hire, and what standards matter across it?"
              value={fields.company_requirements}
              onChange={(value) => setFields((f) => ({ ...f, company_requirements: value }))}
            />
            <div className="space-y-4">
              <p className="text-sm font-semibold">Department expectations</p>
              {me.departments.length === 0 ? (
                <p className="text-sm">
                  Your company has no departments yet. They appear here once a job or a
                  Functional Head names one.
                </p>
              ) : (
                me.departments.map((department) => (
                  <Field
                    key={department.id}
                    id={`department-${department.id}`}
                    label={department.name}
                    value={fields.department_expectations[department.id] ?? ""}
                    onChange={(value) =>
                      setFields((f) => ({
                        ...f,
                        department_expectations: { ...f.department_expectations, [department.id]: value },
                      }))
                    }
                  />
                ))
              )}
            </div>
          </>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" onClick={save} disabled={saving || drafting}>
            {saving ? "Saving" : "Save"}
          </Button>
          {me.profile ? <p className="text-sm">{savedLine(me.profile)}</p> : null}
        </div>

        {me.profile && me.profile.lines_not_used.length > 0 ? (
          <div className="rounded-lg border p-3 text-sm" data-testid="lines-not-used">
            <p className="font-medium">Lines not used in hiring</p>
            <p className="mt-1">
              These were saved with your version but are kept out of job drafting and
              assessment. Rewrite each as something a person could be seen doing.
            </p>
            <ul className="mt-2 space-y-2">
              {me.profile.lines_not_used.map((line, index) => (
                <li key={`${index}-${line.text}`}>
                  <span className="font-medium">&ldquo;{line.text}&rdquo;</span>{" "}
                  {REASON_WORDS[line.reason] ?? line.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Field({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      {hint ? <p className="text-sm">{hint}</p> : null}
      <Textarea id={id} rows={5} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function ProfileSummary({ title, profile }: { title: string; profile: LeadershipProfile | null }) {
  if (!profile) {
    return (
      <div className="space-y-1">
        <p className="font-semibold">{title}</p>
        <p className="text-sm">Nothing saved yet.</p>
      </div>
    );
  }
  const texts = [
    profile.company_requirements,
    profile.department_requirements,
    profile.ideal_employee_expectations,
  ].filter(Boolean);
  return (
    <div className="space-y-1">
      <p className="font-semibold">{title}</p>
      {texts.map((text, index) => (
        <p key={index} className="whitespace-pre-line text-sm">
          {text}
        </p>
      ))}
      <p className="text-sm">{savedLine(profile)}</p>
    </div>
  );
}

export function LeadershipOverview({ company, canEdit }: { company: LeadershipCompany; canEdit: boolean }) {
  const names = new Map(company.departments.map((department) => [department.id, department.name]));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Across the company</CardTitle>
        <CardDescription>
          Every leader&apos;s latest saved version. Each leader edits only their own.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <ReadOnlyNotice
          canEdit={canEdit}
          resource="Leadership Intelligence"
          message="Leadership Intelligence is written by your CEO, your MD and each department's Functional Head. You can read what they saved."
        />
        <ProfileSummary title="CEO" profile={company.ceo} />
        <ProfileSummary title="MD" profile={company.md} />
        {[company.ceo, company.md].map((profile) =>
          profile && Object.keys(profile.department_expectations).length > 0 ? (
            <div key={profile.id} className="space-y-1">
              <p className="font-semibold">{profile.author_label}, by department</p>
              {Object.entries(profile.department_expectations).map(([id, text]) => (
                <p key={id} className="text-sm">
                  <span className="font-medium">{names.get(id) ?? "A retired department"}:</span> {text}
                </p>
              ))}
            </div>
          ) : null
        )}
        {company.functional_heads.map((entry) => (
          <ProfileSummary
            key={entry.department.id}
            title={`Functional Head, ${entry.department.name}`}
            profile={entry.profile}
          />
        ))}
      </CardContent>
    </Card>
  );
}

export function LeadershipIntelligencePage() {
  const { can } = usePermissions();
  const canAuthor = can(CAP.authorLeadershipIntelligence);
  const canView = can(CAP.viewLeadershipIntelligence);
  const [me, setMe] = React.useState<LeadershipMe | null>(null);
  const [company, setCompany] = React.useState<LeadershipCompany | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const [mine, overview] = await Promise.all([
        canAuthor ? apiGet<LeadershipMe>("/leadership/me") : Promise.resolve(null),
        canView ? apiGet<LeadershipCompany>("/leadership/company") : Promise.resolve(null),
      ]);
      setMe(mine);
      setCompany(overview);
    } catch (failure) {
      // A failed read is never rendered as an empty answer.
      setError(failure instanceof Error ? failure.message : "Leadership Intelligence could not be loaded.");
    }
  }, [canAuthor, canView]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const refreshOverview = React.useCallback(async () => {
    if (!canView) return;
    setCompany(await apiGet<LeadershipCompany>("/leadership/company"));
  }, [canView]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Leadership Intelligence"
        description="What your leaders want from the people you hire. Saved versions shape how jobs are drafted and how candidates are assessed, once each job's skills are saved."
      />
      {error ? (
        <p role="alert" className="text-sm">
          {error}
        </p>
      ) : null}
      {me && me.can_author ? (
        <LeadershipEditor
          me={me}
          onSaved={(next) => {
            setMe(next);
            void refreshOverview().catch(() => undefined);
          }}
        />
      ) : null}
      {me && !me.can_author && me.refusal ? <p className="text-sm">{me.refusal}</p> : null}
      {company ? <LeadershipOverview company={company} canEdit={canAuthor} /> : null}
    </div>
  );
}
