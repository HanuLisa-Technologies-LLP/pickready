import Link from "next/link";
import {
  ArrowRight,
  Building2,
  FileSearch,
  Fingerprint,
  MapPin,
  ScanSearch,
  Send,
  ShieldCheck,
  UserRoundCheck,
} from "lucide-react";

import {
  HoverLift,
  Reveal,
  RevealStagger,
  StaggerItem,
} from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const ROADMAP = [
  { icon: Building2, label: "Role", detail: "One approved brief" },
  { icon: ScanSearch, label: "Source", detail: "Applied, uploaded, databank" },
  { icon: Fingerprint, label: "Match", detail: "Evidence against the job" },
  { icon: UserRoundCheck, label: "Assess", detail: "Role-shaped questions" },
  { icon: FileSearch, label: "PPI", detail: "One complete profile" },
  { icon: Send, label: "Decide", detail: "Human call, clear trail" },
] as const;

const TESTIMONIALS = [
  {
    quote:
      "The report gave our panel a common language. We entered the interview knowing what to verify, not where to start.",
    name: "HR leader",
    company: "Technology services, Bengaluru",
  },
  {
    quote:
      "We could see why a profile matched. That transparency made the shortlist much easier to defend with the business.",
    name: "Talent partner",
    company: "Financial services, Hyderabad",
  },
  {
    quote:
      "The experience felt considered from the candidate side too. Every message was clear about the next step.",
    name: "Candidate",
    company: "Product engineering",
  },
] as const;

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

export function ProcessRoadmap() {
  return (
    <section
      className="border-y border-border bg-surface/55 py-20 lg:py-24"
      aria-labelledby="roadmap-title"
    >
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal className="max-w-2xl">
          <p className="text-sm font-semibold uppercase tracking-[.16em] text-brand-600">
            End-to-end roadmap
          </p>
          <h2
            id="roadmap-title"
            className="mt-3 text-balance text-2xl font-bold sm:text-3xl"
          >
            One evidence line, from role to decision
          </h2>
        </Reveal>
        <div className="relative mt-10 overflow-x-auto pb-4 [scrollbar-width:thin]">
          <div className="absolute left-12 right-12 top-8 hidden h-px bg-gradient-to-r from-transparent via-brand-600/45 to-transparent sm:block" />
          <ol className="relative grid min-w-[900px] grid-cols-6 gap-4 sm:min-w-0">
            {ROADMAP.map((item, index) => (
              <li key={item.label} className="relative">
                <HoverLift className="h-full rounded-2xl border border-border bg-canvas p-5 shadow-card transition-[border-color,box-shadow] duration-150 hover:border-field hover:shadow-pop">
                  <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-100 text-accent-foreground">
                    <item.icon className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <p className="mt-5 text-xs font-semibold text-brand-600">
                    0{index + 1}
                  </p>
                  <h3 className="mt-1 font-semibold">{item.label}</h3>
                  <p className="mt-2 text-sm leading-6">{item.detail}</p>
                </HoverLift>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

export function PfiDifferentiator() {
  return (
    <section
      className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-28"
      aria-labelledby="ppi-edge-title"
    >
      <div className="overflow-hidden rounded-3xl border border-border bg-[linear-gradient(135deg,hsl(var(--surface)),hsl(var(--brand-100)))] p-7 shadow-pop sm:p-10">
        <div className="grid items-center gap-10 lg:grid-cols-[1.05fr_.95fr]">
          <Reveal>
            <Badge variant="brand">PickReady intelligence</Badge>
            <h2
              id="ppi-edge-title"
              className="mt-5 text-balance text-3xl font-bold"
            >
              AI can rank. PPI helps your team understand.
            </h2>
            <p className="mt-5 text-pretty text-lg leading-8">
              PickReady Profile Intelligence connects role match, behavioural
              evidence, technical depth and validation into one readable
              decision profile. It is our own framework, generated from your job
              description, not a generic score pasted onto a resume.
            </p>
            <ul className="mt-7 grid gap-3 text-sm sm:grid-cols-2">
              {[
                "Four radar charts, and not one number on them",
                "Remarks tied to what the candidate actually said",
                "One conversation, not four separate bot threads",
                "Interview probes aimed at what stayed uncertain",
              ].map((item) => (
                <li
                  key={item}
                  className="flex items-start gap-2 rounded-xl border border-border bg-canvas/70 p-3"
                >
                  <ShieldCheck
                    className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
                    aria-hidden="true"
                  />
                  {item}
                </li>
              ))}
            </ul>
          </Reveal>
          <Reveal delay={0.08} className="relative">
            <div className="mx-auto aspect-square max-w-sm rounded-full border border-brand-600/20 bg-canvas/70 p-8 shadow-card">
              <div className="grid h-full place-items-center rounded-full border border-dashed border-brand-600/30">
                <div className="grid h-[72%] w-[72%] rotate-45 place-items-center rounded-[2rem] border border-brand-600/35 bg-brand-600/10">
                  <div className="-rotate-45 text-center">
                    <p className="text-5xl font-black text-gradient-brand">
                      PPI
                    </p>
                    <p className="mt-2 text-xs font-semibold uppercase tracking-[.2em]">
                      Decision intelligence
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

export function Testimonials() {
  return (
    <section
      className="border-y border-border bg-surface/55 py-20"
      aria-labelledby="voices-title"
    >
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <Reveal>
          <p className="text-sm font-semibold uppercase tracking-[.16em] text-brand-600">
            From the people doing the work
          </p>
          <h2 id="voices-title" className="mt-3 text-2xl font-bold sm:text-3xl">
            Clearer inputs change the conversation
          </h2>
        </Reveal>
        <div className="-mx-6 mt-10 flex snap-x snap-mandatory gap-5 overflow-x-auto px-6 pb-5 [scrollbar-width:thin] lg:mx-0 lg:px-0">
          {TESTIMONIALS.map((item) => (
            <HoverLift
              key={item.quote}
              className="min-w-[84vw] snap-start rounded-2xl border border-border bg-canvas p-7 shadow-card transition-[border-color,box-shadow] duration-150 hover:border-field hover:shadow-pop sm:min-w-[28rem] lg:min-w-[34rem]"
            >
              <article>
                <p className="text-balance text-lg font-medium leading-8">
                  &ldquo;{item.quote}&rdquo;
                </p>
                <p className="mt-6 text-sm font-semibold">{item.name}</p>
                <p className="mt-1 text-sm">{item.company}</p>
              </article>
            </HoverLift>
          ))}
        </div>
      </div>
    </section>
  );
}

export function AboutPreview() {
  return (
    <section
      className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-28"
      aria-labelledby="about-preview-title"
    >
      <div className="grid gap-10 lg:grid-cols-[.9fr_1.1fr] lg:items-center">
        <Reveal className="relative min-h-72 overflow-hidden rounded-3xl bg-[#090b16] p-8 text-white shadow-pop">
          <div
            aria-hidden="true"
            className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-violet-600/35 blur-3xl"
          />
          <p className="relative text-xs font-semibold uppercase tracking-[.2em] text-violet-300">
            Built from the inside
          </p>
          <p className="relative mt-10 max-w-sm text-3xl font-bold leading-tight">
            Twenty-five years in HR. One conviction: technology should give the
            team time back.
          </p>
          <p className="relative mt-8 text-sm text-white/55">
            Manjunath · Founder &amp; CEO
          </p>
        </Reveal>
        <Reveal delay={0.08}>
          <p className="text-sm font-semibold uppercase tracking-[.16em] text-brand-600">
            About PickReady
          </p>
          <h2
            id="about-preview-title"
            className="mt-3 text-balance text-3xl font-bold"
          >
            Experience became a different operating model
          </h2>
          <p className="mt-5 text-pretty text-lg leading-8">
            PickReady grew from years spent seeing where teams lose time:
            disconnected sourcing, repetitive screening, opaque scoring and
            systems that move the administrative load instead of removing it.
          </p>
          <p className="mt-4 text-pretty leading-7">
            We combine AI-driven discovery and assessment with human validation
            before a profile reaches the customer. The result is not more
            activity - it is a profile the team can act on.
          </p>
          <Button asChild variant="outline" className="group mt-7">
            <Link href="/about">
              Meet the idea and the team
              <ArrowRight className="transition-transform group-hover:translate-x-0.5" />
            </Link>
          </Button>
        </Reveal>
      </div>
    </section>
  );
}

export function InsightsPreview() {
  return (
    <section className="bg-surface/55 py-20" aria-labelledby="insights-title">
      <div className="mx-auto max-w-6xl px-6 lg:px-10">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <Reveal>
            <p className="text-sm font-semibold uppercase tracking-[.16em] text-brand-600">
              Insights
            </p>
            <h2
              id="insights-title"
              className="mt-3 text-2xl font-bold sm:text-3xl"
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
              Read all insights{" "}
              <ArrowRight className="transition-transform group-hover:translate-x-0.5" />
            </Link>
          </Button>
        </div>
        <RevealStagger className="-mx-6 mt-10 flex snap-x snap-mandatory gap-5 overflow-x-auto px-6 pb-5 [scrollbar-width:thin] lg:mx-0 lg:px-0">
          {INSIGHTS.map((item) => (
            <StaggerItem
              key={item.title}
              className="min-w-[82vw] snap-start sm:min-w-[23rem] lg:min-w-0 lg:flex-1"
            >
              <HoverLift className="h-full rounded-2xl border border-border bg-canvas p-6 shadow-card transition-[border-color,box-shadow] duration-150 hover:border-field hover:shadow-pop">
                <Badge variant="outline">{item.tag}</Badge>
                <h3 className="mt-5 text-lg font-semibold leading-7">
                  {item.title}
                </h3>
                <p className="mt-3 text-sm leading-6">{item.body}</p>
              </HoverLift>
            </StaggerItem>
          ))}
        </RevealStagger>
      </div>
    </section>
  );
}

export function Locations() {
  return (
    <section
      className="mx-auto max-w-6xl px-6 py-16 lg:px-10"
      aria-labelledby="locations-title"
    >
      <Reveal className="rounded-2xl border border-border bg-canvas p-7 shadow-card sm:flex sm:items-center sm:justify-between sm:gap-8">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[.16em] text-brand-600">
            Our locations
          </p>
          <h2 id="locations-title" className="mt-2 text-2xl font-bold">
            Close to the teams we serve
          </h2>
        </div>
        <ul className="mt-6 flex flex-wrap gap-3 sm:mt-0">
          {["Hyderabad", "Visakhapatnam", "Bengaluru", "Chennai"].map(
            (city) => (
              <li
                key={city}
                className="flex items-center gap-2 rounded-full border border-border bg-surface px-4 py-2 text-sm font-medium"
              >
                <MapPin className="h-4 w-4 text-brand-600" aria-hidden="true" />
                {city}
              </li>
            ),
          )}
        </ul>
      </Reveal>
    </section>
  );
}
