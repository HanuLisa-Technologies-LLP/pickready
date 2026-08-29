import Link from "next/link";
import { ArrowRight, PlayCircle } from "lucide-react";

import { WorkflowAnimation } from "@/components/workflow-animation";
import { Pressable, Reveal } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function WorkflowShowcase() {
  return (
    <section
      id="workflow"
      className="relative scroll-mt-24 overflow-hidden border-y border-border bg-[#070812] py-20 text-white lg:py-28"
      aria-labelledby="workflow-title"
    >
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-0 h-96 w-[52rem] -translate-x-1/2 rounded-full bg-teal-700/20 blur-[120px]" />
      </div>
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
          <p className="mx-auto mt-5 max-w-2xl text-pretty text-base leading-7 text-white/65 sm:text-lg">
            From a live role to AI matching, structured assessment, the PPI
            Assessment Report and a clear shortlist - one continuous evidence
            trail.
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
