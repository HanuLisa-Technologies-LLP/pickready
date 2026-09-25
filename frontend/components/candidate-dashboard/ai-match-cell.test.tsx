// @vitest-environment jsdom

/**
 * THE ONE STYLING RULE THE SPECIFICATION CALLS NON-NEGOTIABLE.
 *
 * "Pre-Screen Grade must render with muted/outline styling ONLY, no solid
 * filled pill, no bright color... This visual distinctiveness is mandatory: it
 * tells the recruiter 'this is an early signal, not a final verdict'."
 *
 * spec-doc6 §8.1 repeats it and asks for exactly this test. Column 3 is AI
 * Match now (Yukti's reading of the RESUME alone, as one of the four grade
 * words), and the letter grade is gone (D3), but the column is still the early
 * signal the rule was written for, so the rule and this test carry over to it.
 *
 * WHY THE ASSERTION IS ON THE CLASS LIST AND NOT ON A SCREENSHOT
 * ---------------------------------------------------------------
 * Because of how the regression arrives. Nobody sets out to make the early
 * signal look authoritative; somebody adds `bg-rating-1-bg` to "make Highly
 * Matching stand out", and it renders beautifully. A visual check passes. What
 * fails is a recruiter's reading of the row, months later, on a candidate
 * whose evidence never arrived. So the test names the class fragments that
 * would do it, and refuses them.
 */

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import {
  FORBIDDEN_ON_AI_MATCH,
  GRADE_CLASS,
  STATE_HIGHLY,
  STATE_MATCHING,
  STATE_MODERATELY,
  STATE_NOT,
  STATE_NOT_ASSESSED,
  STATE_NOT_CHECKED,
} from "./grade";
import { AiMatchCell, ReadyPickGradeCell } from "./cells";
import { row } from "./test-fixtures";

afterEach(cleanup);

function renderCell(node: React.ReactNode) {
  return render(<TooltipProvider>{node}</TooltipProvider>);
}

const STATES: Array<[string, string]> = [
  [STATE_HIGHLY, "Highly Matching"],
  [STATE_MATCHING, "Matching"],
  [STATE_MODERATELY, "Moderately Matching"],
  [STATE_NOT, "Not Matching"],
  [STATE_NOT_CHECKED, "Not checked yet"],
  [STATE_NOT_ASSESSED, "Not assessed"],
];

describe("AI Match", () => {
  it("never carries a solid-fill or brand-colour class, in any state", () => {
    for (const [state, label] of STATES) {
      cleanup();
      renderCell(
        <AiMatchCell
          row={row({ ai_match_state: state, ai_match_label: label })}
        />
      );
      const classes = screen.getByTestId("ai-match").className;
      for (const forbidden of FORBIDDEN_ON_AI_MATCH) {
        expect(
          classes.includes(forbidden),
          `state ${state} carries "${forbidden}", which makes an early ` +
            "signal read as a final verdict"
        ).toBe(false);
      }
      // And it is positively muted: a transparent background and a border.
      expect(classes).toContain("bg-transparent");
      expect(classes).toContain("border");
      expect(classes).toContain("font-normal");
      expect(classes).toContain("text-[11px]");
    }
  });

  it("is visually distinct from the Vivekium Grade, which may look finished", () => {
    // The rule is comparative: the two must not read as equally authoritative.
    // Asserting only that column 3 is plain would still pass if somebody made
    // column 4 plain too, which loses the distinction from the other side.
    renderCell(
      <AiMatchCell
        row={row({ ai_match_state: STATE_HIGHLY, ai_match_label: "Highly Matching" })}
      />
    );
    const early = screen.getByTestId("ai-match").className;

    cleanup();
    renderCell(
      <ReadyPickGradeCell
        row={row({ ranking_state: STATE_HIGHLY, ranking_label: "Highly Matching" })}
      />
    );
    const finished = screen.getByTestId("ready-pick-grade").className;

    expect(finished).toContain(GRADE_CLASS[STATE_HIGHLY].split(" ")[1]);
    expect(early).not.toContain("bg-rating");
    expect(finished).not.toContain("bg-transparent");
  });

  it("marks a resume nothing has read with a dashed outline and says so", () => {
    // "AI Matching has not read this" is not a grade, and it must not look
    // like the weakest one.
    renderCell(<AiMatchCell row={row()} />);
    const element = screen.getByTestId("ai-match");
    expect(element.textContent).toContain("Not checked yet");
    expect(element.getAttribute("data-state")).toBe(STATE_NOT_CHECKED);
    expect(element.className).toContain("border-dashed");
  });

  it("says in words that it is read from the resume only", () => {
    renderCell(
      <AiMatchCell
        row={row({
          ai_match_state: STATE_MATCHING,
          ai_match_label: "Matching",
          ai_match_screen_reader_label:
            "Matching, from the resume only. Resume check only. Real skills are tested in the assessment.",
        })}
      />
    );
    expect(screen.getByText(/from the resume only/)).toBeTruthy();
  });

  it("renders no digit and no letter grade", () => {
    for (const [state, label] of STATES) {
      cleanup();
      renderCell(
        <AiMatchCell row={row({ ai_match_state: state, ai_match_label: label })} />
      );
      const text = screen.getByTestId("ai-match").textContent ?? "";
      expect(text).not.toMatch(/\d/);
      expect(text.trim()).not.toMatch(/^(A|B|C|Hold)$/);
    }
  });
});
