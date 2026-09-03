// The pure answer helpers every format and the player agree through.

import { describe, expect, it } from "vitest";

import {
  answerLine,
  emptyAnswerFor,
  fillTemplate,
  isAnswerComplete,
  turnIsProse,
  turnKeyFor,
} from "./answers";
import type { FillBlankPayloadView, QuestionOut } from "./contracts";
import { timeAllocationPhrase } from "./time-guidance";

const MCQ: QuestionOut = {
  id: "q-mcq",
  question_type: "mcq_multi",
  payload: {
    options: [
      { id: "a", text: "Index the join column" },
      { id: "b", text: "Drop the foreign key" },
      { id: "c", text: "Batch the writes" },
    ],
    select_count: 2,
  },
  time_allocation_seconds: 90,
};

const BLANK: QuestionOut = {
  id: "q-blank",
  question_type: "fill_blank",
  payload: {
    template: "Postgres stores row versions under ___ and reclaims them with ___.",
    blanks: [
      { index: 0, case_sensitive: false, expected_length: 4 },
      { index: 1, case_sensitive: false, expected_length: 6 },
    ],
  },
  time_allocation_seconds: 60,
};

const CODING: QuestionOut = {
  id: "q-code",
  question_type: "coding",
  payload: {
    language: "python",
    language_options: ["python", "go"],
    starter_code: "def solve(items):\n    pass\n",
    constraints: "",
  },
  time_allocation_seconds: 600,
};

describe("emptiness and completeness", () => {
  it("starts every format from its own empty shape", () => {
    expect(emptyAnswerFor(MCQ)).toEqual({ selected_option_ids: [] });
    expect(emptyAnswerFor(BLANK)).toEqual({ values: ["", ""] });
    expect(emptyAnswerFor(CODING)).toEqual({
      language: "python",
      code: "def solve(items):\n    pass\n",
    });
  });

  it("does not count untouched starter code as an answer", () => {
    // The starter is the question's, not the candidate's. Sending it back
    // would be an empty submission dressed as code.
    expect(isAnswerComplete(emptyAnswerFor(CODING), "def solve(items):\n    pass\n")).toBe(false);
    expect(isAnswerComplete({ language: "python", code: "def solve(items):\n    return []\n" })).toBe(true);
  });

  it("accepts a fill-blank with one blank filled and one left", () => {
    expect(isAnswerComplete({ values: ["", "vacuum"] })).toBe(true);
    expect(isAnswerComplete({ values: ["  ", ""] })).toBe(false);
  });

  it("refuses blank prose and an unselected radio", () => {
    expect(isAnswerComplete({ text: "   " })).toBe(false);
    expect(isAnswerComplete({ selected_option_id: "" })).toBe(false);
  });
});

describe("the readable line", () => {
  it("quotes chosen options by their text, never by id", () => {
    const line = answerLine(MCQ, { selected_option_ids: ["a", "c"] });
    expect(line).toBe("Index the join column; Batch the writes");
    expect(line).not.toContain("a;");
  });

  it("writes the filled sentence, keeping the marker where a blank was left", () => {
    expect(fillTemplate((BLANK.payload as FillBlankPayloadView).template, ["heap", ""])).toBe(
      "Postgres stores row versions under heap and reclaims them with ___."
    );
  });

  it("summarises code by language and length without quoting it", () => {
    expect(answerLine(CODING, { language: "go", code: "package main\nfunc main() {}\n" })).toBe(
      "Code submitted in Go, 3 lines."
    );
  });
});

describe("turn keys", () => {
  it("keys a base question by its id and prose turns by their position", () => {
    expect(turnKeyFor(MCQ, 3, false)).toBe("q-mcq");
    expect(turnKeyFor(null, 3, false)).toBe("prose:3:follow-up");
    expect(turnKeyFor(null, 3, true)).toBe("prose:3:reask");
  });

  it("treats a follow-up and both text formats as prose", () => {
    expect(turnIsProse(null)).toBe(true);
    expect(turnIsProse({ ...MCQ, question_type: "evidence_based" })).toBe(true);
    expect(turnIsProse(MCQ)).toBe(false);
  });
});

describe("time guidance", () => {
  it("is a phrase in words, never a digit", () => {
    expect(timeAllocationPhrase(240)).toBe("about four minutes");
    expect(timeAllocationPhrase(60)).toBe("about a minute");
    expect(timeAllocationPhrase(90)).toBe("about a minute and a half");
    expect(timeAllocationPhrase(20)).toBe("about half a minute");
    expect(timeAllocationPhrase(150)).toBe("about two and a half minutes");
    expect(timeAllocationPhrase(600)).not.toMatch(/\d/);
  });
});
