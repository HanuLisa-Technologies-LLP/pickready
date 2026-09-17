import Link from "next/link";
import { ArrowRight, ShieldCheck } from "lucide-react";

import { Reveal, RevealStagger, StaggerItem } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * The narrative sections between the product and the price.
 *
 * THREE EXPORTS WERE REMOVED HERE, AND THE REASONS ARE DIFFERENT:
 *
 *  - `Testimonials` carried three quotes attributed to an "HR leader", a
 *    "Talent partner" and a "Candidate". Nobody said them. An invented
 *    testimonial is the one kind of copy that cannot be repaired by editing,
 *    and the landing brief for this pass forbids one outright.
 *  - `Locations` claimed four offices. Nothing else in the repository, the
 *    About page included, states where the company sits, so the page would
 *    have been the only source for a fact it could not support.
 *  - `ProcessRoadmap` told the same story as `HowItWorks`, one screen apart,
 *    in six stages instead of three, on a rail that needed 900px of
 *    horizontal scroll on a phone. One implementation per concept: the
 *    sequence is told once, by `HowItWorks`.
 */

const INSIGHTS = [
  {
    tag: "Decision quality",
    title: "Why a shortlist needs evidence, not another score",
    body: "A practical framework for making profile, behaviour and technical signals readable together.",
  },
  {
    tag: "Candidate trust",
    title: "Consent should feel like a choice, not a buried checkbox",
    body: "How clear purpose, retention and withdrawal language improves the candidate experience.",
  },
  {
    tag: "Operating model",
    title: "The interview should begin where the report ends",
    body: "Use structured probes to spend conversation time on the uncertainties that matter.",
  },
] as const;

/** The four client-facing grades, in order. Words, and only words. */
const GRADES = [
  { word: "Highly Matching", tone: "rating1" },
  { word: "Matching", tone: "rating2" },
  { word: "Moderately Matching", tone: "rating3" },
  { word: "Not Matching", tone: "rating5" },
] as const;

/**
 * The thesis section, next to the report section's contents: one says what is
 * in the document, this one says why it is shaped that way.
 */
export function EvidenceProfile() {
  return (
    <section
      className="border-y border-border bg-navy-50/60 py-20 lg:py-24"
      aria-labelledby="evidence-title"
    >
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <div className="grid items-start gap-12 lg:grid-cols-[1.05fr_.95fr] lg:gap-14">
          <Reveal>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600">
              ReadyPick intelligence
            </p>
            <h2
              id="evidence-title"
              className="mt-4 text-balance text-2xl font-bold tracking-[-0.015em] sm:text-3xl"
            >
              AI can rank. A PRISM Report helps your team understand.
            </h2>
            <p className="mt-4 text-pretty text-base leading-7">
              ReadyPick Profile Intelligence connects role match, behavioural
              evidence, technical depth and validation into one readable
              decision profile. It is our own framework, generated from your job
              description, not a generic score pasted onto a resume.
            </p>
            {/* Flat rows on a hairline grid, not four bordered boxes inside a
                bordered panel. DESIGN.md section 4: no card inside a card. */}
            <ul className="mt-8 grid gap-px border border-border bg-border sm:grid-cols-2">
              {[
                "Three radar charts, and not one number on them",
                "Remarks tied to what the candidate actually said",
                "One conversation, not four separate bot threads",
                "Interview probes aimed at what stayed uncertain",
              ].map((item) => (
                <li
                  key={item}
                  className="flex items-start gap-2.5 bg-canvas p-4 text-sm leading-6"
                >
                  <ShieldCheck
                    className="mt-0.5 h-4 w-4 shrink-0 text-teal-700"
                    strokeWidth={1.5}
                    aria-hidden="true"
                  />
                  {item}
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal delay={0.08}>
            {/* This replaces a rotated diamond inside two concentric circles
                with the word PRISM set in gradient type across it. That was
                ornament standing in for an explanation. The rating vocabulary
                is the actual differentiator, so the panel now shows it. */}
            <div className="border border-border bg-surface">
              <h3 className="border-b border-border px-6 py-4 text-sm font-semibold">
                Every rating, in four words
              </h3>
              <ul className="divide-y divide-border">
                {GRADES.map((grade) => (
                  <li key={grade.word} className="px-6 py-4">
                    <Badge variant={grade.tone}>{grade.word}</Badge>
                  </li>
                ))}
              </ul>
              <p className="border-t border-border px-6 py-4 text-xs font-medium leading-5">
                No percentage, no rank, no letter. The same four words on the
                screen, in the PDF and in the email.
              </p>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

export function AboutPreview() {
  return (
    <section
      className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-24"
      aria-labelledby="about-preview-title"
    >
      <div className="grid gap-10 lg:grid-cols-[.9fr_1.1fr] lg:items-center lg:gap-14">
        {/* The dark panel carried a blurred teal circle in the corner, the
            same ambient glow removed from the hero. A teal rule does the same
            job of marking the panel as ours and holds still while doing it. */}
        <Reveal className="relative flex min-h-72 flex-col justify-between border border-navy-700 bg-navy-900 p-8 text-white">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-teal-400">
            Built from the inside
          </p>
          <span aria-hidden="true" className="mt-6 block h-px w-16 bg-teal-400" />
          <p className="mt-6 max-w-sm text-balance text-2xl font-semibold leading-tight sm:text-3xl">
            Twenty-five years in HR. One conviction: technology should give the
            team time back.
          </p>
          <p className="mt-8 text-sm font-medium">
            Manjunath, Founder and CEO
          </p>
        </Reveal>

        <Reveal delay={0.08}>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600">
            About ReadyPick
          </p>
          <h2
            id="about-preview-title"
            className="mt-4 text-balance text-2xl font-bold tracking-[-0.015em] sm:text-3xl"
          >
            Experience became a different operating model
          </h2>
          <p className="mt-4 text-pretty text-base leading-7">
            ReadyPick grew from years spent seeing where teams lose time:
            disconnected sourcing, repetitive screening, opaque scoring and
            systems that move the administrative load instead of removing it.
          </p>
          <p className="mt-4 text-pretty text-base leading-7">
            We combine AI-driven discovery and assessment with human validation
            before a profile reaches the customer. The result is not more
            activity. It is a profile the team can act on.
          </p>
          <Button asChild variant="outline" className="group mt-7">
            <Link href="/about">
              Meet the idea and the team
              <ArrowRight
                className="transition-transform group-hover:translate-x-0.5"
                aria-hidden="true"
              />
            </Link>
          </Button>
        </Reveal>
      </div>
    </section>
  );
}

export function InsightsPreview() {
  return (
    <section
      className="border-y border-border bg-surface/60 py-20 lg:py-24"
      aria-labelledby="insights-title"
    >
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <Reveal>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600">
              Insights
            </p>
            <h2
              id="insights-title"
              className="mt-4 text-balance text-2xl font-bold tracking-[-0.015em] sm:text-3xl"
            >
              Ideas for evidence-led people decisions
            </h2>
          </Reveal>
          <Button
            asChild
            variant="ghost"
            className="group self-start sm:self-auto"
          >
            <Link href="/insights">
              Read all insights
              <ArrowRight
                className="transition-transform group-hover:translate-x-0.5"
                aria-hidden="true"
              />
            </Link>
          </Button>
        </div>

        {/* Three cards, stacked at 375px and side by side from `sm`. The
            horizontal snap carousel this replaces put 82vw cards in a scroller
            on the one viewport that can least afford a second scroll axis. */}
        <RevealStagger className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {INSIGHTS.map((item) => (
            <StaggerItem key={item.title} className="h-full">
              <article className="h-full border border-border bg-canvas p-6 transition-colors duration-150 hover:border-field-hover">
                <Badge variant="outline">{item.tag}</Badge>
                <h3 className="mt-5 text-lg font-semibold leading-7">
                  {item.title}
                </h3>
                <p className="mt-3 text-sm leading-6">{item.body}</p>
              </article>
            </StaggerItem>
          ))}
        </RevealStagger>
      </div>
    </section>
  );
}
