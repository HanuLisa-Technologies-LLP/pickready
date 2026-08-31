// @vitest-environment jsdom

/**
 * Column 4's six states, column 5's pending state, and the two action columns.
 *
 * The theme running through every test here: an ABSENT value must be visibly
 * absent. Four of the eight columns are filled by agents whose output may not
 * exist yet, and the way that fails is not a crash. It is a cell that looks
 * like an answer.
 */

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import {
  BAND_NOT_RECOMMENDED,
  BAND_PENDING,
  BAND_READY,
  BAND_STRONG,
  BAND_UNDER_REVIEW,
  BAND_CLASS,
} from "./band";
import {
  CandidateCell,
  NoteCell,
  ProfileButton,
  ReadyPickScoreCell,
  StageCell,
  TeamReviewButton,
} from "./cells";
import { row } from "./test-fixtures";

afterEach(cleanup);

const wrap = (node: React.ReactNode) =>
  render(<TooltipProvider>{node}</TooltipProvider>);

describe("Ready Pick Score", () => {
  it("shows the number, because D8 licenses exactly this one", () => {
    // The other half of the no-numbers rule, and the half a well-meaning
    // sweep would delete. spec-doc6 D8 carved this exception deliberately.
    wrap(
      <ReadyPickScoreCell
        row={row({
          ready_pick_score: 87,
          band: BAND_STRONG,
          band_label: "Ready to Pick, Strong",
          confidence_indicator: "filled",
        })}
      />
    );
    expect(screen.getByText("87")).toBeTruthy();
    expect(screen.getByText("Ready to Pick, Strong")).toBeTruthy();
  });

  it("shows no number when no profile has been written, and never a zero", () => {
    wrap(<ReadyPickScoreCell row={row()} />);
    const cell = screen.getByTestId("ready-pick-score");
    expect(cell.getAttribute("data-band")).toBe(BAND_PENDING);
    expect(cell.textContent).not.toMatch(/\d/);
    expect(cell.textContent).toContain("Pending Ready Pick Profile");
  });

  it("announces Under Review with its meaning, not as a colour", () => {
    // The specification names this state explicitly. A red pill reading two
    // words tells a screen-reader user nothing about why the control beside it
    // is locked.
    wrap(
      <ReadyPickScoreCell
        row={row({
          band: BAND_UNDER_REVIEW,
          band_label: "Under Review",
          band_screen_reader_label:
            "Status: Under Review, awaiting integrity disposition",
          under_integrity_review: true,
        })}
      />
    );
    expect(
      screen.getByText(/awaiting integrity disposition/)
    ).toBeTruthy();
  });

  it("colours Under Review differently from Not Recommended", () => {
    // spec-doc6 C30. The document groups both under one red, which would
    // render "we have not finished checking" and "we think this person is
    // weak" identically. A flag is not a rejection.
    expect(BAND_CLASS[BAND_UNDER_REVIEW]).not.toEqual(
      BAND_CLASS[BAND_NOT_RECOMMENDED]
    );
    expect(BAND_CLASS[BAND_UNDER_REVIEW]).toContain("warning");
  });

  it("never invents a score range", () => {
    // The specification asks for `82 [76 to 88]`. Nothing in the engine
    // publishes an interval, and a bracket computed here would be a number
    // with no provenance printed beside one that has some.
    wrap(
      <ReadyPickScoreCell
        row={row({ ready_pick_score: 82, band: BAND_READY, band_label: "Ready to Pick" })}
      />
    );
    expect(screen.getByTestId("ready-pick-score").textContent).not.toContain("[");
  });

  it("carries the confidence in words beside the dot", () => {
    wrap(
      <ReadyPickScoreCell
        row={row({
          ready_pick_score: 65,
          band: BAND_READY,
          confidence_indicator: "outline",
          confidence_label: "Low confidence",
        })}
      />
    );
    expect(screen.getByText(/Low confidence/)).toBeTruthy();
  });
});

describe("Ready Pick Note", () => {
  it("renders the pending sentence rather than an empty cell", () => {
    wrap(<NoteCell row={row()} />);
    expect(screen.getAllByText("Ready Pick Profile not written yet.").length).toBeGreaterThan(0);
  });

  it("is never bold and never coloured", () => {
    // Colour is column 4's. Spending it here would take the meaning out of the
    // one place it means something.
    wrap(<NoteCell row={row({ note: "Owns a comparable migration.", note_is_pending: false })} />);
    const classes = screen.getByTestId("ready-pick-note").className;
    expect(classes).toContain("font-normal");
    expect(classes).not.toContain("font-bold");
    expect(classes).not.toContain("text-rating");
    expect(classes).not.toContain("text-teal");
    expect(classes).not.toContain("text-navy");
  });
});

describe("Ready Pick Profile button", () => {
  it("is disabled with an explanation before a profile exists", () => {
    wrap(<ProfileButton row={row()} onOpen={() => undefined} />);
    const button = screen.getByRole("button", { name: /not available/i });
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("names the PRISM Report as a different document in its explanation", () => {
    // spec-doc6 C15: the row's pending state refers to the Ready Pick Profile,
    // not to the delivered PRISM Report.
    expect(row().profile_pending_reason).toContain("PRISM Report");
  });

  it("is enabled once a profile exists", () => {
    wrap(
      <ProfileButton
        row={row({
          profile: { artifact: "ready_pick_profile", evaluation_id: "e1" },
          profile_pending_reason: null,
        })}
        onOpen={() => undefined}
      />
    );
    const button = screen.getByRole("button", { name: /Open the Ready Pick Profile/i });
    expect(button.hasAttribute("disabled")).toBe(false);
  });
});

describe("Team Review button", () => {
  it("is never disabled and is never teal", () => {
    // The Dashboard document suggests teal here. In this design system teal
    // means CORROBORATED EVIDENCE, and a person's opinion is the furthest
    // thing from it; spending the evidence colour on the subjective column
    // would empty it of meaning everywhere else.
    wrap(<TeamReviewButton row={row()} onOpen={() => undefined} disabledReason={null} />);
    const button = screen.getByRole("button", { name: /Open Team Review/i });
    expect(button.hasAttribute("disabled")).toBe(false);
    expect(button.className).not.toContain("teal");
  });
});

describe("Stage control", () => {
  it("is locked with a lock and a reason under integrity review", () => {
    wrap(
      <StageCell
        row={row({ under_integrity_review: true })}
        canMove
        disabledReason={null}
        onOpen={() => undefined}
      />
    );
    const button = screen.getByRole("button", { name: /Move Test Candidate Zero/i });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.className).toContain("opacity-40");
    expect(button.className).toContain("cursor-not-allowed");
  });

  it("is disabled with an explanation for a role that may not move anybody", () => {
    wrap(
      <StageCell
        row={row()}
        canMove={false}
        disabledReason="Moving a candidate through the pipeline is not part of this role."
        onOpen={() => undefined}
      />
    );
    expect(
      screen.getByRole("button", { name: /Move Test Candidate Zero/i }).hasAttribute("disabled")
    ).toBe(true);
  });

  it("is enabled in the normal flow", () => {
    wrap(<StageCell row={row()} canMove disabledReason={null} onOpen={() => undefined} />);
    expect(
      screen.getByRole("button", { name: /Move Test Candidate Zero/i }).hasAttribute("disabled")
    ).toBe(false);
  });
});

describe("Candidate cell", () => {
  it("shows the name and the system code, and the code is selectable", () => {
    wrap(<CandidateCell row={row()} />);
    expect(screen.getByText("Test Candidate Zero")).toBeTruthy();
    const code = screen.getByText("JSRS-Y4BN-8HGX");
    expect(code.className).toContain("font-mono");
    expect(code.className).toContain("select-all");
  });

  it("offers the copy control to a keyboard user, not only on hover", () => {
    // A control that appears only on hover is a control a keyboard user does
    // not have.
    wrap(<CandidateCell row={row()} />);
    const copy = screen.getByRole("button", { name: /Copy candidate code/i });
    expect(copy.className).toContain("focus-visible:opacity-100");
  });
});
