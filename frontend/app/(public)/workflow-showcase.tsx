/**
 * The animated product tour.
 *
 * IT WAS HELD BACK ONCE, AND THE REASON IS WORTH KEEPING.
 * `components/workflow-animation` used to render a percentage beside each
 * candidate in its ranking scene (`{candidate.score}%`, 94 / 89 / 84 / 76).
 * That is a number attached to a rated person on a client-facing surface,
 * which is the rule this product breaks least willingly, and it contradicted
 * the hero's own promise of plain language and no scores to argue about. The
 * section was left unmounted rather than deleted until the data shape was
 * fixed. `step-scenes.tsx` now carries the four grade WORDS, with the bar's
 * width as an undisplayed rendering coordinate, so it is composed again.
 */
import Link from "next/link";
import { ArrowRight, PlayCircle } from "lucide-react";

import { WorkflowAnimation } from "@/components/workflow-animation";
import { Reveal } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function WorkflowShowcase() {
  return (
    <section
      id="workflow"
      className="relative scroll-mt-24 overflow-hidden border-y border-border bg-navy-900 py-20 text-white lg:py-28"
      aria-labelledby="workflow-title"
    >
      <div className="relative mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal className="mx-auto max-w-3xl text-center">
          <Badge className="border-teal-400/20 bg-teal-400/10 text-teal-100">
            <PlayCircle className="mr-1.5 h-3.5 w-3.5" />
            25-second product tour
          </Badge>
          <h2
            id="workflow-title"
            className="mt-5 text-balance text-3xl font-bold leading-tight sm:text-4xl"
          >
            Watch the work move. Your team keeps the decision.
          </h2>
          <p className="mx-auto mt-5 max-w-2xl text-pretty text-base leading-7 sm:text-lg">
            From a live role to AI matching, structured assessment, the PRISM
            Report and a clear shortlist, one continuous evidence trail.
          </p>
        </Reveal>

        <Reveal delay={0.08} className="mt-12">
          <WorkflowAnimation />
        </Reveal>

        <Reveal
          delay={0.12}
          className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row"
        >
          <Button asChild size="lg" className="group">
            <Link href="/register">
              Get started free
              <ArrowRight className="transition-transform group-hover:translate-x-0.5" />
            </Link>
          </Button>
          <Button
            asChild
            size="lg"
            variant="outline"
            className="border-white/20 bg-white/5 text-white hover:bg-white/10 hover:text-white"
          >
            <a href="#how-it-works">Read the workflow</a>
          </Button>
        </Reveal>
      </div>
    </section>
  );
}
