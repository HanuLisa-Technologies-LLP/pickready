import {
  Database,
  FileCheck2,
  GitBranch,
  Layers,
  MessagesSquare,
  ShieldCheck,
} from "lucide-react";

import { Reveal, RevealStagger, StaggerItem } from "@/components/motion";
import { FeatureCard } from "./feature-card";

/**
 * Six capabilities, each one a claim about the product as it ships.
 *
 * REWIRED 2026-09-28. Three cards had stopped being true: the "one
 * conversation" card described the retired adaptive interview rather than the
 * one proctored, server-timed assessment; the profile card promised a profile
 * "theirs to take elsewhere", which was the cross-employer reuse layer that is
 * now deleted; and the drafts card promised every email is edited before it is
 * sent, while the invitation is drafted and sent by a worker. Each now says
 * what the product does.
 */
const FEATURES = [
  {
    icon: Database,
    title: "Candidate databank",
    description:
      "Applicants, sourced resumes and databank uploads are all read against the same skills. A candidate who has not applied is labelled as not having applied, until they do.",
  },
  {
    icon: Layers,
    title: "One assessment, every skill asked",
    description:
      "One proctored session asks about every skill the team saved, in a fixed order the server times. Answers are typed or spoken prose, with multiple choice and fill in the blank questions beside them.",
  },
  {
    icon: FileCheck2,
    title: "One resume, many applications",
    description:
      "A candidate keeps a main resume and reuses it on every application. Each application is a snapshot of what was actually sent, and a job's skills hold still once real applications arrive.",
  },
  {
    icon: GitBranch,
    title: "A pipeline that holds",
    description:
      "Applications move through validated stages. An illegal jump is refused, so a stage always means what the emails say it means.",
  },
  {
    icon: MessagesSquare,
    title: "Drafted, then decided by you",
    description:
      "The job description and its skills arrive as drafts, and nothing is published until your team has edited and saved them. Every email and message to a candidate is logged.",
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
      className="scroll-mt-20 border-y border-border bg-surface/60 py-20 lg:py-24"
      aria-labelledby="features-title"
    >
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal className="max-w-2xl">
          <p className="type-eyebrow text-brand-600">
            Platform
          </p>
          <h2
            id="features-title"
            className="mt-4 type-section-title"
          >
            Built for teams who have to defend the decision
          </h2>
          <p className="mt-4 type-lead">
            Everything below is in the product today, not on a roadmap.
          </p>
        </Reveal>

        {/* `RevealStagger`, never `Stagger`: this grid is below the fold, and
            a mount-triggered stagger would have finished before the reader
            ever reached it. The distinction is documented in
            components/motion/motion-primitives.tsx.

            The per-card `HoverLift` is gone. Six cards rising under the
            pointer is decoration six times over, and the card already answers
            the pointer with its border. */}
        <RevealStagger className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((feature) => (
            <StaggerItem key={feature.title} className="h-full">
              <FeatureCard {...feature} />
            </StaggerItem>
          ))}
        </RevealStagger>
      </div>
    </section>
  );
}
