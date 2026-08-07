// @vitest-environment jsdom

import * as React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AssessmentProgress,
  AssessmentSteps,
} from "@/components/assessment-progress";
import UnifiedAssessmentPage from "./page";

const mocks = vi.hoisted(() => ({
  apiPost: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ link_id: "link-1" }),
}));

vi.mock("@/lib/api", () => ({
  apiPost: (...args: unknown[]) => mocks.apiPost(...args),
  apiPatch: vi.fn(),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { full_name: "Karthik Kumar" } }),
}));

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

afterEach(() => {
  cleanup();
  mocks.apiPost.mockReset();
  mocks.toast.mockReset();
  window.localStorage.clear();
});

describe("candidate assessment progress", () => {
  it("renders the exact answered count and circular percentage", () => {
    render(<AssessmentProgress answered={7} total={45} />);

    expect(screen.getByText("Assessment Progress")).toBeTruthy();
    expect(screen.getByText("7 / 45 Questions Answered")).toBeTruthy();
    const progress = screen.getByRole("progressbar", {
      name: "Assessment progress",
    });
    expect(progress.getAttribute("aria-valuenow")).toBe("16");
    expect(progress.getAttribute("style")).toContain("16%");
  });

  it("marks completed, current, and pending stages accessibly", () => {
    render(<AssessmentSteps answered={12} total={45} />);

    expect(screen.getByRole("list", { name: "Assessment stages" })).toBeTruthy();
    expect(document.querySelector('[aria-current="step"]')).toBeTruthy();
    expect(screen.getByText("Start")).toBeTruthy();
    expect(screen.getByText("Complete")).toBeTruthy();
  });
});

describe("candidate assessment conversation controls", () => {
  it("renders the assessor controls and exposes Edit after a saved answer", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    mocks.apiPost.mockImplementation((url: string) => {
      if (url.endsWith("/start")) {
        return Promise.resolve({
          conversation_id: "conversation-1",
          status: "active",
          prompt: "Tell me about a production incident you owned.",
          progress_label: "Question 1 of 45",
          answered_questions: 0,
          total_questions: 45,
          is_reask: false,
        });
      }
      return Promise.resolve({
        conversation_id: "conversation-1",
        status: "active",
        prompt: "What did you change afterward?",
        progress_label: "Question 2 of 45",
        answered_questions: 1,
        total_questions: 45,
        is_reask: false,
        answer_message_id: "message-1",
      });
    });

    render(<UnifiedAssessmentPage />);

    expect(
      await screen.findByText("Tell me about a production incident you owned.")
    ).toBeTruthy();
    expect(screen.getByText("AI Assessor")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Clear" })).toBeTruthy();
    const send = screen.getByRole("button", { name: "Send" });
    expect(send.hasAttribute("disabled")).toBe(true);

    fireEvent.change(screen.getByLabelText("Your answer"), {
      target: {
        value:
          "I owned the rollback, restored service in twelve minutes, and added a release canary.",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
    });
    expect(screen.getByText("1 / 45 Questions Answered")).toBeTruthy();
  });
});
