import { FileText, ScanSearch, UserCheck } from "lucide-react";

import { Reveal, Stagger, StaggerItem } from "@/components/motion";

const STEPS = [
  {
    icon: FileText,
    title: "Post the role",
    body: "Describe the job once. ReadyPick drafts the description, your team edits it, and publishing gives you one link to share on any job board.",
  },
  {
    icon: ScanSearch,
    title: "AI ranks and assesses",
    body: "Every applicant is parsed and ranked against the role. The candidates you select take a structured assessment generated from that job's own skills.",
  },
  {
    icon: UserCheck,
    title: "You decide",
    body: "Read one report per candidate, with rated remarks in plain words and suggested interview probes. Then move people through the pipeline.",
  },
];

export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="mx-auto max-w-6xl scroll-mt-20 px-6 py-20 lg:px-10 lg:py-28"
      aria-labelledby="how-it-works-title"
    >
      <Reveal className="max-w-2xl">
        <p className="text-sm font-semibold uppercase tracking-[0.16em] text-brand-600">
          How it works
        </p>
        <h2
          id="how-it-works-title"
          className="mt-3 text-balance text-2xl font-bold sm:text-3xl"
        >
          Three steps, and the middle one is not your job
        </h2>
      </Reveal>

      <Stagger className="mt-12 grid gap-6 md:grid-cols-3">
        {STEPS.map((step, index) => (
          <StaggerItem key={step.title}>
            <div className="relative h-full rounded-xl border border-border bg-surface p-6 shadow-card transition-shadow duration-150 hover:shadow-card-hover">
              <div className="flex items-center gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-brand-100 text-accent-foreground">
                  <step.icon className="h-5 w-5" aria-hidden="true" />
                </span>
                <span
                  aria-hidden="true"
                  className="text-sm font-semibold opacity-40"
                >
                  Step {index + 1}
                </span>
              </div>
              <h3 className="mt-5 text-lg font-semibold">{step.title}</h3>
              <p className="mt-2 text-pretty text-sm leading-6">{step.body}</p>
            </div>
          </StaggerItem>
        ))}
      </Stagger>
    </section>
  );
}
