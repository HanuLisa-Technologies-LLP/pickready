// @vitest-environment jsdom
//
// The Stage B consent screen (vivekium feature 6: each item consented and
// timestamped INDIVIDUALLY).
//
// What is being prevented is the shape this screen used to have: the four
// items as a read-only bullet list under one "I agree" button. The rows
// behind it were already one per item, so the record looked right while the
// candidate had performed a single undifferentiated act. These tests assert
// the three properties that make the per-item claim true of the SCREEN as
// well as the table: one control per item, no agreement until every one of
// them is ticked, and the ticked keys travelling with the request.
//
// The copy is asserted to come from the props rather than being matched
// verbatim, because the catalogue is the server's and this screen must never
// hold a sentence of its own.

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssessmentConsentScreen, CONSENT_ACTION } from "./assessment-consent-screen";
import type { AssessmentConsentTerms } from "@/lib/types";

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
    assessment_mode: "conversational",
    text: "The mode wording the server configured.",
    consent_version: "v1",
    privacy_policy_version: "v1",
    terms_version: "v1",
    items: ITEMS,
    ...overrides,
  };
}

afterEach(cleanup);

describe("assessment consent screen", () => {
  it("renders one tickable control per item, using the server's words", () => {
    render(
      <AssessmentConsentScreen
        terms={terms()}
        busy={false}
        error={null}
        onAccept={vi.fn()}
        onDecline={vi.fn()}
      />
    );
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(ITEMS.length);
    for (const item of ITEMS) {
      expect(screen.getByText(item.text)).toBeTruthy();
    }
  });

  it("refuses to accept until every item is ticked", () => {
    const onAccept = vi.fn();
    render(
      <AssessmentConsentScreen
        terms={terms()}
        busy={false}
        error={null}
        onAccept={onAccept}
        onDecline={vi.fn()}
      />
    );
    const accept = screen.getByRole("button", { name: CONSENT_ACTION });
    expect((accept as HTMLButtonElement).disabled).toBe(true);

    const boxes = screen.getAllByRole("checkbox");
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1]);
    // Two of three: still refused, which is the whole point of the change.
    expect((accept as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(boxes[2]);
    expect((accept as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(accept);
    expect(onAccept).toHaveBeenCalledWith(ITEMS.map((item) => item.key));
  });

  it("un-ticking an item withdraws the agreement again", () => {
    const onAccept = vi.fn();
    render(
      <AssessmentConsentScreen
        terms={terms()}
        busy={false}
        error={null}
        onAccept={onAccept}
        onDecline={vi.fn()}
      />
    );
    const boxes = screen.getAllByRole("checkbox");
    boxes.forEach((box) => fireEvent.click(box));
    const accept = screen.getByRole("button", { name: CONSENT_ACTION });
    expect((accept as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(boxes[1]);
    expect((accept as HTMLButtonElement).disabled).toBe(true);
    expect(onAccept).not.toHaveBeenCalled();
  });

  it("holds no consent copy of its own", () => {
    // An empty item list must render no substitute sentences: a screen that
    // fell back to its own words could promise what the record does not say.
    render(
      <AssessmentConsentScreen
        terms={terms({ items: [] })}
        busy={false}
        error={null}
        onAccept={vi.fn()}
        onDecline={vi.fn()}
      />
    );
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    for (const item of ITEMS) {
      expect(screen.queryByText(item.text)).toBeNull();
    }
    // And with nothing to agree to, there is nothing to accept.
    const accept = screen.getByRole("button", { name: CONSENT_ACTION });
    expect((accept as HTMLButtonElement).disabled).toBe(true);
  });
});
