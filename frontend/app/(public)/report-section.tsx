import { Check } from "lucide-react";

import { Reveal } from "@/components/motion";
import { Badge } from "@/components/ui/badge";

const POINTS = [
  "An AI Score from the resume, then a PPI Assessment from the conversation. Shown side by side, never merged.",
  "Primary Skills, Secondary Skills and Behavioural Competencies, each with a 45 to 50 word remark.",
  "Four radar charts, each plotting what the job needs against what the candidate showed.",
  "Reports are immutable. A retake creates a new report beside the old one.",
];

/** Rated items from the sample card. Word labels only, never a number. */
const SAMPLE = [
  { dimension: "AI Score, skills match", label: "Highly Matching", tone: "rating1" },
  { dimension: "Distributed systems", label: "Highly Matching", tone: "rating1" },
  { dimension: "Data modelling", label: "Matching", tone: "rating2" },
  { dimension: "Collaboration", label: "Matching", tone: "rating2" },
  { dimension: "Stakeholder influence", label: "Moderately Matching", tone: "rating3" },
] as const;

export function ReportSection() {
  return (
    <section
      id="report"
      className="mx-auto max-w-6xl scroll-mt-20 px-6 py-20 lg:px-10 lg:py-28"
      aria-labelledby="report-title"
    >
      <div className="grid items-center gap-14 lg:grid-cols-2">
        <Reveal>
          <p className="text-sm font-semibold uppercase tracking-[0.16em] text-brand-600">
            The PPI Assessment Report
          </p>
          <h2
            id="report-title"
            className="mt-3 text-balance text-2xl font-bold sm:text-3xl"
          >
            One page your hiring manager will actually read
          </h2>
          <p className="mt-4 text-pretty text-base leading-7">
            The report is the point of the whole product. It says what a
            candidate can do, in words, and it never puts a score in front of
            anyone. Four grades do all the rating: Highly Matching, Matching,
            Moderately Matching and Not Matching.
          </p>

          <ul className="mt-8 space-y-4">
            {POINTS.map((point) => (
              <li key={point} className="flex gap-3">
                <span
                  aria-hidden="true"
                  className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand-100"
                >
                  <Check
                    className="h-3 w-3 text-accent-foreground"
                    aria-hidden="true"
                  />
                </span>
                <span className="text-pretty text-sm leading-6">{point}</span>
              </li>
            ))}
          </ul>
        </Reveal>

        <Reveal delay={0.06} className="relative">
          <div className="relative overflow-hidden border border-border bg-surface p-6 shadow-card sm:p-8">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold">
                  PPI Assessment Report
                </h3>
                <p className="mt-1 text-xs opacity-70">
                  Senior Data Engineer, sample
                </p>
              </div>
              <Badge variant="rating1">Highly Matching</Badge>
            </div>

            <p className="mt-6 text-pretty text-sm leading-6">
              Designs and operates batch and streaming pipelines end to end,
              reasons clearly about schema change, and brings analysts along
              with the design. Would benefit from more practice negotiating
              scope with senior stakeholders.
            </p>

            <dl className="mt-7 space-y-3">
              {SAMPLE.map((row) => (
                <div
                  key={row.dimension}
                  className="flex items-center justify-between gap-3 rounded-lg border border-border px-4 py-3"
                >
                  <dt className="min-w-0 truncate text-sm font-medium">
                    {row.dimension}
                  </dt>
                  <dd className="shrink-0">
                    <Badge variant={row.tone}>{row.label}</Badge>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
