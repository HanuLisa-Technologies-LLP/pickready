import type { Metadata } from "next";

import { CallToAction } from "./call-to-action";
import { Features } from "./features";
import { Hero } from "./hero";
import { HowItWorks } from "./how-it-works";
import { LandingTelemetry } from "./landing-telemetry";
import { Pricing } from "./pricing";
import { ReportSection } from "./report-section";
import { WorkflowShowcase } from "./workflow-showcase";
import {
  AboutPreview,
  InsightsPreview,
  Locations,
  PfiDifferentiator,
  ProcessRoadmap,
  Testimonials,
} from "./story-sections";

export const metadata: Metadata = {
  // The client's tagline is the page's promise, so it is the title too.
  // `app/layout.tsx` appends "| ReadyPick" via its template, which is why the
  // product name does not appear here (claude.md: page metadata must not
  // repeat the site name).
  title: "Know Every Candidate Before You Meet Them",
  description:
    "Rank every applicant against the role, run a structured AI assessment, and read one clear PRISM Report per candidate. Rated in words, never in numbers.",
};

export default function LandingPage() {
  return (
    <>
      <LandingTelemetry />
      <main id="main">
        <Hero />
        <WorkflowShowcase />
        <HowItWorks />
        <ProcessRoadmap />
        <Features />
        <PfiDifferentiator />
        <ReportSection />
        <Testimonials />
        <Pricing />
        <AboutPreview />
        <InsightsPreview />
        <Locations />
        <CallToAction />
      </main>
    </>
  );
}
