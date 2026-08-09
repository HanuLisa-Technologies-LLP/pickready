"use client";

// AI Dashboard, Customer Portal (2026-08-09).
//
// The Dashboard beside this one answers "where are my candidates in the
// pipeline". This one answers "what has the AI actually done for us, and is any
// of it stuck". They are separate pages because they are separate questions and
// merging them would bury the second under the first.
//
// TWO THINGS TO UNDERSTAND BEFORE EDITING:
//
// 1. EVERY FIGURE HERE IS A COUNT OF THINGS: jobs, candidates, assessments,
//    reports. That is what the Dashboard already reports and is outside the
//    no-numbers rule, which covers a score, percentage, rank or band for an
//    assessment or a match. The grade breakdown is keyed by the four WORD
//    labels the server sends and carries no percentage, no meter and no bar
//    scaled to a score. Do not add one.
//
// 2. "NEEDS GENERATING" IS THE POINT OF THE PAGE, not a footnote. A job whose
//    PPI framework failed to generate is stuck: nobody on it can be assessed,
//    and until 2026-08-06 nothing anywhere said so, because every health check
//    asked a timestamp instead of the table. The server measures it against the
//    competency rows; this panel is where a customer sees it.

import * as React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  MessageSquare,
  Send,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

import { ApiError, apiGet } from "@/lib/api";
import type { AIDashboard } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { ErrorState, LoadingCards, Section } from "@/components/page-primitives";
import { Card, CardContent } from "@/components/ui/card";
import { ExportXlsxButton } from "@/components/export-xlsx-button";

function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = "default",
}: {
  icon: LucideIcon;
  label: string;
  value: number;
  hint?: string;
  tone?: "default" | "warning";
}) {
  return (
    <Card className="shadow-card transition-shadow duration-150 hover:shadow-card-hover">
      <CardContent className="flex items-start gap-4 p-5">
        <span
          className={
            tone === "warning"
              ? "grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-destructive text-destructive"
              : "grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand-100 text-accent-foreground"
          }
        >
          <Icon className="h-5 w-5" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-[0.08em] opacity-80">
            {label}
          </p>
          <p className="text-2xl font-bold [font-variant-numeric:tabular-nums]">
            {value}
          </p>
          {hint ? <p className="mt-1 text-xs">{hint}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function AIDashboardView() {
  const [data, setData] = React.useState<AIDashboard | null>(null);
  const [loading, setLoading] = React.useState(true);
  // A 403 here is "not yours to see", not a fault, and it does not improve on a
  // reload. Collapsing it into the generic error state is what made the
  // Dashboard read as broken to a new Company Admin.
  const [forbidden, setForbidden] = React.useState(false);

  React.useEffect(() => {
    apiGet<AIDashboard>("/dashboard/ai-insights")
      .then(setData)
      .catch((error: unknown) => {
        setData(null);
        if (error instanceof ApiError && error.status === 403) setForbidden(true);
      })
      .finally(() => setLoading(false));
  }, []);

  const stuck = data?.framework.pending_generation ?? 0;
  const assessedCandidates = (data?.grades ?? []).reduce(
    (total, row) => total + row.candidates,
    0
  );

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="AI Dashboard"
        description="What the AI has done across your jobs, and anything waiting on you."
        actions={
          data ? (
            <ExportXlsxButton
              fileName="pickready-ai-dashboard"
              rows={[
                { measure: "Jobs with an AI framework", count: data.jobs_with_ai_framework },
                { measure: "Jobs ready for candidates", count: data.framework.ready_for_candidates },
                { measure: "Jobs awaiting your approval", count: data.framework.awaiting_approval },
                { measure: "Jobs needing the framework generated", count: data.framework.pending_generation },
                { measure: "Assessments invited", count: data.assessments.invited },
                { measure: "Assessments started", count: data.assessments.started },
                { measure: "Assessments completed", count: data.assessments.completed },
                { measure: "Reports ready", count: data.assessments.reports_ready },
                ...data.grades.map((row) => ({
                  measure: `Candidates graded ${row.grade}`,
                  count: row.candidates,
                })),
                { measure: "Reports scored offline", count: data.reports_on_fallback },
              ]}
            />
          ) : null
        }
      />

      {loading ? (
        <LoadingCards count={6} className="lg:grid-cols-3" label="Loading the AI dashboard" />
      ) : forbidden ? (
        <ErrorState
          title="The AI dashboard is not part of your access"
          description="Ask your Company Admin to grant you dashboard visibility."
        />
      ) : !data ? (
        <ErrorState
          title="AI dashboard unavailable"
          description="These figures could not be loaded. Reload the page to try again."
        />
      ) : (
        <div className="space-y-8">
          {/* The one panel that can require action. It leads on purpose. */}
          <Section
            title="Job setup"
            description="A job can only assess candidates once its evaluation framework exists and has been approved."
          >
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <MetricCard
                icon={CheckCircle2}
                label="Ready for candidates"
                value={data.framework.ready_for_candidates}
              />
              <MetricCard
                icon={ClipboardCheck}
                label="Awaiting your approval"
                value={data.framework.awaiting_approval}
                hint="Approve the framework and these jobs can invite candidates."
              />
              <MetricCard
                icon={AlertTriangle}
                tone={stuck > 0 ? "warning" : "default"}
                label="Needs generating"
                value={stuck}
                hint={
                  stuck > 0
                    ? "These jobs have no framework yet. PickReady retries them automatically; tell us if one stays here."
                    : "Every job has a framework."
                }
              />
            </div>
          </Section>

          <Section
            title="Assessments"
            description="Candidates you invited, and how far each invitation got."
          >
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard icon={Send} label="Invited" value={data.assessments.invited} />
              <MetricCard
                icon={MessageSquare}
                label="Started"
                value={data.assessments.started}
              />
              <MetricCard
                icon={CheckCircle2}
                label="Completed"
                value={data.assessments.completed}
              />
              <MetricCard
                icon={FileText}
                label="Reports ready"
                value={data.assessments.reports_ready}
              />
            </div>
          </Section>

          <Section
            title="How candidates graded"
            description="Every assessed candidate's overall grade across all of your jobs."
          >
            {assessedCandidates === 0 ? (
              <p className="text-sm">
                No candidate has been assessed yet. Grades appear here as reports
                are produced.
              </p>
            ) : (
              <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {data.grades.map((row) => (
                  <li key={row.grade}>
                    <Card>
                      <CardContent className="p-5">
                        <p className="text-xs font-medium uppercase tracking-[0.08em] opacity-80">
                          {row.grade}
                        </p>
                        <p className="text-2xl font-bold [font-variant-numeric:tabular-nums]">
                          {row.candidates}
                        </p>
                        <p className="mt-1 text-xs">
                          {row.candidates === 1 ? "candidate" : "candidates"}
                        </p>
                      </CardContent>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section
            title="How the reports were produced"
            description="PickReady falls back to an offline scoring method when its AI providers are unreachable, so a report is never simply lost."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <MetricCard
                icon={Sparkles}
                label="Reports produced"
                value={data.total_reports}
              />
              <MetricCard
                icon={AlertTriangle}
                tone={data.reports_on_fallback > 0 ? "warning" : "default"}
                label="Scored offline"
                value={data.reports_on_fallback}
                hint={
                  data.reports_on_fallback > 0
                    ? "Ask us to rerun these if you want them scored again."
                    : "Every report was scored normally."
                }
              />
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}
