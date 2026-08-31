import Link from "next/link";
import { ArrowRight, ShieldCheck, Sparkles } from "lucide-react";

import { DotPattern, Marquee } from "@/components/magicui";
import { FadeIn, Pressable, Stagger, StaggerItem } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/** The capabilities that scroll under the hero. Words, never client names. */
const CAPABILITIES = [
  "Resume parsing",
  "Semantic matching",
  "Structured assessment",
  "PPI Assessment Report",
  "Ten stage pipeline",
  "Interview probes",
  "Candidate databank",
  "Compliance vault",
];

export function Hero() {
  return (
    <section
      className="relative overflow-hidden"
      aria-labelledby="landing-title"
    >
      {/* Ambient brand light. Decorative, so it is hidden from assistive tech. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <div className="absolute -top-40 left-1/2 h-[38rem] w-[38rem] -translate-x-1/2 rounded-full bg-brand-600/20 blur-[120px] motion-safe:animate-aurora-drift" />
        <div className="absolute -right-24 top-40 h-80 w-80 rounded-full bg-brand-500/15 blur-[100px]" />
        <DotPattern
          width={22}
          height={22}
          cr={1}
          className="text-ink/20 [mask-image:radial-gradient(60rem_36rem_at_50%_0%,#000,transparent)]"
        />
      </div>

      <div className="relative mx-auto max-w-6xl px-6 pb-20 pt-16 sm:pt-24 lg:px-10 lg:pb-28">
        <div className="grid items-center gap-14 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <FadeIn className="max-w-2xl">
            {/* Master directive §0: no 3D model or brand mark may sit over or
                above the hero headline. The headline leads; the brand mark
                lives in the site header only. */}
            <Badge
              variant="brand"
              className="gap-1.5 px-3 py-1 text-xs font-semibold"
            >
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              AI hiring, built on evidence
            </Badge>

            {/* The product tagline, set by the client. It is the h1: it is the
                promise the whole page then evidences, not a decoration above
                one. */}
            <h1
              id="landing-title"
              className="mt-6 text-balance text-3xl font-bold leading-[1.08] sm:text-4xl lg:text-[3.25rem] lg:leading-[1.05]"
            >
              Know Every Candidate{" "}
              <span className="text-gradient-brand">Before You Meet Them</span>
            </h1>

            <p className="mt-6 max-w-xl text-pretty text-lg leading-8">
              ReadyPick reads every applicant against the role, runs a
              structured assessment built from the job itself, and hands your
              team one readable report per candidate. Plain language, no scores
              to argue about.
            </p>

            <div className="mt-9 flex flex-col gap-3 sm:flex-row sm:items-center">
              <Pressable>
                <Button asChild size="xl" className="group">
                  <Link href="/register?role=candidate">
                    Get started
                    <ArrowRight
                      className="transition-transform duration-150 group-hover:translate-x-0.5"
                      aria-hidden="true"
                    />
                  </Link>
                </Button>
              </Pressable>
              <Pressable>
                <Button asChild size="xl" variant="outline">
                  <Link href="/login?initial_context=all">Log in</Link>
                </Button>
              </Pressable>
            </div>

            <p className="mt-6 flex items-start gap-2 text-sm">
              <ShieldCheck
                className="mt-0.5 h-4 w-4 shrink-0 text-brand-600"
                aria-hidden="true"
              />
              Candidate data stays inside your workspace, with an audit trail on
              every action.
            </p>
          </FadeIn>

          <FadeIn delay={0.08} className="relative">
            <HeroPanel />
          </FadeIn>
        </div>
      </div>

      <div className="relative border-y border-border/70 bg-surface/40 py-4">
        <Marquee pauseOnHover className="[--duration:38s] [--gap:2.5rem]">
          {CAPABILITIES.map((item) => (
            <span
              key={item}
              className="whitespace-nowrap text-sm font-medium tracking-tight opacity-70"
            >
              {item}
            </span>
          ))}
        </Marquee>
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 w-16 bg-gradient-to-r from-canvas to-transparent"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 w-16 bg-gradient-to-l from-canvas to-transparent"
        />
      </div>
    </section>
  );
}

/**
 * The hero's product panel: a stylised candidate list. It is illustrative, so
 * every value is a word label, exactly as the real product renders it. No
 * number appears here, by design.
 */
const PANEL_ROWS = [
  { name: "Priya N.", rating: "Highly Matching", tone: "rating1" },
  { name: "Daniel A.", rating: "Matching", tone: "rating2" },
  { name: "Sofia R.", rating: "Matching", tone: "rating2" },
  { name: "Tomas K.", rating: "Moderately Matching", tone: "rating3" },
] as const;

function HeroPanel() {
  return (
    <div className="relative mx-auto w-full max-w-md lg:max-w-none">
      <div className="relative overflow-hidden border border-border bg-surface shadow-card">
        <div className="flex items-center justify-between gap-3 border-b border-border/70 px-5 py-4">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">
              Senior Data Engineer
            </p>
            <p className="text-xs opacity-70">Applicants ranked by fit</p>
          </div>
          <Badge variant="brand">Live</Badge>
        </div>

        <Stagger as="ul" className="divide-y divide-border/70" delay={0.15}>
          {PANEL_ROWS.map((row) => (
            <StaggerItem as="li" key={row.name}>
              <div className="flex items-center justify-between gap-3 px-5 py-3.5">
                <div className="flex min-w-0 items-center gap-3">
                  <span
                    aria-hidden="true"
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-100 text-xs font-bold text-accent-foreground"
                  >
                    {row.name.slice(0, 1)}
                  </span>
                  <span className="truncate text-sm font-medium">
                    {row.name}
                  </span>
                </div>
                <Badge variant={row.tone} className="shrink-0">
                  {row.rating}
                </Badge>
              </div>
            </StaggerItem>
          ))}
        </Stagger>

        <div className="border-t border-border/70 px-5 py-4 text-xs opacity-70">
          Rated in words, never in numbers.
        </div>
      </div>
    </div>
  );
}
