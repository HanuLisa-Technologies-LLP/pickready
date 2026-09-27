import { FileText, ScanSearch, UserCheck } from "lucide-react";

import { Reveal, RevealStagger, StaggerItem } from "@/components/motion";

/**
 * The sequence, told once (the flow the owner ruled on 2026-09-28): JD, then
 * Skills, then the final job posting, then publish. The SWOT the team may keep
 * is internal hiring intelligence and is not a step anybody is sold here.
 * "Suggested interview probes" became the gap analysis they now live in.
 */
const STEPS = [
  {
    icon: FileText,
    title: "Write the JD and its Skills",
    body: "Vivekium drafts the job description and proposes the Must-have, Nice-to-have and Behavioural skills from it. Your team edits both, checks the final job posting and publishes one link to share on any job board. The JD and skills hold still from the first real application.",
  },
  {
    icon: ScanSearch,
    title: "AI matches, then assesses",
    body: "AI Match reads every resume against those skills and ranks applicants with the evidence it found and a grade in words. The candidates you invite take one proctored assessment built from the same skills: typed or spoken answers, multiple choice, fill in the blank, and coding run in a sandbox.",
  },
  {
    icon: UserCheck,
    title: "You decide",
    body: "Read a PRISM Report and a Proctoring Report for each assessed candidate, with every skill graded in plain words and a gap analysis to take into the interview. Then move people through the pipeline.",
  },
];

export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="mx-auto max-w-6xl scroll-mt-20 px-6 py-20 lg:px-10 lg:py-24"
      aria-labelledby="how-it-works-title"
    >
      <Reveal className="max-w-2xl">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600">
          How it works
        </p>
        <h2
          id="how-it-works-title"
          className="mt-4 text-balance text-2xl font-bold tracking-[-0.015em] sm:text-3xl"
        >
          Three steps, and the middle one is not your job
        </h2>
      </Reveal>

      {/* `RevealStagger` rather than `Stagger`: the three steps sit below the
          fold, where a mount-triggered animation runs and finishes before
          anybody has scrolled to it. */}
      <RevealStagger className="mt-12 grid gap-5 md:grid-cols-3">
        {STEPS.map((step, index) => (
          <StaggerItem key={step.title}>
            <div className="h-full border border-border bg-surface p-6 transition-colors duration-150 hover:border-field-hover">
              {/* The step number carries the sequence, so it is set in ink at
                  full strength. It used to be `opacity-40`, which is grey text
                  by another name, and DESIGN.md section 3 has no exception for
                  a faked one. The rounded icon tile that sat beside it is gone
                  for the reason section 4 gives: a shape with no information
                  in it. */}
              <div className="flex items-center gap-2.5 border-b border-border pb-4">
                <step.icon
                  className="h-5 w-5 shrink-0 text-navy-600"
                  strokeWidth={1.5}
                  aria-hidden="true"
                />
                <span className="text-xs font-semibold uppercase tracking-[0.18em]">
                  Step {index + 1}
                </span>
              </div>
              <h3 className="mt-5 text-lg font-semibold">{step.title}</h3>
              <p className="mt-2 text-pretty text-sm">{step.body}</p>
            </div>
          </StaggerItem>
        ))}
      </RevealStagger>
    </section>
  );
}
