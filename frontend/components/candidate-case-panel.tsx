"use client";

// The recruiter's follow-up work on one candidate: messages, background
// verification and project evidence, side by side as tabs.
//
// WHY THIS EXISTS (vivekium release, Phase 6 WP6-F)
// These three panels were mounted in exactly one place, `profile-review.tsx`,
// which was mounted in exactly one place, the `/org/review` page, which no
// navigation entry linked to. Deleting that orphaned page without giving the
// panels another home would have taken away the only screen on which a person
// can mark a previous employer verified, and the offer gate in
// `hiring_pipeline.apply_transition` refuses an offer until a person has. So
// the page went and the panels moved here, into one block with no data of its
// own that any candidate surface can embed.
//
// WHY IT OWNS NO DATA
// Every tab is an existing panel that loads, authorizes and refuses for itself
// against the candidate id it is given. A second loader here would be a second
// place deciding what a recruiter may read about a candidate, and the day the
// two disagreed the panel would be the one telling the truth.
//
// WHY THE TABS MOUNT LAZILY
// Radix renders only the active tab's content, so opening a candidate costs
// the Messages thread and nothing else. Background verification and project
// evidence are fetched the first time somebody asks for them, not every time
// somebody glances at a name.

import * as React from "react";

import { BgvResultsPanel } from "@/components/bgv-results-panel";
import { BgvVerificationPanel } from "@/components/bgv-verification-panel";
import { CandidateConversationCard } from "@/components/candidate-conversation-card";
import { ProjectEvidencePanel } from "@/components/project-evidence-panel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export type CandidateCaseTab = "messages" | "verification" | "projects";

export function CandidateCasePanel({
  candidateId,
  candidateName,
  initialTab = "messages",
}: {
  candidateId: string;
  candidateName?: string | null;
  initialTab?: CandidateCaseTab;
}) {
  const [tab, setTab] = React.useState<CandidateCaseTab>(initialTab);

  // A different candidate starts on the tab the caller asked for, never on
  // whichever tab the previous candidate was left on: the messages of one
  // person must not appear to be the first thing shown about the next.
  React.useEffect(() => {
    setTab(initialTab);
  }, [candidateId, initialTab]);

  return (
    <Tabs
      value={tab}
      onValueChange={(value) => setTab(value as CandidateCaseTab)}
      className="w-full"
    >
      <TabsList>
        <TabsTrigger value="messages">Messages</TabsTrigger>
        <TabsTrigger value="verification">Background verification</TabsTrigger>
        <TabsTrigger value="projects">Projects</TabsTrigger>
      </TabsList>

      <TabsContent value="messages" className="mt-4">
        {/* One thread per candidate per customer, opened on first view. */}
        <CandidateConversationCard
          candidateId={candidateId}
          candidateName={candidateName}
        />
      </TabsContent>

      <TabsContent value="verification" className="mt-4 space-y-4">
        {/* Consent-gated on the server: an inquiry the candidate has not
            shared with this employer arrives as a "Not shared by the
            candidate" marker, never as a missing page. */}
        <BgvVerificationPanel candidateId={candidateId} />
        <BgvResultsPanel candidateId={candidateId} />
      </TabsContent>

      <TabsContent value="projects" className="mt-4">
        {/* Derived project evidence only; no original file is retained. */}
        <ProjectEvidencePanel candidateId={candidateId} />
      </TabsContent>
    </Tabs>
  );
}
