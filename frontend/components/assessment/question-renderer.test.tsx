// @vitest-environment jsdom
//
// The dispatcher and the six answer components (assessment spec 5, 8).

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { installCodeMirrorDomShims } from "@/lib/assessment/jsdom-shims";
import type {
  AnswerComponentProps,
  AnswerPayload,
  ProctoringFieldHooks,
  QuestionOut,
  QuestionType,
} from "@/lib/assessment/contracts";

import { blankSize } from "./fill-blank-answer";
import { selectionInstruction } from "./mcq-multi-answer";
import { QuestionRenderer } from "./question-renderer";

beforeAll(installCodeMirrorDomShims);
afterEach(cleanup);

function hooks(): ProctoringFieldHooks {
  return {
    onFieldFocus: vi.fn(),
    onFieldBlur: vi.fn(),
    onKeyDown: vi.fn(),
    onBlockedAction: vi.fn(),
    onOptionClick: vi.fn(),
    onScroll: vi.fn(),
  };
}

function question(type: QuestionType, payload: QuestionOut["payload"] = {}): QuestionOut {
  return { id: `q-${type}`, question_type: type, payload, time_allocation_seconds: 120 };
}

const MCQ_OPTIONS = [
  { id: "a", text: "Index the join column" },
  { id: "b", text: "Drop the foreign key" },
  { id: "c", text: "Batch the writes" },
  { id: "d", text: "Disable autovacuum" },
];

function renderQuestion(
  q: QuestionOut,
  overrides: Partial<AnswerComponentProps> = {}
) {
  const fieldHooks = overrides.fieldHooks ?? hooks();
  const onChange = overrides.onChange ?? vi.fn();
  const props: AnswerComponentProps = {
    question: q,
    prompt: "The question as asked",
    value: null,
    onChange,
    disabled: false,
    autosave: "idle",
    fieldHooks,
    onSubmitShortcut: vi.fn(),
    ...overrides,
  };
  const view = render(<QuestionRenderer {...props} />);
  return { ...view, fieldHooks, onChange, props };
}

describe("dispatch", () => {
  it("renders a distinct component for each of the six types", () => {
    const payloads: Record<QuestionType, QuestionOut["payload"]> = {
      evidence_based: {},
      short_answer: {},
      mcq_single: { options: MCQ_OPTIONS, select_count: 1 },
      mcq_multi: { options: MCQ_OPTIONS, select_count: null },
      fill_blank: {
        template: "Use ___ here.",
        blanks: [{ index: 0, case_sensitive: false, expected_length: 5 }],
      },
      coding: { language: "python", language_options: ["python"], starter_code: "", constraints: "" },
    };
    const seen = new Set<string>();
    for (const type of Object.keys(payloads) as QuestionType[]) {
      const { unmount } = renderQuestion(question(type, payloads[type]));
      const host = screen.getByTestId("question-renderer");
      expect(host.getAttribute("data-question-type")).toBe(type);
      seen.add(host.innerHTML.slice(0, 200));
      unmount();
    }
    // Six types, six different renderings: nothing fell through to a
    // default text box.
    expect(seen.size).toBe(6);
  });

  it("shows the evidence guidance only on the evidence format", () => {
    renderQuestion(question("evidence_based"));
    expect(screen.getByText(/what you personally did/i)).toBeTruthy();
    cleanup();
    renderQuestion(question("short_answer"));
    expect(screen.queryByText(/what you personally did/i)).toBeNull();
  });

  it("throws on a type it has no component for, rather than rendering a text box", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    // React re-throws through a DOM event, which jsdom then reports as an
    // uncaught error. The throw is the assertion here, so the report is
    // noise: swallow it for the length of this test only.
    const swallow = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", swallow);
    expect(() =>
      renderQuestion({ ...question("short_answer"), question_type: "essay" as never })
    ).toThrow(/No answer component/);
    window.removeEventListener("error", swallow);
    spy.mockRestore();
  });
});

describe("text fields", () => {
  it("records keystrokes with the deletion flag and blocks paste with a report", () => {
    const { fieldHooks, onChange } = renderQuestion(question("short_answer"));
    const box = screen.getByLabelText("Your answer");
    fireEvent.keyDown(box, { key: "a" });
    fireEvent.keyDown(box, { key: "Backspace" });
    expect(fieldHooks.onKeyDown).toHaveBeenCalledTimes(2);
    expect(vi.mocked(fieldHooks.onKeyDown).mock.calls[0][1]).toBe(false);
    expect(vi.mocked(fieldHooks.onKeyDown).mock.calls[1][1]).toBe(true);

    const paste = fireEvent.paste(box);
    expect(paste).toBe(false);
    expect(fieldHooks.onBlockedAction).toHaveBeenCalledTimes(1);

    fireEvent.change(box, { target: { value: "I led the migration" } });
    expect(onChange).toHaveBeenCalledWith({ text: "I led the migration" });
  });

  it("sends on Ctrl+Enter from the text field", () => {
    const onSubmitShortcut = vi.fn();
    renderQuestion(question("evidence_based"), { onSubmitShortcut });
    fireEvent.keyDown(screen.getByLabelText("Your answer"), { key: "Enter", ctrlKey: true });
    expect(onSubmitShortcut).toHaveBeenCalledTimes(1);
  });
});

describe("MCQ", () => {
  it("multi: ticks accumulate in option order, untick removes, and each click is timed", () => {
    const onChange = vi.fn();
    const { fieldHooks, rerender, props } = renderQuestion(
      question("mcq_multi", { options: MCQ_OPTIONS, select_count: 2 }),
      { onChange, value: { selected_option_ids: [] } }
    );
    expect(screen.getByText("Select two options.")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("Batch the writes"));
    expect(onChange).toHaveBeenLastCalledWith({ selected_option_ids: ["c"] });

    rerender(<QuestionRenderer {...props} value={{ selected_option_ids: ["c"] }} />);
    fireEvent.click(screen.getByLabelText("Index the join column"));
    // "a" was ticked after "c" but the payload lists it first: option order,
    // not click order.
    expect(onChange).toHaveBeenLastCalledWith({ selected_option_ids: ["a", "c"] });

    rerender(<QuestionRenderer {...props} value={{ selected_option_ids: ["a", "c"] }} />);
    fireEvent.click(screen.getByLabelText("Batch the writes"));
    expect(onChange).toHaveBeenLastCalledWith({ selected_option_ids: ["a"] });

    expect(fieldHooks.onOptionClick).toHaveBeenCalledTimes(3);
  });

  it("multi: says select all that apply when the count is open", () => {
    expect(selectionInstruction(null)).toBe("Select all that apply.");
    expect(selectionInstruction(3)).toBe("Select three options.");
  });

  it("refuses a drop onto an option group and counts it against the answer", () => {
    const { fieldHooks } = renderQuestion(
      question("mcq_multi", { options: MCQ_OPTIONS, select_count: 2 }),
      { value: { selected_option_ids: [] } }
    );
    const group = screen.getByRole("group");
    expect(fireEvent.drop(group)).toBe(false);
    expect(fieldHooks.onBlockedAction).toHaveBeenCalledTimes(1);
    fireEvent.scroll(group);
    expect(fieldHooks.onScroll).toHaveBeenCalledTimes(1);
  });

  it("single: one radio, the chosen id in the payload, the click timed", () => {
    const onChange = vi.fn();
    const { fieldHooks } = renderQuestion(
      question("mcq_single", { options: MCQ_OPTIONS, select_count: 1 }),
      { onChange, value: { selected_option_id: "" } }
    );
    expect(screen.getAllByRole("radio")).toHaveLength(4);
    fireEvent.click(screen.getByLabelText("Drop the foreign key"));
    expect(onChange).toHaveBeenLastCalledWith({ selected_option_id: "b" });
    expect(fieldHooks.onOptionClick).toHaveBeenCalledTimes(1);
  });
});

describe("fill in the blank", () => {
  const BLANK = question("fill_blank", {
    template: "Postgres reclaims dead tuples with ___ and stores them in the ___.",
    blanks: [
      { index: 0, case_sensitive: false, expected_length: 6 },
      { index: 1, case_sensitive: true, expected_length: 20 },
    ],
  });

  it("renders one inline input per marker, sized to the expected answer", () => {
    const onChange = vi.fn();
    renderQuestion(BLANK, { onChange, value: { values: ["", ""] } });
    const first = screen.getByLabelText("Blank one") as HTMLInputElement;
    const second = screen.getByLabelText("Blank two") as HTMLInputElement;
    expect(first.size).toBe(blankSize(6));
    expect(second.size).toBe(blankSize(20));
    expect(second.size).toBeGreaterThan(first.size);
    // Inline: the inputs sit inside the sentence's paragraph, not below it.
    expect(first.closest("p")?.textContent).toContain("Postgres reclaims dead tuples with");

    fireEvent.change(second, { target: { value: "heap" } });
    expect(onChange).toHaveBeenLastCalledWith({ values: ["", "heap"] });
    expect(screen.getByText(/Capitalisation matters for blank two/)).toBeTruthy();
  });

  it("clamps the size so a long expected answer cannot overflow a phone", () => {
    expect(blankSize(1)).toBe(4);
    expect(blankSize(200)).toBe(40);
  });
});

describe("coding", () => {
  const CODING = question("coding", {
    language: "python",
    language_options: ["python"],
    starter_code: "def solve(items):\n    pass\n",
    constraints: "Do not sort the input.",
  });

  it("mounts CodeMirror with the starter code, line numbers and no completion", () => {
    renderQuestion(CODING, {
      value: { language: "python", code: "def solve(items):\n    pass\n" },
    });
    const editor = screen.getByTestId("code-editor");
    expect(editor.querySelector(".cm-editor")).toBeTruthy();
    expect(editor.querySelector(".cm-gutter.cm-lineNumbers")).toBeTruthy();
    expect(editor.querySelector(".cm-tooltip-autocomplete")).toBeNull();
    expect(editor.textContent).toContain("def solve(items):");
    expect(screen.getByTestId("language-indicator").textContent).toBe("Python");
    expect(screen.getByText("Do not sort the input.")).toBeTruthy();
  });

  it("refuses paste and drop at the editor and reports each attempt", () => {
    const { fieldHooks } = renderQuestion(CODING, {
      value: { language: "python", code: "" },
    });
    const content = screen.getByTestId("code-editor").querySelector(".cm-content") as HTMLElement;
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    content.dispatchEvent(paste);
    expect(paste.defaultPrevented).toBe(true);

    const drop = new Event("drop", { bubbles: true, cancelable: true });
    content.dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(true);

    expect(fieldHooks.onBlockedAction).toHaveBeenCalledTimes(2);
  });

  it("records keystrokes in the editor and reports the change", () => {
    const onChange = vi.fn();
    const { fieldHooks } = renderQuestion(CODING, {
      onChange,
      value: { language: "python", code: "" },
    });
    const content = screen.getByTestId("code-editor").querySelector(".cm-content") as HTMLElement;
    content.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete", bubbles: true }));
    expect(fieldHooks.onKeyDown).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fieldHooks.onKeyDown).mock.calls[0][1]).toBe(true);
  });

  it("sends on Ctrl+Enter without the editor inserting a line break", () => {
    // The shortcut is caught before the editor sees the key, so the document
    // is untouched: a candidate who sends with the cursor mid-function does
    // not find a stray newline in the code that was submitted.
    const onSubmitShortcut = vi.fn();
    const onChange = vi.fn();
    renderQuestion(CODING, {
      onSubmitShortcut,
      onChange,
      value: { language: "python", code: "def solve(items):\n    pass\n" },
    });
    const content = screen.getByTestId("code-editor").querySelector(".cm-content") as HTMLElement;
    fireEvent.keyDown(content, { key: "Enter", ctrlKey: true });
    expect(onSubmitShortcut).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("offers a language selector only when the question permits more than one", () => {
    renderQuestion(CODING, { value: { language: "python", code: "" } });
    expect(screen.queryByLabelText("Language")).toBeNull();
    cleanup();
    renderQuestion(
      question("coding", {
        language: "python",
        language_options: ["python", "go"],
        starter_code: "",
        constraints: "",
      }),
      { value: { language: "python", code: "" } }
    );
    expect(screen.getByLabelText("Language")).toBeTruthy();
  });
});

describe("autosave indicator contract", () => {
  it("is passed through the props without any component owning storage", () => {
    // The renderer receives the state; storage belongs to the player. A
    // component that wrote localStorage itself would be a second autosave.
    const value: AnswerPayload = { text: "draft" };
    renderQuestion(question("short_answer"), { value, autosave: "saved" });
    expect(window.localStorage.length).toBe(0);
  });
});
