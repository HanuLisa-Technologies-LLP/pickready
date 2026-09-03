// @vitest-environment jsdom
//
// The player around the renderer: autosave that survives a remount, the
// respond body per turn kind, termination and completion through the bridge.

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProctoringProvider } from "@/components/proctoring/proctoring-context";
import { AUTOSAVE_DEBOUNCE_MS, draftKey } from "@/lib/assessment/autosave";
import type {
  AnswerBehaviour,
  ConversationTurn,
  ProctoringBridge,
  ProctoringFieldHooks,
} from "@/lib/assessment/contracts";

const { apiPost, apiPatch, toast } = vi.hoisted(() => ({
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ apiPost, apiPatch }));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { full_name: "Asha Rao" } }),
}));
// ONE toast function for the whole file. The player's start effect depends on
// `toast`, so a mock that minted a new function per render would re-fire the
// start request on every render and the test would be measuring the mock.
vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast }),
}));
vi.mock("@/components/app-shell", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import { AssessmentConversation } from "./assessment-conversation";

const LINK = "link-1";
const START = `/api/v2/assessments/conversations/links/${LINK}/start`;
const RESPOND = "/api/v2/assessments/conversations/conv-1/respond";

const BEHAVIOUR: AnswerBehaviour = {
  keydown_offsets_ms: [10, 40],
  backspace_offsets_ms: [],
  blocked_action_count: 0,
  focus_ms: 900,
  mouse_samples: 3,
  mouse_path_px: 40,
  mouse_idle_ms: 0,
  mouse_clicks: 1,
  option_click_offsets_ms: [],
  scroll_events: 0,
};

/** The bridge the shell would provide, with every method a mock. Typed
 *  generics on `vi.fn` rather than a bare one, so the fake is checked against
 *  the real contract: a bridge method whose signature drifts fails here
 *  rather than passing a test that no longer describes the shell. */
function fakeBridge(): ProctoringBridge & { hooks: ProctoringFieldHooks } {
  const hooks: ProctoringFieldHooks = {
    onFieldFocus: vi.fn<() => void>(),
    onFieldBlur: vi.fn<() => void>(),
    onKeyDown: vi.fn<(timeStampMs: number, isDeletion: boolean) => void>(),
    onBlockedAction: vi.fn<() => void>(),
    onOptionClick: vi.fn<(timeStampMs: number) => void>(),
    onScroll: vi.fn<() => void>(),
  };
  return {
    status: "active",
    sessionId: "ps-1",
    warningsUsed: 0,
    maxWarnings: 3,
    endedMessage: null,
    hooks,
    fieldHooksFor: vi.fn<(questionKey: string) => ProctoringFieldHooks>(() => hooks),
    collectAnswerBehaviour: vi.fn<(questionKey: string) => AnswerBehaviour | null>(
      () => BEHAVIOUR
    ),
    consumePausedMs: vi.fn<() => number>(() => 1234),
    onConversationEnded: vi.fn<(status: "completed" | "terminated") => void>(),
  };
}

function turn(overrides: Partial<ConversationTurn>): ConversationTurn {
  return {
    conversation_id: "conv-1",
    status: "active",
    prompt: "Tell us about the migration.",
    progress_label: "Question 1 of 12",
    answered_questions: 0,
    total_questions: 12,
    is_reask: false,
    question: {
      id: "q-1",
      question_type: "short_answer",
      payload: {},
      time_allocation_seconds: 240,
    },
    ...overrides,
  };
}

function mount(bridge: ProctoringBridge) {
  return render(
    <ProctoringProvider value={bridge}>
      <AssessmentConversation linkId={LINK} />
    </ProctoringProvider>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  apiPost.mockReset();
  apiPatch.mockReset();
});
afterEach(cleanup);

describe("autosave", () => {
  it("keeps the draft on this device and restores it after a remount", async () => {
    apiPost.mockResolvedValue(turn({}));
    const bridge = fakeBridge();
    const first = mount(bridge);
    const box = (await screen.findByLabelText("Your answer")) as HTMLTextAreaElement;
    expect(screen.getByTestId("time-guidance").textContent).toContain("about four minutes");

    fireEvent.change(box, { target: { value: "We moved the ledger in three cuts" } });
    expect(screen.getByRole("status", { name: "" }).getAttribute("data-autosave")).toBe("saving");
    await waitFor(
      () =>
        expect(window.localStorage.getItem(draftKey(LINK, "q-1"))).toBe(
          JSON.stringify({ text: "We moved the ledger in three cuts" })
        ),
      { timeout: AUTOSAVE_DEBOUNCE_MS * 5 }
    );
    expect(screen.getByText("Draft saved on this device")).toBeTruthy();

    first.unmount();
    mount(fakeBridge());
    const restored = (await screen.findByLabelText("Your answer")) as HTMLTextAreaElement;
    expect(restored.value).toBe("We moved the ledger in three cuts");
  });

  it("opens exactly one capture per turn through the bridge", async () => {
    apiPost.mockResolvedValue(turn({}));
    const bridge = fakeBridge();
    mount(bridge);
    await screen.findByLabelText("Your answer");
    expect(bridge.fieldHooksFor).toHaveBeenCalledTimes(1);
    expect(bridge.fieldHooksFor).toHaveBeenCalledWith("q-1");
  });
});

describe("respond", () => {
  it("sends a structured answer as answer_payload with the bridge's capture", async () => {
    apiPost.mockResolvedValueOnce(
      turn({
        question: {
          id: "q-mcq",
          question_type: "mcq_single",
          payload: {
            options: [
              { id: "a", text: "Index the join column" },
              { id: "b", text: "Drop the foreign key" },
              { id: "c", text: "Batch the writes" },
            ],
            select_count: 1,
          },
          time_allocation_seconds: 60,
        },
      })
    );
    apiPost.mockResolvedValueOnce(
      turn({
        prompt: "Next question",
        progress_label: "Question 2 of 12",
        answered_questions: 1,
        answer_message_id: "msg-1",
        answer_line: "Chose: Drop the foreign key",
      })
    );
    const bridge = fakeBridge();
    mount(bridge);
    fireEvent.click(await screen.findByLabelText("Drop the foreign key"));
    fireEvent.click(screen.getByRole("button", { name: /^Send$/ }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    expect(apiPost).toHaveBeenLastCalledWith(RESPOND, {
      answer: "",
      answer_payload: { selected_option_id: "b" },
      paused_ms: 1234,
      behaviour: BEHAVIOUR,
    });
    expect(bridge.collectAnswerBehaviour).toHaveBeenCalledWith("q-mcq");
    // The bubble adopts the server's readable line once it arrives.
    await waitFor(() =>
      expect(screen.getByTestId("answer-bubble").textContent).toBe("Chose: Drop the foreign key")
    );
    // A structured answer was scored on submission; it is not re-opened.
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(window.localStorage.getItem(draftKey(LINK, "q-mcq"))).toBeNull();
  });

  it("sends prose as answer and keeps the edit affordance", async () => {
    apiPost.mockResolvedValueOnce(turn({}));
    apiPost.mockResolvedValueOnce(
      turn({
        prompt: "And what was hardest?",
        question: null,
        answered_questions: 0,
        answer_message_id: "msg-1",
      })
    );
    mount(fakeBridge());
    const box = await screen.findByLabelText("Your answer");
    fireEvent.change(box, { target: { value: "I owned the cutover plan." } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    expect(apiPost).toHaveBeenLastCalledWith(RESPOND, {
      answer: "I owned the cutover plan.",
      paused_ms: 1234,
      behaviour: BEHAVIOUR,
    });
    expect(await screen.findByRole("button", { name: /Edit/ })).toBeTruthy();
    // The follow-up is prose with no question row: no time guidance, a
    // short-answer box.
    expect(screen.queryByTestId("time-guidance")).toBeNull();
    expect(screen.getByLabelText("Your answer")).toBeTruthy();
  });

  it("returns the answer to the field when the send fails", async () => {
    apiPost.mockResolvedValueOnce(turn({}));
    apiPost.mockRejectedValueOnce(new Error("offline"));
    mount(fakeBridge());
    const box = (await screen.findByLabelText("Your answer")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Kept" } });
    fireEvent.click(screen.getByRole("button", { name: /^Send$/ }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect((screen.getByLabelText("Your answer") as HTMLTextAreaElement).value).toBe("Kept")
    );
    expect(screen.queryByTestId("answer-bubble")).toBeNull();
  });
});

describe("ending", () => {
  it("shows the termination message and tells the bridge once", async () => {
    apiPost.mockResolvedValueOnce(turn({}));
    apiPost.mockResolvedValueOnce(
      turn({
        status: "terminated",
        prompt: null,
        question: null,
        termination_message: "The assessment was ended because a second person was seen.",
      })
    );
    const bridge = fakeBridge();
    mount(bridge);
    const box = await screen.findByLabelText("Your answer");
    fireEvent.change(box, { target: { value: "An answer" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Send$/ }));
    });
    expect(
      (await screen.findByTestId("termination-notice")).textContent
    ).toContain("a second person was seen");
    expect(bridge.onConversationEnded).toHaveBeenCalledTimes(1);
    expect(bridge.onConversationEnded).toHaveBeenCalledWith("terminated");
    expect(screen.queryByLabelText("Your answer")).toBeNull();
  });

  it("tells the bridge the conversation completed", async () => {
    apiPost.mockResolvedValue(
      turn({ status: "completed", prompt: null, question: null, answered_questions: 12 })
    );
    const bridge = fakeBridge();
    mount(bridge);
    await screen.findByText("Assessment complete");
    expect(bridge.onConversationEnded).toHaveBeenCalledWith("completed");
    expect(bridge.fieldHooksFor).not.toHaveBeenCalled();
  });
});
