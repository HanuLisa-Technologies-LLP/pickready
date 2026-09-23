import { Check } from "lucide-react";

import { Reveal } from "@/components/motion";
import { Badge } from "@/components/ui/badge";

/**
 * What the delivered document actually contains.
 *
 * THE CHART COUNT IS THREE. This copy said four, which stopped being true on
 * 2026-08-23 when the Behavioural dimension lost its chart: it carries a grade
 * and a remark and no radar. The dimension names were the old PPI wording
 * (Primary Skills, Secondary Skills) and are now the Tatva Assessment's three,
 * Must-have, Nice-to-have and Behavioural. Neither correction invents anything;
 * both bring the page back onto what the product ships.
 */
const POINTS = [
  "An AI Score from the resume, then a Tatva Assessment from the conversation. Shown side by side, never merged.",
  "Must-have, Nice-to-have and Behavioural, each with a 45 to 50 word remark.",
  "Three radar charts, each plotting what the job needs against what the candidate showed.",
  "Reports are immutable. A retake creates a new report beside the old one.",
];

/** Rated items from the sample card. Word labels only, never a number. */
const SAMPLE = [
  {
    dimension: "AI Score, skills match",
    label: "Highly Matching",
    tone: "rating1",
  },
  { dimension: "Distributed systems", label: "Highly Matching", tone: "rating1" },
  { dimension: "Data modelling", label: "Matching", tone: "rating2" },
  { dimension: "Collaboration", label: "Matching", tone: "rating2" },
  {
    dimension: "Stakeholder influence",
    label: "Moderately Matching",
    tone: "rating3",
  },
] as const;

export function ReportSection() {
  return (
    <section
      id="report"
      className="mx-auto max-w-6xl scroll-mt-20 px-6 py-20 lg:px-10 lg:py-24"
      aria-labelledby="report-title"
    >
      <div className="grid items-start gap-12 lg:grid-cols-2 lg:gap-14">
        <Reveal>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600">
            The PRISM Report
          </p>
          <h2
            id="report-title"
            className="mt-4 text-balance text-2xl font-bold tracking-[-0.015em] sm:text-3xl"
          >
            One page your hiring manager will actually read
          </h2>
          <p className="mt-4 text-pretty text-base">
            The report is the point of the whole product. It says what a
            candidate can do, in words, and it never puts a score in front of
            anyone. Four grades do all the rating: Highly Matching, Matching,
            Moderately Matching and Not Matching.
          </p>

          <ul className="mt-8 space-y-4">
            {POINTS.map((point) => (
              <li key={point} className="flex gap-3">
                {/* A plain check in navy, not a check inside a tinted
                    circle. The circle was a second shape carrying the same one
                    bit of information. */}
                <Check
                  className="mt-1 h-4 w-4 shrink-0 text-navy-600"
                  strokeWidth={1.5}
                  aria-hidden="true"
                />
                <span className="text-pretty text-sm">{point}</span>
              </li>
            ))}
          </ul>
        </Reveal>

        <Reveal delay={0.06} className="relative">
          <div className="relative overflow-hidden border border-border bg-surface">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-6 sm:p-7">
              <div>
                <h3 className="text-base font-semibold">PRISM Report</h3>
                {/* Full ink at 13px rather than dimmed body text. */}
                <p className="mt-1 text-xs font-medium">
                  Senior Data Engineer, sample
                </p>
              </div>
              <Badge variant="rating1">Highly Matching</Badge>
            </div>

            <p className="text-pretty p-6 text-sm sm:p-7 sm:pb-6">
              Designs and operates batch and streaming pipelines end to end,
              reasons clearly about schema change, and brings analysts along
              with the design. Would benefit from more practice negotiating
              scope with senior stakeholders.
            </p>

            {/* NO CARD INSIDE A CARD. Each rated line used to be its own
                bordered box inside this bordered panel, which DESIGN.md
                section 4 names as a rendering fault the eye reads before the
                content. A grouping inside a card is a rule and a heading, so
                the rows are divided by hairlines instead. */}
            <dl className="divide-y divide-border border-t border-border">
              {SAMPLE.map((row) => (
                <div
                  key={row.dimension}
                  className="flex items-center justify-between gap-3 px-6 py-3.5 sm:px-7"
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
