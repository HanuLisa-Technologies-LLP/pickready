import type { Metadata } from "next";

import { FeatureCard } from "./feature-card";
import { Hero } from "./hero";
import { LandingTelemetry } from "./landing-telemetry";

export const metadata: Metadata = {
  title: "PickReady — Recruitment Redefined",
  description:
    "Recruitment operations with databank matching, verified candidates, and accountable approvals.",
};

const features = [
  {
    title: "Databank Matching",
    description: "Surface consented candidate profiles for each ratified role.",
  },
  {
    title: "Verified Candidates",
    description: "Bring resumes, structured responses, and employer verification together.",
  },
  {
    title: "Multi-Level Approvals",
    description: "Move jobs through configured approval levels with a complete audit trail.",
  },
  {
    title: "Real-Time Tracking",
    description: "Keep sourcing, review, interview, and candidate status work visible to the team.",
  },
];

export default function LandingPage() {
  return (
    <>
      <LandingTelemetry />
      <main>
        <Hero />
        <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6 lg:px-8" aria-labelledby="features-title">
          <div className="max-w-2xl">
            <h2 id="features-title" className="text-2xl font-bold tracking-tight">
              Built for accountable hiring
            </h2>
            <p className="mt-3 text-muted-foreground">
              One clear process from job approval through candidate review.
            </p>
          </div>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {features.map((feature) => (
              <FeatureCard key={feature.title} {...feature} />
            ))}
          </div>
        </section>
      </main>
      <footer className="border-t">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
          <p>© {new Date().getFullYear()} PickReady. All rights reserved.</p>
          <div className="flex gap-4">
            <a href="#privacy" className="underline-offset-4 hover:underline">Privacy</a>
            <a href="#terms" className="underline-offset-4 hover:underline">Terms</a>
            <span>Powered by PickReady</span>
          </div>
        </div>
      </footer>
    </>
  );
}
