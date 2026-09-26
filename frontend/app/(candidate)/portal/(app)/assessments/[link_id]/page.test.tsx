// @vitest-environment jsdom

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AssessmentProgress,
  AssessmentSteps,
} from "@/components/assessment-progress";
import { ProctoringProvider } from "@/components/proctoring/proctoring-context";
import type { ProctoringBridge } from "@/lib/assessment/contracts";
import AssessmentPage from "./page";

const { apiGet, apiPost } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ link_id: "link-1" }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
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

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiGet, apiPost };
});

const CONSENT = "/api/v2/assessments/conversations/links/link-1/consent";
const ITEMS = [
  { key: "job_scoped_assessment_data", stage: "B", text: "Item one.", version: 1, required: true },
  { key: "accuracy_declaration", stage: "B", text: "Item two.", version: 1, required: true },
];

function consentState(consented: boolean) {
  return {
    consented,
    consent: {
      text: "Consent text from the server.",
      consent_version: "2026-09-24",
      privacy_policy_version: "v1",
      terms_version: "v1",
      items: ITEMS,
    },
  };
}

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
  onConversationEnded: vi.fn(),
};

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});
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
  it("mounts the conversation INSIDE the proctoring shell, never beside it", async () => {
    // Proctoring is mandatory. A page that rendered the conversation outside
    // the shell would be an unmonitored assessment, and it would look
    // identical on screen, so containment is what is asserted.
    apiGet.mockResolvedValue(consentState(true));
    render(<AssessmentPage />);
    const shell = await screen.findByTestId("proctoring-shell");
    const conversation = await screen.findByTestId("assessment-conversation");
    expect(shell.contains(conversation)).toBe(true);
    expect(shell.getAttribute("data-link-id")).toBe("link-1");
  });

  it("asks for consent, never for a mode, before anything is monitored", async () => {
    apiGet.mockImplementation(async (path: string) => {
      if (path === CONSENT) return consentState(false);
      throw new Error(`unexpected GET ${path}`);
    });
    render(<AssessmentPage />);
    await screen.findByText("Consent text from the server.");
    // The consent route is the only thing read: the retired mode routes are
    // never asked, and the rules are the shell's next screen, not this one.
    expect(apiGet.mock.calls.map(([path]) => path)).toEqual([CONSENT]);
    expect(screen.queryByTestId("proctoring-shell")).toBeNull();
  });

  it("posts each ticked item and only then opens the shell", async () => {
    apiGet.mockResolvedValue(consentState(false));
    apiPost.mockResolvedValue(consentState(true));
    render(<AssessmentPage />);
    await screen.findByText("Consent text from the server.");
    for (const box of screen.getAllByRole("checkbox")) fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: "I agree to each of these" }));

    await screen.findByTestId("proctoring-shell");
    expect(apiPost).toHaveBeenCalledWith(CONSENT, {
      consent_keys: ITEMS.map((item) => item.key),
    });
  });
});
