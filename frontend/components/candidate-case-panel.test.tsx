// @vitest-environment jsdom
//
// The candidate case panel is the new and only mount of the recruiter's
// messages, background verification and project evidence panels, after the
// orphaned HR review page and its profile component were deleted (vivekium
// release, Phase 6). What is worth pinning is exactly what the panel owns:
// that each tab hands the SAME candidate id to the panel behind it, and that
// switching candidates cannot leave the previous person's tab open.

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/candidate-conversation-card", () => ({
  CandidateConversationCard: ({
    candidateId,
    candidateName,
  }: {
    candidateId: string;
    candidateName?: string | null;
  }) => (
    <div data-testid="messages">
      messages:{candidateId}:{candidateName ?? ""}
    </div>
  ),
}));
vi.mock("@/components/bgv-verification-panel", () => ({
  BgvVerificationPanel: ({ candidateId }: { candidateId: string }) => (
    <div data-testid="bgv-verification">verification:{candidateId}</div>
  ),
}));
vi.mock("@/components/bgv-results-panel", () => ({
  BgvResultsPanel: ({ candidateId }: { candidateId: string }) => (
    <div data-testid="bgv-results">results:{candidateId}</div>
  ),
}));
vi.mock("@/components/project-evidence-panel", () => ({
  ProjectEvidencePanel: ({ candidateId }: { candidateId: string }) => (
    <div data-testid="projects">projects:{candidateId}</div>
  ),
}));

import { CandidateCasePanel } from "./candidate-case-panel";

afterEach(() => cleanup());

/** Radix tabs activate on mouse down with the primary button. */
function openTab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), { button: 0 });
}

describe("CandidateCasePanel", () => {
  it("opens on the messages thread for the candidate it was given", () => {
    render(<CandidateCasePanel candidateId="cand-1" candidateName="Asha" />);
    expect(screen.getByTestId("messages").textContent).toBe(
      "messages:cand-1:Asha"
    );
    // Lazily mounted: nothing else has fetched yet.
    expect(screen.queryByTestId("bgv-verification")).toBeNull();
    expect(screen.queryByTestId("projects")).toBeNull();
  });

  it("hands the same candidate id to both verification panels", () => {
    render(<CandidateCasePanel candidateId="cand-1" />);
    openTab("Background verification");
    expect(screen.getByTestId("bgv-verification").textContent).toBe(
      "verification:cand-1"
    );
    expect(screen.getByTestId("bgv-results").textContent).toBe(
      "results:cand-1"
    );
  });

  it("shows project evidence on its own tab", () => {
    render(<CandidateCasePanel candidateId="cand-1" />);
    openTab("Projects");
    expect(screen.getByTestId("projects").textContent).toBe("projects:cand-1");
  });

  it("returns to the requested tab when the candidate changes", () => {
    const { rerender } = render(<CandidateCasePanel candidateId="cand-1" />);
    openTab("Background verification");
    expect(screen.getByTestId("bgv-verification")).toBeTruthy();

    rerender(<CandidateCasePanel candidateId="cand-2" />);
    expect(screen.queryByTestId("bgv-verification")).toBeNull();
    expect(screen.getByTestId("messages").textContent).toBe("messages:cand-2:");
  });
});
