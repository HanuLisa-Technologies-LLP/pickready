// @vitest-environment jsdom
//
// The recruiter's view of one exchange per format (assessment spec 7).

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { installCodeMirrorDomShims } from "@/lib/assessment/jsdom-shims";

const { apiGet, toast } = vi.hoisted(() => ({ apiGet: vi.fn(), toast: vi.fn() }));

vi.mock("@/lib/api", () => ({ apiGet }));
// ONE toast function for the whole file. The modal's fetch callback depends on
// `toast`, exactly as the real hook supplies it (a `useCallback` on the
// provider, stable across renders), so a mock that minted a new function per
// render would re-fire the fetch on every render and spin.
vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast }),
}));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

import {
  AssessmentTranscriptModal,
  acceptedPerBlank,
  correctOptionIds,
  type Transcript,
  type TranscriptAnswerDetail,
  type TranscriptExchange,
} from "./assessment-transcript";

beforeAll(installCodeMirrorDomShims);
afterEach(() => {
  cleanup();
  apiGet.mockReset();
});

function detail(overrides: Partial<TranscriptAnswerDetail>): TranscriptAnswerDetail {
  return {
    payload: {},
    answer: {},
    answer_key: {},
    correctness: null,
    blank_results: [],
    evaluation_reasoning: null,
    evaluation_citations: [],
    not_executed_note: null,
    time_spent: "about two minutes",
    ...overrides,
  };
}

const EXCHANGES: TranscriptExchange[] = [
  {
    ordinal: 1,
    domain: "must_have",
    question: "You list the ledger migration on your resume. What did you own?",
    answer: "I owned the cutover plan and the rollback.",
    criterion: "Data migration",
    follow_up: false,
    asked_at: null,
    question_type: "evidence_based",
    resume_anchor: "Led migration of the payments ledger to Postgres (2024)",
    detail: detail({
      evaluation_reasoning: "The answer names a concrete rollback step.",
      evaluation_citations: ["the rollback"],
      time_spent: "about five minutes",
    }),
  },
  {
    ordinal: 2,
    domain: "must_have",
    question: "Which change most reduces the join cost?",
    answer: "Drop the foreign key",
    criterion: "SQL",
    follow_up: false,
    asked_at: null,
    question_type: "mcq_single",
    detail: detail({
      payload: {
        options: [
          { id: "a", text: "Index the join column" },
          { id: "b", text: "Drop the foreign key" },
          { id: "c", text: "Batch the writes" },
        ],
        select_count: 1,
      },
      answer: { selected_option_id: "b" },
      answer_key: { correct_option_ids: ["a"] },
      correctness: "incorrect",
    }),
  },
  {
    ordinal: 3,
    domain: "nice_to_have",
    question: "Complete the sentence.",
    answer: "Postgres reclaims dead tuples with vacuum and stores them in the heap.",
    criterion: "Postgres",
    follow_up: false,
    asked_at: null,
    question_type: "fill_blank",
    detail: detail({
      payload: { template: "Postgres reclaims dead tuples with ___ and stores them in the ___." },
      answer: { values: ["vacuuming", ""] },
      answer_key: { accepted: [["vacuum", "VACUUM"], ["heap"]] },
      correctness: "partially_correct",
      blank_results: ["equivalent", "not_answered"],
    }),
  },
  {
    ordinal: 4,
    domain: "must_have",
    question: "Write a function that dedupes the list.",
    answer: "Code submitted in Python, 2 lines.",
    criterion: "Python",
    follow_up: false,
    asked_at: null,
    question_type: "coding",
    detail: detail({
      payload: { language: "python" },
      answer: { language: "python", code: "def dedupe(items):\n    return list(dict.fromkeys(items))" },
      evaluation_reasoning: "The approach appears to preserve order and remove duplicates.",
      not_executed_note: "This code was read, not run; the judgement is about how it appears.",
      time_spent: "about eight minutes",
    }),
  },
];

function transcript(exchanges: TranscriptExchange[]): Transcript {
  return {
    job_candidate_link_id: "link-1",
    candidate_name: "Asha Rao",
    job_title: "Platform Engineer",
    status: "completed",
    completed_at: "2026-09-02T00:00:00Z",
    exchanges,
    total: exchanges.length,
    limit: 100,
    offset: 0,
  };
}

function mountWith(exchanges: TranscriptExchange[]) {
  apiGet.mockResolvedValue(transcript(exchanges));
  return render(
    <AssessmentTranscriptModal
      open
      onOpenChange={() => undefined}
      linkId="link-1"
      candidateName="Asha Rao"
      jobTitle="Platform Engineer"
    />
  );
}

describe("per-format rendering", () => {
  it("shows what an evidence question was probing, prominently, with the time phrase", async () => {
    mountWith([EXCHANGES[0]]);
    const anchor = await screen.findByTestId("resume-anchor");
    expect(anchor.textContent).toContain("What was being probed");
    expect(anchor.textContent).toContain("Led migration of the payments ledger");
    expect(screen.getByTestId("time-spent").textContent).toBe("Time spent: about five minutes");
    expect(screen.getByTestId("evaluation-reasoning").textContent).toContain(
      "names a concrete rollback step"
    );
    expect(screen.getByText("Evidence-based")).toBeTruthy();
  });

  it("marks an MCQ's chosen and correct options in words", async () => {
    mountWith([EXCHANGES[1]]);
    const mcq = await screen.findByTestId("mcq-detail");
    expect(mcq.textContent).toContain("Not correct");
    expect(screen.getByText("Chosen, not correct")).toBeTruthy();
    expect(screen.getByText("Correct answer, not chosen")).toBeTruthy();
    expect(screen.queryByText("Chosen, and correct")).toBeNull();
  });

  it("puts a fill-blank's input beside what was accepted, per blank", async () => {
    mountWith([EXCHANGES[2]]);
    const blank = await screen.findByTestId("fill-blank-detail");
    expect(blank.textContent).toContain("Partially correct");
    expect(blank.textContent).toContain('Blank one: typed "vacuuming"; accepted: vacuum, VACUUM');
    expect(blank.textContent).toContain("Accepted as equivalent");
    expect(blank.textContent).toContain("Blank two: typed nothing; accepted: heap");
    expect(blank.textContent).toContain("Left blank");
  });

  it("shows code read-only and highlighted, with the reasoning and the not-executed note", async () => {
    mountWith([EXCHANGES[3]]);
    const coding = await screen.findByTestId("coding-detail");
    const editor = coding.querySelector('[data-testid="code-editor"]') as HTMLElement;
    expect(editor.getAttribute("data-readonly")).toBe("true");
    expect(editor.querySelector(".cm-content")?.getAttribute("contenteditable")).toBe("false");
    expect(editor.textContent).toContain("dict.fromkeys");
    expect(screen.getByTestId("not-executed-note").textContent).toContain("Not executed.");
    expect(screen.getByTestId("not-executed-note").textContent).toContain("read, not run");
    expect(screen.getByTestId("evaluation-reasoning").textContent).toContain("appears to preserve order");
  });

  it("carries no number, score or percentage in the words the product writes", async () => {
    mountWith(EXCHANGES);
    await screen.findByTestId("resume-anchor");

    // WHAT THIS TEST IS AND IS NOT ASSERTING. Three kinds of digit reach this
    // screen legitimately, and none of them is a score: the candidate's own
    // words quoted verbatim (a resume anchor naming a year, an answer, the
    // code itself), the line numbers in the code gutter, which are a reading
    // coordinate exactly as the radar chart's band index is, and the
    // navigation counts the transcript has always shown. So the sweep is over
    // what the PRODUCT writes: strip the quoted regions and the editor, and
    // nothing numeric may remain.
    const body = document.body.cloneNode(true) as HTMLElement;
    body.querySelectorAll('[data-testid="code-editor"]').forEach((node) => node.remove());
    const verbatim = EXCHANGES.flatMap((exchange) => [
      exchange.question,
      exchange.answer,
      exchange.resume_anchor ?? "",
      exchange.detail?.evaluation_reasoning ?? "",
      ...(exchange.detail?.evaluation_citations ?? []),
      ...(Array.isArray(exchange.detail?.answer.values)
        ? (exchange.detail?.answer.values as string[])
        : []),
    ]).filter(Boolean);

    let chrome = body.textContent ?? "";
    for (const quote of verbatim) chrome = chrome.split(quote).join("");
    chrome = chrome
      // Navigation, not a score, and both predate the question formats.
      .replace(/Question \d+/g, "")
      .replace(/Showing \d+ of \d+ answered\./g, "");

    expect(chrome).not.toMatch(/\d/);
    expect(document.body.textContent ?? "").not.toMatch(/%/);
    // Correctness and time are words, which is the property the digit sweep
    // is standing in for.
    expect(screen.getAllByTestId("time-spent").map((node) => node.textContent)).toEqual([
      "Time spent: about five minutes",
      "Time spent: about two minutes",
      "Time spent: about two minutes",
      "Time spent: about eight minutes",
    ]);
  });

  it("still renders a plain exchange that carries no format at all", async () => {
    mountWith([
      {
        ordinal: 5,
        domain: "behavioural",
        question: "How do you handle disagreement?",
        answer: "I ask for the other person's reasoning first.",
        criterion: "Collaboration",
        follow_up: false,
        asked_at: null,
      },
    ]);
    expect(await screen.findByText("I ask for the other person's reasoning first.")).toBeTruthy();
    expect(screen.queryByTestId("resume-anchor")).toBeNull();
  });
});

describe("answer key readers", () => {
  it("reads a single id and a list of ids as the same shape", () => {
    expect(correctOptionIds({ correct_option_id: "a" })).toEqual(["a"]);
    expect(correctOptionIds({ correct_option_ids: ["a", "c"] })).toEqual(["a", "c"]);
    expect(correctOptionIds({})).toEqual([]);
  });

  it("gives every blank a list even when the key is short", () => {
    expect(acceptedPerBlank({ accepted: [["x"]] }, 2)).toEqual([["x"], []]);
  });
});
