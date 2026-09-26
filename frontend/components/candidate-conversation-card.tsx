"use client";

// The recruiter's thread with one candidate.
//
// WHY THE THREAD IS OPENED BY A POST RATHER THAN LOOKED UP
// `POST /conversations/candidate/{id}` is create-or-return: it answers with the
// one thread this tenant has with this candidate, minting it on first use. A
// GET plus a create-if-missing would race two recruiters opening the same
// candidate at the same moment into two threads, and whoever read the newer one
// would not know the older existed.
//
// The server also refuses a candidate who is not linked to one of this tenant's
// jobs, so an id typed into a URL cannot open a thread with somebody else's
// applicant. That refusal is a 404 here for the same reason it is everywhere
// else: naming the difference would confirm the candidate exists.

import * as React from "react";
import { MessagesSquare } from "lucide-react";

import { ConversationPanel } from "@/components/conversation-panel";
import { openCandidateConversation } from "@/lib/conversations";
import { apiErrorMessage } from "@/lib/validation-errors";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function CandidateConversationCard({
  candidateId,
  candidateName,
}: {
  candidateId: string;
  candidateName?: string | null;
}) {
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const open = React.useCallback(async () => {
    try {
      const conversation = await openCandidateConversation(candidateId);
      setConversationId(conversation.id);
      setError(null);
    } catch (failure) {
      setConversationId(null);
      setError(apiErrorMessage(failure));
    }
  }, [candidateId]);

  React.useEffect(() => {
    void open();
  }, [open]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <MessagesSquare className="h-4 w-4" aria-hidden="true" />
          <CardTitle className="text-base">Messages</CardTitle>
        </div>
        <CardDescription>
          {candidateName
            ? `Your team and ${candidateName}, in one place.`
            : "Your team and this candidate, in one place."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <div className="space-y-2">
            <p className="text-sm">{error}</p>
            <Button size="sm" variant="outline" onClick={() => void open()}>
              Try again
            </Button>
          </div>
        ) : conversationId ? (
          <ConversationPanel
            conversationId={conversationId}
            emptyCopy="No messages yet. Anything you write here reaches the candidate in their portal."
          />
        ) : (
          <p className="text-sm">Opening the thread.</p>
        )}
      </CardContent>
    </Card>
  );
}
