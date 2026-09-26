import Link from "next/link";
import { ArrowRight, ShieldCheck } from "lucide-react";

import { DotPattern } from "@/components/magicui";
import { FadeIn, Pressable, Stagger, StaggerItem } from "@/components/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * The capabilities under the hero. Words, never client names, and never a
 * number.
 *
 * These used to scroll past in a Magic UI marquee. DESIGN.md section 1 is
 * explicit that nothing decorative moves, and an infinite 38 second loop is
 * decoration: it carries no transition and it asks the reader to wait for a
 * word to come round again. The same eight words in a hairline grid say more,
 * hold still, and read at 375px.
 */
const CAPABILITIES = [
  "Resume parsing",
  "Semantic matching",
  "Structured assessment",
  "PRISM Report",
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
      {/*
        Depth comes from STRUCTURE, not from ambient light.

        This block used to hold two large blurred colour fields, one of them
        drifting on an 18 second loop. Both are removed. They were the
        "glowing blob" tell, they broke DESIGN.md's "nothing decorative
        moves", and a hero that glows undercuts the one claim the product
        makes, which is that it is precise. What is left is a dot lattice
        under a radial mask: a measured grid, stationary, hidden from
        assistive tech.
      */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <DotPattern
          width={22}
          height={22}
          cr={1}
          className="text-ink/20 [mask-image:radial-gradient(60rem_36rem_at_50%_0%,#000,transparent)]"
        />
      </div>

      <div className="relative mx-auto max-w-6xl px-6 pb-16 pt-14 sm:pt-20 lg:px-10 lg:pb-24">
        <div className="grid items-center gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-14">
          <FadeIn className="max-w-2xl">
            {/* Master directive section 0: no 3D model or brand mark may sit
                over or above the hero headline. The headline leads; the brand
                mark lives in the site header only.

                WHO the product is for, said before the promise rather than
                left to be inferred from it. The sparkle icon that used to sit
                here is gone: an AI badge with a sparkle on it is the decoration
                every AI product ships, and it competes with the sentence. */}
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600">
              For in-house hiring teams and recruitment partners
            </p>

            {/* The product tagline, set by the client. It is the h1: it is the
                promise the whole page then evidences, not a decoration above
                one. Fraunces (font-display) is the one place the product gets
                display type, per DESIGN.md section 3. */}
            <h1
              id="landing-title"
              className="mt-5 text-balance font-display text-[2.125rem] font-semibold leading-[1.06] tracking-[-0.02em] text-navy-600 sm:text-[2.75rem] lg:text-[3.375rem]"
            >
              Know Every Candidate Before You Meet Them
            </h1>

            <p className="mt-6 max-w-xl text-pretty text-lg leading-8">
              Vivekium reads every applicant against the role, runs a
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

            <p className="mt-7 flex items-start gap-2 text-sm">
              <ShieldCheck
                className="mt-0.5 h-4 w-4 shrink-0 text-teal-700"
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

      {/* The capability rail. `gap-px` over a border-coloured parent draws the
          hairlines, so the grid is one shared rule rather than eight boxes.
          Two columns at 375px, four from `sm`. */}
      <div className="relative border-y border-border bg-surface/50">
        <div className="mx-auto max-w-6xl px-6 py-7 lg:px-10">
          <p
            id="hero-capabilities-label"
            className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-600"
          >
            In the product today
          </p>
          <ul
            aria-labelledby="hero-capabilities-label"
            className="mt-5 grid grid-cols-2 gap-px border border-border bg-border sm:grid-cols-4"
          >
            {CAPABILITIES.map((item) => (
              <li
                key={item}
                className="bg-canvas px-4 py-3 text-xs font-medium"
              >
                {item}
              </li>
            ))}
          </ul>
        </div>
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
      {/* `shadow-hero` is DESIGN.md's level 3, and it is documented as
          existing for this one surface. Every other card on the page stays at
          level 0, a border and nothing else. */}
      <div className="relative overflow-hidden border border-border bg-surface shadow-hero">
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">
              Senior Data Engineer
            </p>
            {/* No `opacity-*` on text anywhere in this file. Hierarchy is
                size and weight; DESIGN.md section 3 keeps every text token at
                full ink. */}
            <p className="text-xs">Applicants ranked by fit</p>
          </div>
          <Badge variant="brand">Live</Badge>
        </div>

        <Stagger as="ul" className="divide-y divide-border" delay={0.15}>
          {PANEL_ROWS.map((row) => (
            <StaggerItem as="li" key={row.name}>
              <div className="flex items-center justify-between gap-3 px-5 py-3.5">
                <div className="flex min-w-0 items-center gap-3">
                  {/* Genuinely circular, so `rounded-full` is the right
                      shape here rather than a pill container. */}
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

        <div className="border-t border-border px-5 py-4 text-xs font-medium">
          Rated in words, never in numbers.
        </div>
      </div>
    </div>
  );
}
