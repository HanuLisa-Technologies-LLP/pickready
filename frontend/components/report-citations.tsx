"use client";

// What a PRISM Report remark rests on (PLAN-p5 WP5-F).
//
// Clicking a rated remark opens the evidence it cites: the question the
// candidate was asked, the answer they gave, or a passage the evaluation read.
// The server resolves the report's stored citation trail at READ time and
// sends words and the candidate's own text only: no id, no locator, no
// position, no number. This component renders what it is given and never
// derives a citation of its own, because a citation the client invented
// would read as provenance and be none.
//
// The fetch happens on the FIRST click, once per report open, and never
// before: every read of the citations is an audited read of the candidate's
// answers, so opening the report is not the same event as reading the
// evidence behind it and must not be recorded as one.
//
// Teal is the one colour in the system with a meaning, evidence, and this is
// exactly that meaning: a teal rule marks the candidate's cited words.

import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export interface CitationEvidence {
  /** What kind of evidence this is, in words ("The candidate's answer"). */
  kind: string;
  /** The question an answer was given to, when the trail pairs one. */
  question?: string | null;
  /** The candidate's own words or the passage read, capped by the server.
   *  Null when the cited record no longer exists. */
  excerpt?: string | null;
}

export interface CitationStatement {
  /** The report section key (`must_have`, `nice_to_have`, `behavioural`, ...). */
  section: string;
  /** The skill the statement is about. */
  item: string;
  /** `finding` for a remark, `grade` for a grade line. */
  kind: string;
  text: string;
  /** The server's words-only marker when the support is weak. */
  support?: string | null;
  evidence: CitationEvidence[];
}

export interface ReportCitations {
  /** False for a report written before the citation trail existed, which is
   *  a different answer from a trail with nothing in it. */
  trail_available: boolean;
  statements: CitationStatement[];
}

export type CitationsLoader = () => Promise<ReportCitations>;

export const TRAIL_UNAVAILABLE =
  "This report was written before its evidence trail was recorded, so this remark cannot be traced to individual answers.";
export const NO_CITED_EVIDENCE = "No cited evidence is recorded for this remark.";
export const EVIDENCE_GONE = "The cited record is no longer available.";
export const EVIDENCE_LOAD_FAILED = "The evidence behind this remark could not be loaded.";

/** The remark statement for one rated row, if the trail holds one. */
export function remarkStatement(
  citations: ReportCitations,
  section: string,
  item: string
): CitationStatement | undefined {
  return citations.statements.find(
    (statement) =>
      statement.section === section && statement.item === item && statement.kind === "finding"
  );
}

type LoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; citations: ReportCitations }
  | { status: "error"; message: string };

/**
 * A rated row's remark, as a disclosure onto the evidence it cites.
 *
 * Without a loader (the PDF preview, a context with no citations route) it is
 * the plain remark and nothing pretends to be clickable.
 */
export function CitedRemark({
  remark,
  section,
  item,
  load,
}: {
  remark: string;
  section: string;
  item: string;
  load?: CitationsLoader;
}) {
  const [open, setOpen] = React.useState(false);
  const [state, setState] = React.useState<LoadState>({ status: "idle" });
  const panelId = React.useId();

  if (!load || !remark) {
    return <p className="text-sm">{remark}</p>;
  }

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && state.status === "idle") {
      setState({ status: "loading" });
      load()
        .then((citations) => setState({ status: "ready", citations }))
        .catch((error: unknown) =>
          setState({
            status: "error",
            message: error instanceof Error ? error.message : EVIDENCE_LOAD_FAILED,
          })
        );
    }
  };

  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={panelId}
        className="group flex w-full items-start gap-1.5 rounded-sm text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
      >
        <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span className="underline decoration-teal-600 decoration-dotted underline-offset-4 group-hover:decoration-solid">
          {remark}
        </span>
        <span className="sr-only"> Show the evidence this remark rests on.</span>
      </button>
      {open ? (
        <div id={panelId} className="mt-3 border-l-2 border-teal-600 pl-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">
            What this rests on
          </p>
          <CitationPanel state={state} section={section} item={item} />
        </div>
      ) : null}
    </div>
  );
}

function CitationPanel({
  state,
  section,
  item,
}: {
  state: LoadState;
  section: string;
  item: string;
}) {
  if (state.status === "idle" || state.status === "loading") {
    return <p className="text-sm">Loading the evidence.</p>;
  }
  if (state.status === "error") {
    return (
      <p className="text-sm" role="alert">
        {EVIDENCE_LOAD_FAILED}
        {state.message !== EVIDENCE_LOAD_FAILED ? ` ${state.message}` : null}
      </p>
    );
  }
  const { citations } = state;
  if (!citations.trail_available) {
    return <p className="text-sm">{TRAIL_UNAVAILABLE}</p>;
  }
  const statement = remarkStatement(citations, section, item);
  if (!statement || statement.evidence.length === 0) {
    return <p className="text-sm">{NO_CITED_EVIDENCE}</p>;
  }
  return (
    <div className="space-y-3">
      {statement.support ? <p className="text-xs">{statement.support}</p> : null}
      <ul className="space-y-3">
        {statement.evidence.map((entry, index) => (
          <li key={`${entry.kind}-${index}`} className="text-sm">
            <p className="text-xs font-medium text-teal-700 dark:text-teal-300">{entry.kind}</p>
            {entry.question ? <p className="mt-1">Asked: {entry.question}</p> : null}
            {entry.excerpt ? (
              <blockquote className="mt-1 italic leading-6">
                &ldquo;{entry.excerpt}&rdquo;
              </blockquote>
            ) : entry.question ? null : (
              <p className="mt-1">{EVIDENCE_GONE}</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
