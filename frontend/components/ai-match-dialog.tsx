"use client";

// The AI Match details for one candidate, opened from the Details button in
// the job page's candidate table.
//
// WHAT THIS REPLACED. `ai-rating-report-modal.tsx` rendered five fixed
// sections (Skills Match, Experience Relevance, Role & Responsibility,
// Education & Qualification, Overall), each a grade and a model-written
// remark. That was the retired matcher's shape: four weighted parameters and a
// paragraph of prose per parameter. The resume check that replaced it (Yukti)
// writes EVIDENCE TAGS instead, each one a skill the resume shows or a
// Must-have it does not, checked against the resume's own text before it is
// stored, plus the server's sentences saying where the grade came from.
//
// WHAT IS DELIBERATELY ABSENT. The check is built from fixed internal parts,
// and a recruiter never sees them: not their names, not their order, not their
// share of anything. Showing the parts would invite reading the grade as the
// sum of numbers that are not on the screen, which is the question the word
// grade exists to stop somebody asking. Every line here is either a grade
// WORD, a tag, or a server-written sentence rendered verbatim.
//
// NO NUMBERS and NO ARITHMETIC. Nothing in this file maps a number to a word,
// counts tags, or reorders anything the server sent.

import * as React from "react";

import type { RankedCandidate } from "@/lib/types";
import { EvidenceTags } from "@/components/evidence-tags";
import { RatingLabel } from "@/components/rating-label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function AiMatchDialog({
  row,
  open,
  onOpenChange,
}: {
  row: RankedCandidate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!row) return null;

  // Grouped by polarity, each group in the server's own order. The server
  // already sends positives first; grouping here is presentation (two headed
  // lists) and never a re-ranking within a group.
  const positives = row.evidence_tags.filter((tag) => tag.polarity === "positive");
  const negatives = row.evidence_tags.filter((tag) => tag.polarity === "negative");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] w-[min(96vw,720px)] max-w-[720px] overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="flex flex-wrap items-center gap-3 text-lg">
            <span>AI Match</span>
            <span className="font-normal">{row.full_name}</span>
            {row.ai_match_label ? (
              <RatingLabel label={row.ai_match_label} />
            ) : row.ai_match_status_word ? (
              <span className="text-sm font-medium">{row.ai_match_status_word}</span>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            The evidence behind this grade and where the grade came from.
          </DialogDescription>
          {row.reference_code ? (
            <p
              data-reference-code
              className="select-all font-mono text-[11px] tracking-wider"
            >
              {row.reference_code}
            </p>
          ) : null}
        </DialogHeader>

        <div className="max-h-[calc(88vh-6rem)] space-y-6 overflow-y-auto px-6 py-5">
          <section aria-labelledby="ai-match-evidence">
            <h3 id="ai-match-evidence" className="mb-2 text-sm font-semibold">
              Evidence from the resume
            </h3>
            {positives.length || negatives.length ? (
              <div className="space-y-3">
                {positives.length ? (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide">
                      Shown in the resume
                    </h4>
                    <EvidenceTags tags={positives} />
                  </div>
                ) : null}
                {negatives.length ? (
                  <div>
                    <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide">
                      Must-have skills not shown
                    </h4>
                    <EvidenceTags tags={negatives} />
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="text-sm">No evidence tags for this candidate.</p>
            )}
          </section>

          <section aria-labelledby="ai-match-provenance">
            <h3 id="ai-match-provenance" className="mb-2 text-sm font-semibold">
              Where this grade came from
            </h3>
            {row.provenance.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {row.provenance.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm">No further detail was recorded.</p>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
