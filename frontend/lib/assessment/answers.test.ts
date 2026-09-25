// The pure answer helpers every format and the player agree through.

import { describe, expect, it } from "vitest";

import {
  answerLine,
  starterCodeFor,
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

const PY_STARTER = "def solve(items):\n    pass\n";
const JAVA_STARTER = "public class Main {\n    public static void main(String[] a) {}\n}\n";

const CODING: QuestionOut = {
  id: "q-code",
  question_type: "coding",
  payload: {
    payload_version: 2,
    title: "Sum a list",
    io: "stdin_stdout",
    input_format: "One line of integers.",
    output_format: "Their sum.",
    constraints: "",
    languages: ["python", "java"],
    starter_code: { python: PY_STARTER, java: JAVA_STARTER },
    visible_tests: [{ id: "v1", stdin: "1 2\n", expected_stdout: "3\n", explanation: "" }],
    limits: {},
  },
  time_allocation_seconds: 1200,
};

/** A question from before code execution (payload version 1). Old rows
 *  only, but the helpers still read one correctly. */
const LEGACY_CODING: QuestionOut = {
  id: "q-code-v1",
  question_type: "coding",
  payload: {
    language: "go",
    language_options: ["go"],
    starter_code: "package main\n",
    constraints: "",
  },
  time_allocation_seconds: 600,
};

describe("emptiness and completeness", () => {
  it("starts every format from its own empty shape", () => {
    expect(emptyAnswerFor(MCQ)).toEqual({ selected_option_ids: [] });
    expect(emptyAnswerFor(BLANK)).toEqual({ values: ["", ""] });
    // A coding question starts in its FIRST offered language, with that
    // language's starter.
    expect(emptyAnswerFor(CODING)).toEqual({ language: "python", code: PY_STARTER });
  });

  it("does not count untouched starter code as an answer", () => {
    // The starter is the question's, not the candidate's. Sending it back
    // would be an empty submission dressed as code.
    const empty = emptyAnswerFor(CODING);
    expect(isAnswerComplete(empty, starterCodeFor(CODING, empty))).toBe(false);
    expect(isAnswerComplete({ language: "python", code: "def solve(items):\n    return []\n" })).toBe(true);
  });

  it("compares a coding answer with the starter of the language it is IN", () => {
    // Switching to Java and leaving the Java starter untouched is still
    // nothing written, however different it is from the Python starter.
    const untouchedJava = { language: "java", code: JAVA_STARTER };
    expect(starterCodeFor(CODING, untouchedJava)).toBe(JAVA_STARTER);
    expect(isAnswerComplete(untouchedJava, starterCodeFor(CODING, untouchedJava))).toBe(false);
    expect(starterCodeFor(CODING, null)).toBe(PY_STARTER);
    expect(starterCodeFor(MCQ, { selected_option_ids: [] })).toBe("");
    expect(starterCodeFor(null, null)).toBe("");
  });

  it("still reads a version 1 question's single language and starter", () => {
    expect(emptyAnswerFor(LEGACY_CODING)).toEqual({ language: "go", code: "package main\n" });
    expect(starterCodeFor(LEGACY_CODING, null)).toBe("package main\n");
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

  it("summarises code by language and length without quoting it, in words", () => {
    expect(answerLine(CODING, { language: "java", code: JAVA_STARTER })).toBe(
      "Code submitted in Java, four lines."
    );
    expect(answerLine(CODING, { language: "python", code: "print(1)" })).toBe(
      "Code submitted in Python 3, one line."
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
