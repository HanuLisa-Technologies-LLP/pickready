import type { Metadata } from "next";
import { BrainCircuit, Handshake, History, UsersRound } from "lucide-react";

import { FadeIn, Stagger, StaggerItem } from "@/components/motion";

export const metadata: Metadata = {
  title: "About",
  description:
    "The experience, philosophy and people behind ReadyPick's evidence-led candidate profiles.",
};

const PRINCIPLES = [
  {
    icon: BrainCircuit,
    title: "AI for leverage",
    body: "Discovery, matching and assessment should remove repetitive work while keeping the evidence visible.",
  },
  {
    icon: UsersRound,
    title: "Human validation",
    body: "Every profile deserves a final human check before it becomes a customer decision input.",
  },
  {
    icon: Handshake,
    title: "Aligned commercial model",
    body: "A flat job subscription keeps our incentive on profile quality, not on a percentage of compensation.",
  },
] as const;

export default function AboutPage() {
  return (
    <main id="main">
      <section className="relative overflow-hidden border-b border-border py-20 lg:py-28">
        <div aria-hidden="true" className="absolute -top-40 left-1/2 h-[32rem] w-[48rem] -translate-x-1/2 rounded-full bg-brand-600/15 blur-[120px]" />
        <FadeIn className="relative mx-auto max-w-4xl px-6 text-center lg:px-10">
          <p className="text-sm font-semibold uppercase tracking-[.18em] text-brand-600">About ReadyPick</p>
          <h1 className="mt-5 text-balance text-4xl font-bold leading-tight sm:text-5xl">
            Built from inside HR, for the decisions HR has to defend
          </h1>
          <p className="mx-auto mt-6 max-w-3xl text-pretty text-lg leading-8">
            ReadyPick is the next chapter of a long operating journey: learning what teams need when sourcing, screening, validation and decision support have to work as one.
          </p>
        </FadeIn>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-28">
        <div className="grid gap-12 lg:grid-cols-[.9fr_1.1fr] lg:items-start">
          <FadeIn className="rounded-3xl border border-border bg-surface p-8 shadow-card">
            <History className="h-8 w-8 text-brand-600" aria-hidden="true" />
            <p className="mt-7 text-sm font-semibold uppercase tracking-[.16em] text-brand-600">The evolution</p>
            <h2 className="mt-3 text-2xl font-bold">Built for its time. Rebuilt for this one.</h2>
          </FadeIn>
          <FadeIn delay={0.08} className="space-y-5 text-pretty text-base leading-8">
            <p>
              Before ReadyPick, Recruitrix.ai brought profiles, assessments, verification and delivery into one platform when many teams were still assembling those pieces separately. Its remote-ready model supported more than 60 customers across India, delivered more than 10,000 jobs and led to an acquisition.
            </p>
            <p>
              The market moved. AI matured, candidate expectations changed and people teams needed more control over how evidence becomes a decision. ReadyPick takes the practical lessons from that journey and rebuilds the operating model from first principles.
            </p>
            <p>
              ReadyPick turns role requirements, candidate evidence and structured conversation into one clear assessment trail, so teams can spend interview time on the questions that matter.
            </p>
          </FadeIn>
        </div>
      </section>

      <section className="border-y border-border bg-surface/55 py-20">
        <div className="mx-auto max-w-6xl px-6 lg:px-10">
          <FadeIn className="max-w-2xl">
            <p className="text-sm font-semibold uppercase tracking-[.16em] text-brand-600">How we work</p>
            <h2 className="mt-3 text-3xl font-bold">Lean by design, accountable by default</h2>
            <p className="mt-5 text-lg leading-8">
              A small, high-leverage team combines AI-driven discovery and screening with dedicated human validation on every profile. The structure is deliberate: enough process for consistency, without layers that slow a customer down.
            </p>
          </FadeIn>
          <Stagger className="mt-10 grid gap-5 md:grid-cols-3">
            {PRINCIPLES.map((principle) => (
              <StaggerItem key={principle.title}>
                <article className="h-full rounded-2xl border border-border bg-canvas p-6 shadow-card">
                  <principle.icon className="h-6 w-6 text-brand-600" aria-hidden="true" />
                  <h3 className="mt-5 text-lg font-semibold">{principle.title}</h3>
                  <p className="mt-3 text-sm leading-6">{principle.body}</p>
                </article>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-28">
        <div className="grid items-center gap-10 lg:grid-cols-[.85fr_1.15fr]">
          <FadeIn className="relative aspect-[4/3] overflow-hidden rounded-3xl bg-[#090b16] text-white shadow-pop">
            <div aria-hidden="true" className="absolute inset-0 bg-[radial-gradient(circle_at_70%_10%,rgba(124,58,237,.45),transparent_45%)]" />
            <div className="absolute inset-0 grid place-items-center">
              <span className="grid h-36 w-36 place-items-center rounded-full border border-white/15 bg-white/[.06] text-7xl font-black text-violet-200">M</span>
            </div>
            <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent p-7">
              <p className="font-semibold">Manjunath</p>
              <p className="mt-1 text-sm text-white/60">Founder &amp; CEO · HR StraTech Leader</p>
              <p className="mt-3 text-sm font-medium text-violet-200">Built by HR. For HR.</p>
            </div>
          </FadeIn>
          <FadeIn delay={0.08}>
            <p className="text-sm font-semibold uppercase tracking-[.16em] text-brand-600">Founder</p>
            <h2 className="mt-3 text-3xl font-bold">The problem was lived before it was coded</h2>
            <div className="mt-6 space-y-5 text-pretty leading-8">
              <p>
                Manjunath spent more than 25 years in HR - as a practitioner, transformation leader and builder of the platform he believed the function was missing.
              </p>
              <p>
                He reviewed more than 600 HR technology platforms, advised over 50 companies on where their people processes break and mentored more than 100 HR professionals. Those conversations shaped a simple standard: technology should give time back, not add another system to manage.
              </p>
              <p>
                ReadyPick is not a side project. It is the operating belief that teams deserve clear evidence, candidates deserve clarity and the final decision must stay human.
              </p>
            </div>
          </FadeIn>
        </div>
      </section>
    </main>
  );
}
