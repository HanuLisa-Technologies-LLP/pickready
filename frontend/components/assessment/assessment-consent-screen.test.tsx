// @vitest-environment jsdom
//
// The one consent screen (Appendix B section 1, vivekium feature 6: each item
// consented and timestamped INDIVIDUALLY).
//
// Four properties are pinned. One control per consent item, and no agreement
// until every one of them is ticked. There is one screen for every candidate:
// no mode, no mode-specific title. The rules are NOT repeated here: the
// proctoring shell's next screen shows the server's rules once, and this one
// says that it comes next. And declining records nothing: it is a way back to
// the applications, not a request.
//
// The copy is asserted to come from the props rather than being matched
// verbatim, because the catalogue is the server's and this screen must never
// hold a consent sentence of its own.

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AssessmentConsentScreen,
  CONSENT_ACTION,
  CONSENT_DECLINE,
  CONSENT_NEXT,
  CONSENT_TITLE,
} from "./assessment-consent-screen";
import type { AssessmentConsentTerms } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// Every Stage B item is mandatory, which is why the button waits for all of
// them; the optional registration item lives on the profile form instead.
const ITEMS = [
  {
    key: "job_scoped_assessment_data",
    stage: "B",
    text: "Item one text.",
    version: 1,
    required: true,
  },
  { key: "bgv_portability", stage: "B", text: "Item two text.", version: 1, required: true },
  {
    key: "accuracy_declaration",
    stage: "B",
    text: "Item three text.",
    version: 2,
    required: true,
  },
];

function terms(overrides: Partial<AssessmentConsentTerms> = {}): AssessmentConsentTerms {
  return {
    text: "The consent wording the server configured.",
    consent_version: "2026-09-24",
    privacy_policy_version: "v1",
    terms_version: "v1",
    items: ITEMS,
    ...overrides,
  };
}

function mount(
  props: Partial<React.ComponentProps<typeof AssessmentConsentScreen>> = {}
) {
  render(
    <AssessmentConsentScreen
      terms={terms()}
      busy={false}
      error={null}
      onAccept={vi.fn()}
      {...props}
    />
  );
}

const acceptButton = () =>
  screen.getByRole("button", { name: CONSENT_ACTION }) as HTMLButtonElement;

afterEach(cleanup);

describe("assessment consent screen", () => {
  it("is one screen for every candidate, with the server's text, and says the rules come next", () => {
    mount();
    expect(screen.getByText(CONSENT_TITLE)).toBeTruthy();
    expect(screen.getByText("The consent wording the server configured.")).toBeTruthy();
    expect(screen.getByText(CONSENT_NEXT)).toBeTruthy();
    // Nothing on it offers or names a mode.
    expect(screen.queryByText(/video interview/i)).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
  });

  it("renders one tickable control per item, using the server's words", () => {
    mount();
    expect(screen.getAllByRole("checkbox")).toHaveLength(ITEMS.length);
    for (const item of ITEMS) {
      expect(screen.getByText(item.text)).toBeTruthy();
    }
  });

  it("refuses to accept until every item is ticked", () => {
    const onAccept = vi.fn();
    mount({ onAccept });
    expect(acceptButton().disabled).toBe(true);

    const boxes = screen.getAllByRole("checkbox");
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);
    // Two of three: still refused.
    expect(acceptButton().disabled).toBe(true);

    fireEvent.click(boxes[2]);
    expect(acceptButton().disabled).toBe(false);
    fireEvent.click(acceptButton());
    expect(onAccept).toHaveBeenCalledWith(ITEMS.map((item) => item.key));
  });

  it("un-ticking an item withdraws the agreement again", () => {
    const onAccept = vi.fn();
    mount({ onAccept });
    const boxes = screen.getAllByRole("checkbox");
    boxes.forEach((box) => fireEvent.click(box));
    expect(acceptButton().disabled).toBe(false);

    fireEvent.click(boxes[1]);
    expect(acceptButton().disabled).toBe(true);
    expect(onAccept).not.toHaveBeenCalled();
  });

  it("holds no consent copy of its own", () => {
    mount({ terms: terms({ items: [] }) });
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    for (const item of ITEMS) {
      expect(screen.queryByText(item.text)).toBeNull();
    }
    // And with nothing to agree to, there is nothing to accept.
    expect(acceptButton().disabled).toBe(true);
  });

  it("declines by going back to the applications, recording nothing", () => {
    const onAccept = vi.fn();
    mount({ onAccept });
    const decline = screen.getByRole("link", { name: CONSENT_DECLINE });
    expect(decline.getAttribute("href")).toBe("/portal/applications");
    fireEvent.click(decline);
    expect(onAccept).not.toHaveBeenCalled();
  });
});
