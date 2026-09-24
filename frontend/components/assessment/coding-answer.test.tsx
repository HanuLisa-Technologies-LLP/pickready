// @vitest-environment jsdom
//
// The coding question: its problem details, one draft per language, and the
// Run button against the visible sample tests (PLAN-p4 section 3.10).
//
// The network is the real client (`lib/api.ts`) over a stubbed `fetch`, so
// what is asserted is the request the server receives and what the candidate
// reads back, not which helper was called.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@monaco-editor/react", async () =>
  (await import("@/lib/assessment/monaco-test-double")).monacoReactModule()
);

// Radix Select cannot be driven under jsdom (it needs pointer capture), so it
// is rendered as the native control it stands for.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    disabled,
    children,
  }: {
    value: string;
    onValueChange: (value: string) => void;
    disabled?: boolean;
    children: React.ReactNode;
  }) => (
    <select
      aria-label="Language"
      value={value}
      disabled={disabled}
      onChange={(event) => onValueChange(event.target.value)}
    >
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children: React.ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

import { CodingConversationContext } from "@/lib/assessment/coding";
import type {
  AnswerPayload,
  CodingPayloadView,
  CodingPayloadViewV2,
  ProctoringFieldHooks,
  QuestionOut,
} from "@/lib/assessment/contracts";
import { resetMonacoDouble } from "@/lib/assessment/monaco-test-double";

import { CodingAnswer } from "./coding-answer";

const PY_STARTER = "import sys\n";
const JAVA_STARTER = "public class Main {\n}\n";

function payload(overrides: Partial<CodingPayloadViewV2> = {}): CodingPayloadViewV2 {
  return {
    payload_version: 2,
    title: "Remove duplicates",
    io: "stdin_stdout",
    input_format: "One line of integers.",
    output_format: "The integers without duplicates, in order.",
    constraints: "Do not sort the input.",
    languages: ["python", "java"],
    starter_code: { python: PY_STARTER, java: JAVA_STARTER },
    visible_tests: [
      { id: "v1", stdin: "3 1 3\n", expected_stdout: "3 1\n", explanation: "The second 3 goes." },
      { id: "v2", stdin: "5\n", expected_stdout: "5\n", explanation: "" },
    ],
    limits: {},
    ...overrides,
  };
}

function question(p: CodingPayloadViewV2 | CodingPayloadView = payload()): QuestionOut {
  return { id: "q-1", question_type: "coding", payload: p, time_allocation_seconds: 1200 };
}

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

/** The player's half: it owns the answer value and the conversation id. */
function Player({ q, initial }: { q: QuestionOut; initial: AnswerPayload | null }) {
  const [value, setValue] = React.useState<AnswerPayload | null>(initial);
  return (
    <CodingConversationContext.Provider value="conv-1">
      <CodingAnswer
        question={q}
        prompt="Remove the duplicates."
        value={value}
        onChange={setValue}
        disabled={false}
        autosave="idle"
        fieldHooks={hooks()}
        onSubmitShortcut={vi.fn()}
      />
      <output data-testid="answer">{JSON.stringify(value)}</output>
    </CodingConversationContext.Provider>
  );
}

function answer(): { language: string; code: string } {
  return JSON.parse(screen.getByTestId("answer").textContent ?? "null");
}

function editor(): HTMLTextAreaElement {
  return screen.getByTestId("monaco-input") as HTMLTextAreaElement;
}

async function mount(
  q: QuestionOut = question(),
  initial: AnswerPayload | null = { language: "python", code: "print(input())\n" }
) {
  const view = render(<Player q={q} initial={initial} />);
  if (q.payload && (q.payload as { payload_version?: number }).payload_version === 2) {
    await waitFor(() => editor());
  }
  return view;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string; body: Record<string, unknown> | null };

function calls(fetchMock: ReturnType<typeof vi.fn>): Call[] {
  return fetchMock.mock.calls.map(([url, init]) => {
    const request = init as RequestInit;
    return {
      url: String(url),
      method: String(request.method),
      body: typeof request.body === "string" ? JSON.parse(request.body) : null,
    };
  });
}

const COMPLETE = {
  run_id: "run-1",
  status: "complete",
  message: null,
  tests: [
    {
      key: "v1",
      passed: true,
      result_word: "Passed",
      stdout: "3 1\n",
      expected_stdout: "3 1\n",
      stderr: "",
      compile_output: "",
    },
    {
      key: "v2",
      passed: false,
      result_word: "Wrong answer",
      stdout: "",
      expected_stdout: "5\n",
      stderr: "Traceback: IndexError",
      compile_output: "",
    },
  ],
};

beforeEach(resetMonacoDouble);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the problem", () => {
  it("shows the title, the formats, the constraints and each sample by position", async () => {
    await mount();
    expect(screen.getByTestId("coding-title").textContent).toBe("Remove duplicates");
    expect(screen.getByText("One line of integers.")).toBeTruthy();
    expect(screen.getByText("Do not sort the input.")).toBeTruthy();
    const samples = screen.getByTestId("coding-samples");
    expect(samples.textContent).toContain("Sample one");
    expect(samples.textContent).toContain("Sample two");
    expect(samples.textContent).toContain("The second 3 goes.");
    // The server's opaque test keys are never shown.
    expect(samples.textContent).not.toMatch(/\bv1\b|\bv2\b/);
  });

  it("names the language and tells the candidate how their program is run", async () => {
    await mount(question(payload({ languages: ["java"], starter_code: { java: JAVA_STARTER } })), {
      language: "java",
      code: JAVA_STARTER,
    });
    expect(screen.getByTestId("language-indicator").textContent).toBe("Java");
    expect(screen.getByTestId("language-hint").textContent).toContain("public class named Main");
  });

  it("says so, rather than offering a Run that can only fail, on a version 1 question", async () => {
    await mount(
      question({ language: "go", language_options: ["go"], starter_code: "", constraints: "" }),
      null
    );
    expect(screen.getByTestId("coding-unsupported").textContent).toContain("older format");
    expect(screen.queryByTestId("coding-run")).toBeNull();
  });
});

describe("one draft per language", () => {
  it("keeps the code of the language being left, and starts a new one from its starter", async () => {
    await mount();
    fireEvent.change(editor(), { target: { value: "print('mine')\n" } });
    expect(answer()).toEqual({ language: "python", code: "print('mine')\n" });

    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "java" } });
    expect(answer()).toEqual({ language: "java", code: JAVA_STARTER });

    fireEvent.change(screen.getByLabelText("Language"), { target: { value: "python" } });
    expect(answer()).toEqual({ language: "python", code: "print('mine')\n" });
  });
});

describe("Run", () => {
  it("starts a run of the code on screen, polls it, and shows each sample's result", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(202, { run_id: "run-1", status: "queued" }))
      .mockResolvedValueOnce(json(200, { run_id: "run-1", status: "queued", tests: [], message: null }))
      .mockResolvedValueOnce(json(200, COMPLETE));
    vi.stubGlobal("fetch", fetchMock);
    await mount();

    fireEvent.click(screen.getByTestId("coding-run"));
    expect(screen.getByTestId("coding-run-results").getAttribute("data-state")).toBe("running");
    // Nothing else can start while a run is in flight.
    expect((screen.getByTestId("coding-run") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Language") as HTMLSelectElement).disabled).toBe(true);

    await waitFor(
      () => expect(screen.getByTestId("coding-run-results").getAttribute("data-state")).toBe("complete"),
      { timeout: 5000 }
    );
    const [post, ...polls] = calls(fetchMock);
    expect(post.url).toBe("/api/v2/assessments/conversations/conv-1/coding/q-1/runs");
    expect(post.method).toBe("POST");
    expect(post.body).toMatchObject({ language: "python", source: "print(input())\n" });
    expect(String(post.body?.client_token)).toMatch(/^[0-9a-f-]{36}$/);
    expect(polls.map((call) => call.url)).toEqual([
      "/api/v2/assessments/conversations/conv-1/coding/q-1/runs/run-1",
      "/api/v2/assessments/conversations/conv-1/coding/q-1/runs/run-1",
    ]);

    const results = screen.getByTestId("coding-run-results");
    expect(screen.getByTestId("coding-run-summary").textContent).toBe(
      "Your code passed one of the two sample tests."
    );
    const rows = screen.getAllByTestId("coding-run-test");
    expect(rows.map((row) => row.getAttribute("data-passed"))).toEqual(["true", "false"]);
    expect(rows[0].textContent).toContain("Sample one");
    expect(rows[1].textContent).toContain("Sample two");
    expect(rows[1].textContent).toContain("Wrong answer");
    expect(rows[1].textContent).toContain("Traceback: IndexError");
    // No figure of any kind reaches the panel.
    expect(results.textContent?.replace(/3 1|5/g, "")).not.toMatch(/\d/);
    expect((screen.getByTestId("coding-run") as HTMLButtonElement).disabled).toBe(false);
  });

  for (const [status, sentence] of [
    [503, "The code runner is not available right now. Your code is kept; you can keep working and submit when ready."],
    [429, "You have run this question as many times as it allows. Your code is kept; submit when ready."],
    [409, "A run is already in progress."],
  ] as const) {
    it(`shows the server's ${status} sentence verbatim`, async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(status, { detail: sentence })));
      await mount();
      fireEvent.click(screen.getByTestId("coding-run"));
      await waitFor(() => expect(screen.getByTestId("coding-run-message").textContent).toBe(sentence));
      // The code is untouched by a failed run.
      expect(answer().code).toBe("print(input())\n");
    });
  }

  it("never shows a status code when the server wrote no sentence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response("upstream", { status: 502 })));
    await mount();
    fireEvent.click(screen.getByTestId("coding-run"));
    await waitFor(() => screen.getByTestId("coding-run-message"));
    expect(screen.getByTestId("coding-run-message").textContent).not.toMatch(/\d/);
  });

  it("shows the server's sentence when the runner could not finish the run", async () => {
    const sentence = "The code runner could not finish this run. Your code is kept.";
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json(202, { run_id: "run-9", status: "queued" }))
        .mockResolvedValueOnce(
          json(200, { run_id: "run-9", status: "unavailable", tests: [], message: sentence })
        )
    );
    await mount();
    fireEvent.click(screen.getByTestId("coding-run"));
    await waitFor(() => expect(screen.getByTestId("coding-run-message").textContent).toBe(sentence), {
      timeout: 5000,
    });
  });

  it("retries a click whose request never arrived with the SAME token, and a new click with a new one", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(json(202, { run_id: "run-1", status: "queued" }))
      .mockResolvedValueOnce(json(200, COMPLETE))
      .mockResolvedValueOnce(json(202, { run_id: "run-2", status: "queued" }))
      .mockResolvedValueOnce(json(200, { ...COMPLETE, run_id: "run-2" }));
    vi.stubGlobal("fetch", fetchMock);
    await mount();

    fireEvent.click(screen.getByTestId("coding-run"));
    await waitFor(() =>
      expect(screen.getByTestId("coding-run-message").textContent).toContain("couldn't reach the server")
    );
    fireEvent.click(screen.getByTestId("coding-run"));
    await waitFor(
      () => expect(screen.getByTestId("coding-run-results").getAttribute("data-state")).toBe("complete"),
      { timeout: 5000 }
    );
    fireEvent.click(screen.getByTestId("coding-run"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5), { timeout: 5000 });

    const posts = calls(fetchMock).filter((call) => call.method === "POST");
    expect(posts).toHaveLength(3);
    const [lost, retry, fresh] = posts.map((call) => call.body?.client_token);
    // The retry of the lost request is the same click; the next press is not,
    // even of identical code, because the server already answered the first.
    expect(retry).toBe(lost);
    expect(fresh).not.toBe(lost);
  });

  it("runs on Ctrl+Enter in the editor", async () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    await mount();
    fireEvent.keyDown(editor(), { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(calls(fetchMock)[0].method).toBe("POST");
  });

  it("offers no Run for an empty editor", async () => {
    await mount(question(), { language: "python", code: "   \n" });
    expect((screen.getByTestId("coding-run") as HTMLButtonElement).disabled).toBe(true);
  });
});
