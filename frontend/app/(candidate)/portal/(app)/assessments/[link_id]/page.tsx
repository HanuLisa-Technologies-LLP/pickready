"use client";

// The assessment page: the proctoring shell around the conversation.
//
// The shell runs first (consent, then the system check, then the monitoring
// session) and only then mounts the conversation, so there is no moment at
// which a question is on screen without monitoring. Proctoring is mandatory
// and the page has no other branch.

import { useParams } from "next/navigation";

import { AssessmentConversation } from "@/components/assessment/assessment-conversation";
import { ProctoringShell } from "@/components/proctoring/proctoring-shell";

export default function UnifiedAssessmentPage() {
  const { link_id: linkId } = useParams<{ link_id: string }>();
  return (
    <ProctoringShell linkId={linkId}>
      <AssessmentConversation linkId={linkId} />
    </ProctoringShell>
  );
}
