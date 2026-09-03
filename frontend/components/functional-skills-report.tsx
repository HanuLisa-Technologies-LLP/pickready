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
import { RATING_GRADES, type ProctoringReport, type RatingGrade } from "@/lib/types";

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

/**
 * The PRISM Report's section order (spec doc 4, part 3), and the ONLY place it
 * is written down on this side.
 *
 * The view below is driven by this array rather than by a hand-ordered block of
 * JSX, so a section cannot be moved by editing one file and left where it was
 * in the other. `backend/tests/test_prism_report.py` reads this literal out of
 * this source file and asserts it equals `report_pdf.SECTION_ORDER`: a reorder
 * that touches only one renderer fails there instead of shipping a PDF that
 * disagrees with the screen a recruiter approved it from.
 *
 * Gap Analysis now precedes Validation. Validation is the candidate's own
 * unrated submission and is the last thing on the document; the action plan
 * belongs beside the grades it was derived from, not after a block of
 * uninterpreted form answers.
 */
export const REPORT_SECTION_ORDER = [
  "ai_score",
  "overall",
  "must_have",
  "nice_to_have",
  "behavioural",
  "gap_analysis",
  "validation",
  // The Proctoring Report is LAST (proctoring spec section 7). It is
  // informational, it moves no grade, and it sits after everything that does.
  "proctoring",
] as const;

/**
 * THREE radar charts, not four (spec doc 4, part 3).
 *
 * The spec lists a chart for Overall Assessment, Must-have and Nice-to-have and
 * lists only a grade and a remark for Behavioural. Filtering here rather than
 * at the generator is deliberate: a report is immutable, so every report
 * already written still carries a behavioural chart in its stored payload, and
 * a reader opening one today must see the same three charts as a reader opening
 * one written tomorrow.
 */
export const RENDERED_CHART_KEYS = ["overall", "must_have", "nice_to_have"] as const;

export interface FunctionalReport {
  id: string;
  job_candidate_link_id: string;
  /** COMPANY-JOB-CANDIDATE, so a printed report and a row in the candidate
   *  table can be matched by eye. A label, never a permission. */
  reference_code?: string;
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
  /** The Proctoring Report, when one has been generated for this assessment.
   *  Null while the session is still running or its report is still being
   *  written. Words only: it carries no number at any path. */
  proctoring?: ProctoringReport | null;
  synthesized_at: string;
  immutable?: boolean;
}

interface ValidationField {
  key: string;
  label: string;
  value: string | null;
  /** "Application" for the six mandatory fields (the default when absent, for
   *  reports written before the profile questionnaire was added here), or the
   *  candidate profile form's own section title for the 38 profile items. */
  group?: string;
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
  // The allowlist is checked here rather than at each call site: a section
  // added later gets no chart unless somebody names it above, which is the
  // direction that fails safely against the three-chart rule.
  if (!(RENDERED_CHART_KEYS as readonly string[]).includes(key)) return undefined;
  return report.radar_charts?.find((chart) => chart.key === key);
}

function ValidationSection({ validation }: { validation: ValidationBlock }) {
  // The server sends the field list in form order so the report and the form a
  // candidate filled in cannot drift apart.
  const fields = validation.fields ?? [];
  const facts = fields.filter((field) => field.key !== "role_interest");
  const interest = fields.find((field) => field.key === "role_interest")?.value;

  // Grouped by "Application" (the six mandatory fields) then the candidate
  // profile form's own sections (the 38 profile items), in first-appearance
  // order, so the section reads as two questionnaires rather than one long
  // grid once the profile answers are included.
  const groups: Array<{ title: string; items: ValidationField[] }> = [];
  for (const field of facts) {
    const title = field.group ?? "Application";
    let group = groups.find((g) => g.title === title);
    if (!group) {
      group = { title, items: [] };
      groups.push(group);
    }
    group.items.push(field);
  }

  return (
    <section aria-label="Validation">
      <h3 className="mb-1 text-lg font-semibold">Validation</h3>
      <p className="mb-3 text-xs">
        Submitted by the candidate on their application and profile, shown exactly as written.
        Nothing here is rated or interpreted.
      </p>
      {validation.captured === false ? (
        <p className="rounded-md border p-3 text-sm">
          This application was submitted before these fields became mandatory, so none were
          collected.
        </p>
      ) : (
        <>
          {/* SEMANTIC left rule, and a documented Impeccable `side-tab`
              exception (.impeccable-exceptions.md). It marks the candidate's
              OWN unrated words, reproduced exactly as submitted, and that
              boundary is the point: a reader has to be able to tell what the
              candidate said from what the platform concluded. */}
          <blockquote className="mb-4 border-l-4 border-teal-600 pl-4 text-sm italic">
            “{interest || "Not stated"}”
          </blockquote>
          <div className="space-y-5">
            {groups.map((group) => (
              <div key={group.title}>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.title}
                </h4>
                <div className="grid gap-3 sm:grid-cols-2">
                  {group.items.map((field) => (
                    <div key={field.key} className="rounded-md border p-3">
                      <p className="text-xs font-medium uppercase tracking-wide">{field.label}</p>
                      <p className="mt-1 text-sm">{field.value || "Not stated"}</p>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

export function FunctionalSkillsReportView({ report }: { report: FunctionalReport }) {
  const series = report.radar_series ?? ["Job Requirement", "Candidate Assessment"];

  // Keyed by the section identifiers in REPORT_SECTION_ORDER and rendered by
  // walking that array. A section can therefore be reordered in exactly one
  // place, and a key with no entry here would render nothing loudly rather
  // than quietly moving to the end.
  const sections: Record<(typeof REPORT_SECTION_ORDER)[number], React.ReactNode> = {
    ai_score: <AiScoreSection key="ai_score" report={report} />,
    overall: <OverallSection key="overall" report={report} series={series} />,
    must_have: (
      <DimensionSection
        key="must_have"
        title="Must-have"
        dimensions={report.must_have}
        chart={chartFor(report, "must_have")}
        series={series}
      />
    ),
    nice_to_have: (
      <DimensionSection
        key="nice_to_have"
        title="Nice-to-have"
        dimensions={report.nice_to_have}
        chart={chartFor(report, "nice_to_have")}
        series={series}
      />
    ),
    // No chart: the spec gives Behavioural a grade and a remark only.
    behavioural: (
      <DimensionSection
        key="behavioural"
        title="Behavioural Competencies"
        dimensions={report.behavioural}
        series={series}
      />
    ),
    gap_analysis: <GapAnalysisSection key="gap_analysis" report={report} />,
    validation: <ValidationSection key="validation" validation={report.validation} />,
    proctoring: <ProctoringSection key="proctoring" report={report.proctoring ?? null} />,
  };

  return (
    <div className="space-y-8">
      {REPORT_SECTION_ORDER.map((key) => sections[key])}
    </div>
  );
}

/** The pre-assessment resume snapshot (spec doc 4, part 3). */
function AiScoreSection({ report }: { report: FunctionalReport }) {
  return (
      <section aria-label="AI Score">
        <h3 className="mb-1 text-lg font-semibold">AI Score</h3>
        <p className="mb-3 text-xs">
          A resume-based snapshot generated before the assessment. A close match with the Tatva
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
  );
}

/**
 * The Overall Assessment: the Overall Grade, its 45 to 50 word remark, and the
 * first of the three radar charts.
 *
 * The heading is the spec's own section name. It is deliberately not named
 * after the framework: the framework is the Tatva Assessment and this is the
 * PRISM Report that states its result, and using either word for the other is
 * how a reader ends up thinking they are one thing.
 */
function OverallSection({
  report,
  series,
}: {
  report: FunctionalReport;
  series: string[];
}) {
  const chart = chartFor(report, "overall");
  return (
      <section aria-label="Overall Assessment">
        <h3 className="mb-1 text-lg font-semibold">Overall Assessment</h3>
        <div className="rounded-lg border bg-muted/30 p-5">
          <div className="mb-2 flex items-center gap-3">
            <p className="text-xs font-semibold uppercase tracking-wide">Overall</p>
            <RatingLabel label={report.overall_grade} />
          </div>
          <p className="leading-7">{report.overall_summary}</p>
        </div>
        {chart ? (
          <div className="mt-4">
            <DualRadar chart={chart} series={series} />
          </div>
        ) : null}
        <GradeLegend />
      </section>
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

/**
 * The Proctoring Report (proctoring spec section 7), the final section.
 *
 * NO ICONS, NO COLOUR CODES, NO SEVERITY COLUMN (spec 7.1). The reader is
 * meant to understand importance from what is said and where it sits: the
 * most significant finding is first because the server ordered it that way,
 * and nothing on this page ranks it again. A tinted chip or a warning icon
 * beside a finding would state a judgement the system is not entitled to
 * make, which is the same reason the sentences never say the candidate
 * cheated.
 *
 * Every string here comes from the server's phrasing library. This component
 * writes only the two constants below, which are the same words the PDF
 * prints, because a recruiter reads one and forwards the other.
 */
export const PROCTORING_TITLE = "Proctoring Report";
export const PROCTORING_NOTE =
  "Informational only. This section does not affect this candidate's score or ranking.";
export const PROCTORING_ABSENT = "No proctoring report exists for this assessment.";

const FINDING_GROUPS: Array<{ key: keyof ProctoringReport["findings"]; label: string }> = [
  { key: "screen_browser", label: "Screen & Browser Activity" },
  { key: "camera", label: "Camera Monitoring" },
  { key: "audio", label: "Audio Monitoring" },
  { key: "answer_patterns", label: "Answer Pattern Analysis" },
];

function ProctoringSection({ report }: { report: ProctoringReport | null }) {
  return (
    <section aria-label="Proctoring Report">
      <h3 className="mb-1 text-lg font-semibold">{PROCTORING_TITLE}</h3>
      <p className="mb-3 text-xs">{PROCTORING_NOTE}</p>
      {report === null ? (
        <p className="rounded-md border p-3 text-sm">{PROCTORING_ABSENT}</p>
      ) : (
        <div className="space-y-5">
          <div className="rounded-md border p-4">
            <p className="text-xs">{report.date_line}</p>
            <p className="mt-2 font-medium leading-7">{report.outcome}</p>
            <p className="mt-2 text-sm leading-7">{report.summary}</p>
          </div>

          <div className="space-y-4">
            {FINDING_GROUPS.map((group) => (
              <div key={group.key}>
                <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide">{group.label}</h4>
                <ul className="space-y-2">
                  {report.findings[group.key].map((sentence, index) => (
                    <li key={`${group.key}-${index}`} className="text-sm leading-7">
                      {sentence}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          {report.activity_log.length > 0 ? (
            <div>
              <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide">Activity Log</h4>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[36rem] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-border text-left">
                      <th className="py-2 pr-4 font-medium">Time</th>
                      <th className="py-2 pr-4 font-medium">What happened</th>
                      <th className="py-2 pr-4 font-medium">How long</th>
                      <th className="py-2 font-medium">What the system did</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.activity_log.map((row, index) => (
                      <tr key={`log-${index}`} className="border-b border-border align-top">
                        <td className="py-2 pr-4 font-mono text-xs">{row.time}</td>
                        <td className="py-2 pr-4">{row.what_happened}</td>
                        <td className="py-2 pr-4">{row.how_long}</td>
                        <td className="py-2">{row.what_the_system_did}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          <p className="text-xs leading-6">{report.closing}</p>
        </div>
      )}
    </section>
  );
}
