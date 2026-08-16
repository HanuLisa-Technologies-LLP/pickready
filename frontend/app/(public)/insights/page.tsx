import type { Metadata } from "next";

import { FadeIn, Stagger, StaggerItem } from "@/components/motion";
import { Badge } from "@/components/ui/badge";

export const metadata: Metadata = {
  title: "Insights",
  description: "Practical thinking on evidence-led candidate decisions, assessment and trust.",
};

const ARTICLES = [
  ["Decision quality", "Why a shortlist needs evidence, not another score", "A score compresses uncertainty. A good decision profile names it, connects it to evidence and gives the interviewer a useful next question."],
  ["Candidate trust", "Consent should feel like a choice, not a buried checkbox", "Clear purpose, retention and withdrawal language is part of the product experience. It tells a candidate what happens before they share anything."],
  ["Interviews", "The interview should begin where the report ends", "When known strengths and open questions are already visible, the panel can spend its limited time testing judgment rather than repeating the resume."],
  ["AI operations", "What adaptive matching should learn - and what it should never decide", "Customer patterns can improve future relevance, but protected characteristics and final decisions must remain outside the model's authority."],
  ["People analytics", "Words make ratings usable", "A stable set of verbal bands creates a common language across role match, behaviour and technical depth without pretending that a person is a decimal."],
  ["Operating model", "Why one evidence trail beats six disconnected tools", "Context is lost at every hand-off. One continuous trail makes approvals, communication and later review easier to explain."],
] as const;

export default function InsightsPage() {
  return (
    <main id="main" className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-28">
      <FadeIn className="max-w-3xl">
        <p className="text-sm font-semibold uppercase tracking-[.18em] text-brand-600">ReadyPick Insights</p>
        <h1 className="mt-4 text-balance text-4xl font-bold sm:text-5xl">Better evidence. Better conversations.</h1>
        <p className="mt-6 text-pretty text-lg leading-8">
          Practical notes for people teams building faster, clearer and more accountable candidate decisions.
        </p>
      </FadeIn>
      <Stagger className="mt-14 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {ARTICLES.map(([tag, title, body]) => (
          <StaggerItem key={title}>
            <article className="flex h-full flex-col rounded-2xl border border-border bg-surface p-7 shadow-card">
              <Badge variant="outline" className="self-start">{tag}</Badge>
              <h2 className="mt-6 text-xl font-semibold leading-8">{title}</h2>
              <p className="mt-4 text-sm leading-7">{body}</p>
              <p className="mt-auto pt-8 text-xs font-semibold uppercase tracking-[.14em] text-brand-600">ReadyPick editorial</p>
            </article>
          </StaggerItem>
        ))}
      </Stagger>
    </main>
  );
}
