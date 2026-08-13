"use client";

import {
  Legend,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from "recharts";

import { RatingLabel } from "@/components/rating-label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RATING_GRADES, type RatingGrade } from "@/lib/types";

export type { RatingGrade };

export interface ReportDimension {
  name: string;
  description?: string | null;
  /** One of the four grades. Never a number, a percentage, or a letter. */
  grade: RatingGrade;
  /** What the job requires of this item. Null on AI Score and technical rows. */
  required_level?: RatingGrade | null;
  remark: string;
}

/**
 * One spoke of one radar chart, built server-side (spec §10.4).
 *
 * Two shapes are plotted on the same axes: what the job requires and what the
 * candidate demonstrated. `*_index` (1 innermost .. 4 outermost) is a RENDERING
 * COORDINATE, not a disclosed score, because a radar cannot be drawn without a radius,
 * and the four grades are the radial axis. It is never displayed: no axis
 * ticks, no data labels, no tooltips.
 */
export interface RadarAxis {
  axis: string;
  requirement_band: RatingGrade;
  requirement_index: number;
  candidate_band: RatingGrade;
  candidate_index: number;
}

export interface GapProbeItem {
  name: string;
  grade: RatingGrade;
  /** The SAME remark the item carries in its own section. Reused, never
   *  rewritten: one report states one assessment of an item. */
  remark?: string | null;
  probes: string[];
}

export interface GapGroup {
  category: string;
  label: string;
  items: GapProbeItem[];
  /** Said in words when the group is empty, rather than left as blank space. */
  no_gaps_statement?: string | null;
  /** Present on the Must-have group when a Not Matching item has capped the
   *  Overall Grade, so the reader is told rather than left to cross-reference. */
  cap_statement?: string | null;
}

export interface GapAnalysis {
  focus_summary: string;
  must_have_cap_applied: boolean;
  groups: GapGroup[];
}

export interface RadarChartSpec {
  key: string;
  title: string;
  axes: RadarAxis[];
}

export interface FunctionalReport {
  id: string;
  job_candidate_link_id: string;
  grade: string;
  /** The pre-assessment resume snapshot: the job's own matching categories. */
  ai_score: ReportDimension[];
  overall_grade: RatingGrade;
  overall_summary: string;
  must_have: ReportDimension[];
  nice_to_have: ReportDimension[];
  behavioural: ReportDimension[];
  /** LEGACY, and empty on every report written from Draft v4 onward: technical
   *  depth is assessed inside Must-have. Non-empty only on a report written
   *  against the standalone technical bank that no longer exists. */
  technical?: ReportDimension[];
  validation: ValidationBlock;
  /** Gap Analysis & Action Plan (spec 9.6). */
  gap_analysis?: GapAnalysis;
  /** RETIRED, replaced by `gap_analysis`. Non-empty only on a report written
   *  before Draft v4, which still renders what it was actually written with. */
  suggested_interview_questions?: string[];
  radar_charts?: RadarChartSpec[];
  radar_bands?: string[];
  radar_series?: string[];
  synthesized_at: string;
  immutable?: boolean;
}

interface ValidationField {
  key: string;
  label: string;
  value: string | null;
}

interface ValidationBlock {
  captured?: boolean;
  fields?: ValidationField[];
  role_interest?: string | null;
  [key: string]: unknown;
}

/** Outermost (best) to innermost, matching `services/rating.GRADES`. */
const BAND_FILL: Record<RatingGrade, string> = {
  "Highly Matching": "#065f46",
  Matching: "#15803d",
  "Moderately Matching": "#d97706",
  "Not Matching": "#b91c1c",
};

const REQUIREMENT_COLOR = "#64748b";
const CANDIDATE_COLOR = "#5028E0";

function DimensionSection({
  title,
  dimensions,
  chart,
  series,
}: {
  title: string;
  dimensions: ReportDimension[];
  chart?: RadarChartSpec;
  series: string[];
}) {
  if (dimensions.length === 0) return null;
  return (
    <section aria-label={title}>
      <h3 className="mb-3 text-lg font-semibold">{title}</h3>
      {chart ? <DualRadar chart={chart} series={series} /> : null}
      <div className="grid gap-3 md:grid-cols-2">
        {dimensions.map((dimension) => (
          <Card key={`${title}-${dimension.name}`}>
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-3">
                <CardTitle className="text-base">{dimension.name}</CardTitle>
                <RatingLabel label={dimension.grade} />
              </div>
              {dimension.description ? (
                <p className="text-xs">{dimension.description}</p>
              ) : null}
              {dimension.required_level ? (
                <p className="text-xs">
                  This role requires: <RatingLabel label={dimension.required_level} />
                </p>
              ) : null}
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-6">{dimension.remark}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}

/**
 * One radar plotting the job's required level against the candidate's assessed
 * level (spec §10.4). No numbers anywhere: no axis ticks, no data labels, no
 * tooltip. The legend names the two shapes by word.
 *
 * The radial domain is FIXED at 0..4 rather than auto: an auto domain rescales
 * per candidate, which would make a weak profile draw identically to a strong
 * one and defeat the whole point of comparing two people's charts.
 */
function DualRadar({ chart, series }: { chart: RadarChartSpec; series: string[] }) {
  if (chart.axes.length < 3) return null;
  const [requirementLabel = "Job Requirement", candidateLabel = "Candidate Assessment"] =
    series;

  const data = chart.axes.map((entry) => ({
    dimension: entry.axis,
    [requirementLabel]: entry.requirement_index,
    [candidateLabel]: entry.candidate_index,
  }));

  return (
    <div className="mb-4">
      <div className="h-[360px] rounded-lg border bg-background p-3">
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={data} outerRadius="70%">
            <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fill: "currentColor" }} />
            <PolarRadiusAxis domain={[0, RATING_GRADES.length]} tick={false} axisLine={false} />
            <Radar
              name={requirementLabel}
              dataKey={requirementLabel}
              stroke={REQUIREMENT_COLOR}
              strokeWidth={2}
              strokeDasharray="5 4"
              fill={REQUIREMENT_COLOR}
              fillOpacity={0.1}
              dot={false}
              isAnimationActive={false}
            />
            <Radar
              name={candidateLabel}
              dataKey={candidateLabel}
              stroke={CANDIDATE_COLOR}
              strokeWidth={2}
              fill={CANDIDATE_COLOR}
              fillOpacity={0.25}
              dot={false}
              isAnimationActive={false}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
          </RadarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** The grade ramp, outermost to innermost. Words only. */
function GradeLegend() {
  return (
    <ul className="flex flex-wrap items-center gap-3 text-xs" aria-label="Grade legend">
      {RATING_GRADES.map((grade) => (
        <li key={grade} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: BAND_FILL[grade] }}
          />
          {grade}
        </li>
      ))}
    </ul>
  );
}

function chartFor(report: FunctionalReport, key: string): RadarChartSpec | undefined {
  return report.radar_charts?.find((chart) => chart.key === key);
}

function ValidationSection({ validation }: { validation: ValidationBlock }) {
  // The server sends the field list in form order so the report and the form a
  // candidate filled in cannot drift apart.
  const fields = validation.fields ?? [];
  const facts = fields.filter((field) => field.key !== "role_interest");
  const interest = fields.find((field) => field.key === "role_interest")?.value;

  return (
    <section aria-label="Validation">
      <h3 className="mb-1 text-lg font-semibold">Validation</h3>
      <p className="mb-3 text-xs">
        Submitted by the candidate on their application and shown exactly as written. Nothing
        here is rated or interpreted.
      </p>
      {validation.captured === false ? (
        <p className="rounded-md border p-3 text-sm">
          This application was submitted before these fields became mandatory, so none were
          collected.
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {facts.map((field) => (
              <div key={field.key} className="rounded-md border p-3">
                <p className="text-xs font-medium uppercase tracking-wide">{field.label}</p>
                <p className="mt-1 text-sm">{field.value || "Not stated"}</p>
              </div>
            ))}
          </div>
          <blockquote className="mt-3 border-l-4 pl-4 text-sm italic">
            “{interest || "Not stated"}”
          </blockquote>
        </>
      )}
    </section>
  );
}

export function FunctionalSkillsReportView({ report }: { report: FunctionalReport }) {
  const series = report.radar_series ?? ["Job Requirement", "Candidate Assessment"];

  return (
    <div className="space-y-8">
      {/* ── AI Score: the pre-assessment snapshot (§10.1, §10.3) ───────────── */}
      <section aria-label="AI Score">
        <h3 className="mb-1 text-lg font-semibold">AI Score</h3>
        <p className="mb-3 text-xs">
          A resume-based snapshot generated before the assessment. A close match with the PPI
          Assessment below confirms the resume was accurate; a gap between them is itself
          useful signal.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          {report.ai_score.map((dimension) => (
            <Card key={`ai-${dimension.name}`}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                  <CardTitle className="text-base">{dimension.name}</CardTitle>
                  <RatingLabel label={dimension.grade} />
                </div>
                {dimension.description ? (
                  <p className="text-xs">{dimension.description}</p>
                ) : null}
              </CardHeader>
              <CardContent>
                <p className="text-sm leading-6">{dimension.remark}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* ── PPI Assessment: Overall, then the three framework sections ─────── */}
      <section aria-label="Overall Assessment">
        <h3 className="mb-1 text-lg font-semibold">PPI Assessment</h3>
        <div className="rounded-lg border bg-muted/30 p-5">
          <div className="mb-2 flex items-center gap-3">
            <p className="text-xs font-semibold uppercase tracking-wide">Overall</p>
            <RatingLabel label={report.overall_grade} />
          </div>
          <p className="leading-7">{report.overall_summary}</p>
        </div>
        <div className="mt-4">
          <DualRadar chart={chartFor(report, "overall") as RadarChartSpec} series={series} />
        </div>
        <GradeLegend />
      </section>

      <DimensionSection
        title="Must-have"
        dimensions={report.must_have}
        chart={chartFor(report, "must_have")}
        series={series}
      />
      <DimensionSection
        title="Nice-to-have"
        dimensions={report.nice_to_have}
        chart={chartFor(report, "nice_to_have")}
        series={series}
      />
      <DimensionSection
        title="Behavioural Competencies"
        dimensions={report.behavioural}
        chart={chartFor(report, "behavioural")}
        series={series}
      />

      <ValidationSection validation={report.validation} />

      <GapAnalysisSection report={report} />
    </div>
  );
}


/**
 * Gap Analysis & Action Plan (spec 9.6).
 *
 * Replaces the suggested-questions list entirely. Three groups in the same
 * Must-have / Nice-to-have / Behavioural order the rest of the report uses,
 * Not Matching before Moderately Matching inside each, and every empty group
 * says so in words rather than rendering blank space.
 *
 * The retired suggested-questions payload is deliberately never rendered.
 * Draft v4 replaced that client-facing section completely.
 */
function GapAnalysisSection({ report }: { report: FunctionalReport }) {
  const gaps = report.gap_analysis;
  if (!gaps || gaps.groups.length === 0) {
    return null;
  }

  return (
    <section aria-label="Gap Analysis and Action Plan">
      <h3 className="mb-3 text-lg font-semibold">Gap Analysis &amp; Action Plan</h3>
      {gaps.focus_summary ? (
        <p className="mb-4 rounded-md border bg-muted/30 p-4 font-medium leading-7">
          {gaps.focus_summary}
        </p>
      ) : null}

      <div className="space-y-6">
        {gaps.groups.map((group) => (
          <div key={group.category}>
            <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide">
              {group.label}
            </h4>
            {group.cap_statement ? (
              <p className="mb-3 rounded-md border border-dashed p-3 text-sm font-medium">
                {group.cap_statement}
              </p>
            ) : null}
            {group.items.length === 0 ? (
              <p className="rounded-md border p-3 text-sm">{group.no_gaps_statement}</p>
            ) : (
              <ul className="space-y-3">
                {group.items.map((item) => (
                  <li key={item.name} className="rounded-md border p-4">
                    <div className="mb-2 flex flex-wrap items-center gap-3">
                      <span className="font-medium">{item.name}</span>
                      <RatingLabel label={item.grade} />
                    </div>
                    {item.remark ? (
                      <p className="mb-3 text-sm leading-7">{item.remark}</p>
                    ) : null}
                    <ul className="space-y-2">
                      {item.probes.map((probe, index) => (
                        <li
                          key={`${item.name}-${index}`}
                          className="rounded-md border border-dashed p-3 text-sm"
                        >
                          {probe}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>

      <p className="mt-4 text-xs">
        Advisory input for the interviewer, grounded in what the candidate actually said. It
        identifies what to probe, never whether to advance or reject.
      </p>
    </section>
  );
}
