"use client";

// The AI Rating & Report viewer (new spec, 2026-07-28).
//
// The job detail table used to print all five rated comments inline, which made
// every row tall enough that a recruiter could see about four candidates at a
// time. The client asked for the opposite: show only the Overall tag in the
// table, behind a button that opens the full reasoning in a large, scrollable
// document, "like a long pdf viewer".
//
// The two rules this file exists to honour, same as the table:
//   * NO NUMBERS. Every rating is a word label handed over by the backend.
//     There is no score, percentage or rank arithmetic anywhere in this file.
//   * The labels are the server's. Nothing here maps a number to a word.

import * as React from "react";
import { FileText } from "lucide-react";

import type { RankedCandidate } from "@/lib/types";
import { RatingLabel } from "@/components/rating-label";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** The five rated dimensions, in the fixed order the spec lists them. */
const SECTIONS = [
  { title: "Skills Match", comment: "skills_match_comment", label: "skills_match_label" },
  { title: "Experience Relevance", comment: "experience_comment", label: "experience_label" },
  { title: "Role & Responsibility", comment: "role_alignment_comment", label: "role_alignment_label" },
  { title: "Education & Qualification", comment: "education_comment", label: "education_label" },
  { title: "Overall", comment: "overall_comment", label: "overall_label" },
] as const;

/**
 * The compact table cell: the Overall tag, plus the button that opens the full
 * reasoning. Deliberately the ONLY rated thing visible in the row.
 */
export function AiRatingCell({
  row,
  onOpen,
}: {
  row: RankedCandidate;
  onOpen: (row: RankedCandidate) => void;
}) {
  if (row.ranking_status !== "ready") {
    return (
      <p className="text-xs">
        Not scored yet. Run AI matching to rate this candidate.
      </p>
    );
  }
  return (
    <div className="flex flex-col items-start gap-2">
      <RatingLabel label={row.overall_label} />
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => onOpen(row)}
      >
        <FileText className="h-3.5 w-3.5" />
        AI Report
      </Button>
    </div>
  );
}

/**
 * The full report. One section per rated dimension, each with its word label
 * and the AI's reasoning, in a tall scrollable body so long remarks read as a
 * document rather than as a cramped table cell.
 */
export function AiRatingReportModal({
  row,
  open,
  onOpenChange,
}: {
  row: RankedCandidate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (!row) return null;

  const sections = SECTIONS.filter(
    (s) => row[s.comment] as string | null
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] w-[min(96vw,880px)] max-w-[880px] overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="flex flex-wrap items-center gap-3 text-lg">
            <span>AI Rating &amp; Report</span>
            <span className="font-normal">{row.full_name}</span>
            <RatingLabel label={row.overall_label} />
          </DialogTitle>
          {/* What this rating was actually derived from. It is the PRE-
              assessment snapshot: resume against job description, produced
              before the candidate has done anything beyond being sourced.
              Saying so on the heading is what stops it being read as a verdict
              on the person -- the assessed grades live in the PRISM Report,
              which is a different document written from a different input. */}
          <DialogDescription>Based on Candidate Resume and JD</DialogDescription>
          {row.reference_code ? (
            <p className="select-all font-mono text-[11px] tracking-wider">
              {row.reference_code}
            </p>
          ) : null}
        </DialogHeader>

        {/* The document body. Fixed max height with its own scroll so the
            dialog chrome stays put on a long report. */}
        <div className="max-h-[calc(88vh-5rem)] overflow-y-auto px-6 py-5">
          {sections.length === 0 ? (
            <p className="py-10 text-center text-sm">
              This candidate has not been rated yet. Run AI matching to generate
              the report.
            </p>
          ) : (
            <article className="space-y-6">
              {sections.map((section) => (
                <section key={section.title}>
                  <h3 className="mb-1.5 flex flex-wrap items-center gap-2 text-sm font-semibold">
                    {section.title}
                    <RatingLabel label={row[section.label] as string | null} />
                  </h3>
                  <p className="text-sm leading-6">
                    {row[section.comment] as string}
                  </p>
                </section>
              ))}
            </article>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
