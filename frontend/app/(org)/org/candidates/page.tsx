import type { Metadata } from "next";

import { CandidateDashboard } from "@/components/candidate-dashboard/candidate-dashboard";
import { PageHeader } from "@/components/app-shell";

/**
 * The Candidate Dashboard, at `/org/candidates`.
 *
 * A NEW surface rather than a replacement. The job page's inline candidate
 * table answers "who applied to THIS job", which is what a recruiter opens a
 * job to see. This answers "what should I do next", across every job the
 * caller may see, which is a different question and the one somebody signs in
 * to ask. Both are kept.
 */
export const metadata: Metadata = {
  // No site name here. `app/layout.tsx` sets a `%s | ReadyPick` template, so
  // repeating it would render it twice.
  title: "Candidates",
  description:
    "Every candidate you can act on, with their pre-screen signal, Ready Pick Score, evidence and stage.",
};

export default function CandidatesPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Candidates"
        description="Sort by Ready Pick Score, open the evidence, record your own read, and move people forward."
      />
      <CandidateDashboard />
    </div>
  );
}
