import { CallToAction } from "./call-to-action";
import { Features } from "./features";
import { Hero } from "./hero";
import { HowItWorks } from "./how-it-works";
import { LandingTelemetry } from "./landing-telemetry";
import { Pricing } from "./pricing";
import { ReportSection } from "./report-section";
import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";
import { WorkflowShowcase } from "./workflow-showcase";
import {
  AboutPreview,
  EvidenceProfile,
  InsightsPreview,
} from "./story-sections";

/**
 * The marketing landing page, composed.
 *
 * WHY THIS ORDER. The page is read top to bottom by somebody who has never
 * heard of the product, so each section exists to answer the question the one
 * above it raises:
 *
 *  1. `Hero`            what it is, who it is for, why it matters, what to do
 *                       next, all above the fold.
 *  2. `HowItWorks`      "how would that work for me": three steps, and the
 *                       claim that the middle one is not the reader's job.
 *  3. `Features`        "what do I actually get": the platform, in the
 *                       product today rather than on a roadmap.
 *  4. `ReportSection`   "what lands on my desk at the end": the PRISM Report,
 *                       with a sample rated in words. This is the artifact the
 *                       whole product exists to produce, so it sits at the
 *                       centre of the page rather than at the end.
 *  5. `EvidenceProfile` "why is that better than a score": the thesis, and
 *                       the four word grades that replace the number. It
 *                       follows the report rather than preceding it, because
 *                       the argument is easier to accept once the reader has
 *                       seen the thing being argued about.
 *  6. `AboutPreview`    "who is behind this": the one trust section.
 *  7. `InsightsPreview` "do they think about this seriously": three real
 *                       articles that exist at /insights.
 *  8. `Pricing`         "what does it cost": asked only once the value is
 *                       established, and answered with the real credit rate.
 *  9. `CallToAction`    the close.
 *
 * `WorkflowShowcase` sits between 2 and 3, as the product tour: having read how
 * it works, you watch it work. It was held out of an earlier draft because the
 * animation it embeds printed a percentage next to a rated candidate, and no
 * number reaches a client. That data shape now carries the four grade words, so
 * the section is composed. Its own file keeps the history.
 *
 * NO INVENTED EVIDENCE. There is no statistic, no customer logo, no
 * testimonial and no performance claim anywhere on this page, and adding one
 * needs a source, not a sentence.
 *
 * WHY THE HEADER AND FOOTER ARE HERE RATHER THAN IN A LAYOUT. This component
 * is rendered by `app/page.tsx`, which is at the app root and therefore does
 * not get `app/(public)/layout.tsx`. Two routes cannot both own `/`, so the
 * frame is repeated here deliberately, and `landingLive` is passed as `true`
 * because this component only renders when the landing page is what `/`
 * serves.
 */
export function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col overflow-x-clip bg-canvas text-ink">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:bg-brand-600 focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
      >
        Skip to content
      </a>

      <SiteHeader landingLive />

      <main id="main" className="flex-1 pt-16">
        <Hero />
        <HowItWorks />
        <WorkflowShowcase />
        <Features />
        <ReportSection />
        <EvidenceProfile />
        <AboutPreview />
        <InsightsPreview />
        <Pricing />
        <CallToAction />
      </main>

      <SiteFooter landingLive />
      <LandingTelemetry />
    </div>
  );
}
