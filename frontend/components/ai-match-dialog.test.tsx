// @vitest-environment jsdom
//
// The AI Match details dialog (Vivekium release, PLAN-p2 WP-D).
//
// A recruiter never sees the parts the resume check is built from (D2): not
// their names, not their order, not their share. The dialog shows a grade
// word, the tags (positives first), the server's provenance sentences exactly
// as sent, and the reference code. Nothing else.

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RankedCandidate } from "@/lib/types";

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div role="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

import { AiMatchDialog } from "./ai-match-dialog";

// `backend/app/services/yukti/config.COMPONENTS`, as identifiers and as the
// words a renderer would most likely turn them into.
const COMPONENT_NAMES = [
  "must_have_evidenced",
  "nice_to_have_evidenced",
  "experience_level",
  "role_fit",
  "company_need_fit",
  "validation_fit",
];

// Sentences exactly as `yukti.projection.provenance_words` writes them.
const PROVENANCE = [
  "Tatva Assessment: mostly decides this grade.",
  "Resume check: skills, experience and the role, read from the resume.",
  "Application answers: expected pay within range, notice period within 30 days.",
  "The skills changed after this check. Run AI Matching to refresh.",
];

const ROW = {
  link_id: "link-1",
  full_name: "Asha Rao",
  reference_code: "K7QP-2M4X-9TB1",
  ai_match_status: "scored",
  ai_match_label: "Matching",
  ai_match_status_word: null,
  ai_match_stale: true,
  // Deliberately interleaved: the dialog groups by polarity, positives first,
  // keeping the server's order inside each group.
  evidence_tags: [
    { text: "Python", polarity: "positive", shown_in_row: true },
    { text: "Kubernetes", polarity: "negative", shown_in_row: true },
    { text: "SQL", polarity: "positive", shown_in_row: true },
    { text: "Terraform", polarity: "negative", shown_in_row: false },
  ],
  provenance: PROVENANCE,
} as unknown as RankedCandidate;

afterEach(cleanup);

function renderDialog(row: RankedCandidate | null = ROW) {
  return render(<AiMatchDialog row={row} open={row !== null} onOpenChange={vi.fn()} />);
}

describe("AiMatchDialog", () => {
  it("renders the provenance lines verbatim and in the server's order", () => {
    renderDialog();
    const items = Array.from(
      screen.getByText("Where this grade came from").parentElement!.querySelectorAll("li"),
    ).map((li) => li.textContent);
    expect(items).toEqual(PROVENANCE);
  });

  it("shows every tag, positives first, each group in the server's order", () => {
    const { container } = renderDialog();
    const tags = Array.from(container.querySelectorAll("li[data-polarity]")).map(
      (li) => `${li.getAttribute("data-polarity")}:${li.textContent}`,
    );
    expect(tags).toEqual([
      "positive:Evidenced: Python",
      "positive:Evidenced: SQL",
      "negative:Not evidenced: Kubernetes",
      "negative:Not evidenced: Terraform",
    ]);
  });

  it("names none of the parts the resume check is built from", () => {
    const { container } = renderDialog();
    const text = (container.textContent ?? "").toLowerCase();
    for (const name of COMPONENT_NAMES) {
      expect(text).not.toContain(name);
      expect(text).not.toContain(name.replace(/_/g, " "));
      expect(text).not.toContain(name.replace(/_/g, "-"));
    }
    expect(text).not.toContain("%");
  });

  it("shows the grade word and the reference code", () => {
    renderDialog();
    expect(screen.getByText("Matching")).toBeTruthy();
    expect(screen.getByText("K7QP-2M4X-9TB1")).toBeTruthy();
  });

  it("shows the status word when there is no grade, and says there are no tags", () => {
    renderDialog({
      ...ROW,
      ai_match_status: "not_assessed",
      ai_match_label: null,
      ai_match_status_word: "Not assessed",
      evidence_tags: [],
      provenance: ["No readable resume text."],
    } as RankedCandidate);
    expect(screen.getByText("Not assessed")).toBeTruthy();
    expect(screen.getByText("No evidence tags for this candidate.")).toBeTruthy();
    expect(screen.getByText("No readable resume text.")).toBeTruthy();
  });

  it("renders nothing without a row", () => {
    const { container } = renderDialog(null);
    expect(container.textContent).toBe("");
  });
});
