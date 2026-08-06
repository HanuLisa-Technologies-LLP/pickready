"use client";

// What a candidate was actually asked, and what they actually answered.
//
// WHY THIS SCREEN EXISTS
// ----------------------
// A PPI Assessment Report states a grade. This is the evidence behind it, and
// until 2026-08-06 there was no way for a recruiter to see it at all: the
// transcript lived in `assessment_messages` and the only reader was a psql
// session. A recruiter deciding whether to interview someone, and a candidate
// disputing a grade, both need the answers themselves.
//
// It matters more now than it did. Technical questions used to be a preset bank
// a company authored, so "what was asked" was knowable by opening the job. They
// are now written per candidate during the conversation, which is a better
// interview and an unreadable one without this.
//
// WHAT IS DELIBERATELY NOT HERE
// -----------------------------
// No score, no rubric, no required level, no number of any kind. The standing
// rule covers this screen exactly as it covers the report: rated output is one
// of four words, and the rubric is internal scoring machinery. Nothing here
// re-words or summarises an answer either -- a summary of an answer is not
// evidence of what someone said.

import * as React from "react";
import { Loader2, MessageSquare, User } from "lucide-react";

import { apiGet } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface TranscriptExchange {
  ordinal: number;
  domain: string;
  question: string;
  answer: string;
  criterion: string | null;
  follow_up: boolean;
  asked_at: string | null;
}

export interface Transcript {
  job_candidate_link_id: string;
  candidate_name: string | null;
  job_title: string | null;
  status: string;
  completed_at: string | null;
  exchanges: TranscriptExchange[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * One page. The endpoint caps at 200, which comfortably holds the longest
 * interview the product can produce (45 base questions plus up to 15 probes),
 * so the common case is a single fetch and the Load-more control never appears.
 * It exists for the case that is not common yet.
 */
const PAGE = 100;

const STATUS_COPY: Record<string, string> = {
  not_started: "This candidate has not opened their assessment yet.",
  active: "This assessment is still in progress.",
  completed: "Assessment complete.",
};

function Exchange({ exchange }: { exchange: TranscriptExchange }) {
  return (
    <li className="rounded-lg border p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide">
          {exchange.follow_up ? "Follow-up" : `Question ${exchange.ordinal}`}
        </span>
        {exchange.criterion ? (
          // The skill or competency this answer was filed under. This is what
          // makes a transcript readable as evidence rather than as a wall of
          // text: it is the criterion the answer was graded against.
          <Badge variant="outline">{exchange.criterion}</Badge>
        ) : null}
      </div>

      <div className="flex gap-2.5">
        <MessageSquare className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <p className="text-sm font-medium">{exchange.question}</p>
      </div>

      <div className="mt-3 flex gap-2.5 border-t pt-3">
        <User className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        {/* `whitespace-pre-wrap` because a candidate's paragraph breaks are
            part of what they wrote. Collapsing them would be an edit. */}
        <p className="whitespace-pre-wrap text-sm">{exchange.answer}</p>
      </div>
    </li>
  );
}

export function AssessmentTranscriptModal({
  open,
  onOpenChange,
  linkId,
  candidateName,
  jobTitle,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  linkId: string | null;
  candidateName: string;
  jobTitle?: string | null;
}) {
  const { toast } = useToast();
  const [transcript, setTranscript] = React.useState<Transcript | null>(null);
  const [exchanges, setExchanges] = React.useState<TranscriptExchange[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const fetchPage = React.useCallback(
    async (offset: number) => {
      if (!linkId) return;
      setLoading(true);
      setError(null);
      try {
        // Absolute /api/ path: the fetch wrapper routes these past API_BASE
        // (pinned to /api/v1) to the origin, so a v2 route is reachable.
        const res = await apiGet<Transcript>(
          `/api/v2/assessments/transcripts/links/${linkId}?limit=${PAGE}&offset=${offset}`
        );
        setTranscript(res);
        setExchanges((prev) =>
          offset === 0 ? res.exchanges : [...prev, ...res.exchanges]
        );
      } catch (e) {
        const message =
          e instanceof Error ? e.message : "Couldn't load the transcript";
        setError(message);
        toast({
          title: "Transcript unavailable",
          description: message,
          variant: "destructive",
        });
      } finally {
        setLoading(false);
      }
    },
    [linkId, toast]
  );

  React.useEffect(() => {
    if (!open || !linkId) return;
    setTranscript(null);
    setExchanges([]);
    void fetchPage(0);
  }, [open, linkId, fetchPage]);

  const loadedAll = transcript ? exchanges.length >= transcript.total : true;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            Assessment transcript, {candidateName}
            {jobTitle ? ` (${jobTitle})` : ""}
          </DialogTitle>
          <DialogDescription>
            Every question this candidate was asked and every answer they gave,
            exactly as submitted.
          </DialogDescription>
        </DialogHeader>

        {loading && exchanges.length === 0 ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
          </div>
        ) : error ? (
          <p className="py-10 text-center text-sm">{error}</p>
        ) : (
          <div className="space-y-4">
            {transcript ? (
              <p className="text-sm">
                {STATUS_COPY[transcript.status] ?? transcript.status}
                {transcript.total > 0
                  ? ` Showing ${exchanges.length} of ${transcript.total} answered.`
                  : ""}
              </p>
            ) : null}

            {exchanges.length === 0 ? (
              <p className="py-8 text-center text-sm">
                No answers have been recorded for this candidate yet.
              </p>
            ) : (
              <ol className="space-y-3">
                {exchanges.map((exchange) => (
                  <Exchange
                    key={`${exchange.ordinal}-${exchange.follow_up}`}
                    exchange={exchange}
                  />
                ))}
              </ol>
            )}

            {!loadedAll ? (
              <Button
                variant="outline"
                className="w-full"
                disabled={loading}
                onClick={() => void fetchPage(exchanges.length)}
              >
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  "Load more"
                )}
              </Button>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
