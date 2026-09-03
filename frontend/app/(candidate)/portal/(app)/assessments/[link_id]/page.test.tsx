// @vitest-environment jsdom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AssessmentProgress,
  AssessmentSteps,
} from "@/components/assessment-progress";
import { ProctoringProvider } from "@/components/proctoring/proctoring-context";
import type { ProctoringBridge } from "@/lib/assessment/contracts";
import UnifiedAssessmentPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ link_id: "link-1" }),
}));

/**
 * The shell, stubbed to its ONE structural promise: it renders the assessment
 * inside a `ProctoringProvider` and never beside it.
 *
 * Stubbed because the real shell opens a camera, a microphone and two
 * inference workers before it will mount anything, none of which exists in
 * jsdom. Its own screens and gates are covered directly in
 * `components/proctoring/proctoring-shell.test.tsx`; what is left for a
 * page-level test is the wiring, which is exactly what the page is.
 */
vi.mock("@/components/proctoring/proctoring-shell", () => ({
  ProctoringShell: ({ linkId, children }: { linkId: string; children: React.ReactNode }) => (
    <div data-testid="proctoring-shell" data-link-id={linkId}>
      <ProctoringProvider value={STUB_BRIDGE}>{children}</ProctoringProvider>
    </div>
  ),
}));

vi.mock("@/components/assessment/assessment-conversation", () => ({
  AssessmentConversation: ({ linkId }: { linkId: string }) => (
    <p data-testid="assessment-conversation">Conversation for {linkId}</p>
  ),
}));

const STUB_BRIDGE: ProctoringBridge = {
  status: "active",
  sessionId: "ps-1",
  warningsUsed: 0,
  maxWarnings: 3,
  endedMessage: null,
  fieldHooksFor: () => ({
    onFieldFocus: vi.fn(),
    onFieldBlur: vi.fn(),
    onKeyDown: vi.fn(),
    onBlockedAction: vi.fn(),
    onOptionClick: vi.fn(),
    onScroll: vi.fn(),
  }),
  collectAnswerBehaviour: () => null,
  consumePausedMs: () => 0,
  onConversationEnded: vi.fn(),
};

afterEach(cleanup);

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

describe("the assessment page", () => {
  it("mounts the conversation INSIDE the proctoring shell, never beside it", () => {
    // Proctoring is mandatory (spec principle P4). A page that rendered the
    // conversation outside the shell would be an unmonitored assessment, and
    // it would look identical on screen, so containment is what is asserted
    // rather than mere presence.
    render(<UnifiedAssessmentPage />);
    const shell = screen.getByTestId("proctoring-shell");
    const conversation = screen.getByTestId("assessment-conversation");
    expect(shell.contains(conversation)).toBe(true);
  });

  it("hands both halves the same application", () => {
    render(<UnifiedAssessmentPage />);
    expect(screen.getByTestId("proctoring-shell").getAttribute("data-link-id")).toBe("link-1");
    expect(screen.getByText("Conversation for link-1")).toBeTruthy();
  });
});
