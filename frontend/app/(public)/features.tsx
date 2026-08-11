import {
  Database,
  FileCheck2,
  GitBranch,
  Layers,
  MessagesSquare,
  ShieldCheck,
} from "lucide-react";

import {
  HoverLift,
  Reveal,
  RevealStagger,
  StaggerItem,
} from "@/components/motion";
import { FeatureCard } from "./feature-card";

const FEATURES = [
  {
    icon: Database,
    title: "Candidate databank",
    description:
      "Applied, sourced and uploaded candidates share one pool. Everyone is parsed and matched the same way, whichever door they came through.",
  },
  {
    icon: Layers,
    title: "One conversation, not four bots",
    description:
      "Technical depth and behavioural evidence come out of a single interview that follows what the candidate says. Nobody is handed four separate threads to finish.",
  },
  {
    icon: FileCheck2,
    title: "One profile, many applications",
    description:
      "A candidate keeps a main resume and a profile they fill in once, and it stays theirs to take elsewhere. Each application is an immutable snapshot of what was actually sent.",
  },
  {
    icon: GitBranch,
    title: "A pipeline that holds",
    description:
      "Applications move through validated stages. An illegal jump is refused, so a stage always means what the emails say it means.",
  },
  {
    icon: MessagesSquare,
    title: "Drafted, then edited by you",
    description:
      "Job descriptions and lifecycle emails arrive as drafts. Your team edits before anything is published or sent, and every send is logged.",
  },
  {
    icon: ShieldCheck,
    title: "Tenant isolation and audit",
    description:
      "Row level security separates every customer's data, capabilities are configuration rather than code, and each request is recorded.",
  },
];

export function Features() {
  return (
    <section
      id="features"
      className="scroll-mt-20 border-y border-border bg-surface/60 py-20 lg:py-28"
      aria-labelledby="features-title"
    >
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal className="max-w-2xl">
          <p className="text-sm font-semibold uppercase tracking-[0.16em] text-brand-600">
            Platform
          </p>
          <h2
            id="features-title"
            className="mt-3 text-balance text-2xl font-bold sm:text-3xl"
          >
            Built for teams who have to defend the decision
          </h2>
          <p className="mt-4 text-pretty text-base leading-7">
            Everything below is in the product today, not on a roadmap.
          </p>
        </Reveal>

        <RevealStagger className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature) => (
            <StaggerItem key={feature.title} className="h-full">
              <HoverLift className="h-full">
                <FeatureCard {...feature} />
              </HoverLift>
            </StaggerItem>
          ))}
        </RevealStagger>
      </div>
    </section>
  );
}
