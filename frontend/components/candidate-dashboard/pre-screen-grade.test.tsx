// @vitest-environment jsdom

/**
 * THE ONE STYLING RULE THE SPECIFICATION CALLS NON-NEGOTIABLE.
 *
 * "Pre-Screen Grade must render with muted/outline styling ONLY, no solid
 * filled pill, no bright color... This visual distinctiveness is mandatory: it
 * tells the recruiter 'this is an early signal, not a final verdict'."
 *
 * spec-doc6 §8.1 repeats it and asks for exactly this test: "Add a component
 * test asserting the Pre-Screen Grade element never carries a solid-fill or
 * brand-colour class. That test exists to prevent the exact regression the
 * document describes: an early signal reading as a final verdict."
 *
 * WHY THE ASSERTION IS ON THE CLASS LIST AND NOT ON A SCREENSHOT
 * ---------------------------------------------------------------
 * Because of how the regression arrives. Nobody sets out to make the
 * Pre-Screen Grade look authoritative; somebody adds `bg-rating-1-bg` to "make
 * the A stand out", and it renders beautifully. A visual check passes. What
 * fails is a recruiter's reading of the row, months later, on a candidate
 * whose evidence never arrived. So the test names the class fragments that
 * would do it, and refuses them.
 */

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { FORBIDDEN_ON_PRE_SCREEN, BAND_CLASS, BAND_STRONG } from "./band";
import { PreScreenGradeCell, ReadyPickScoreCell } from "./cells";
import { row } from "./test-fixtures";

afterEach(cleanup);

function renderCell(node: React.ReactNode) {
  return render(<TooltipProvider>{node}</TooltipProvider>);
}

describe("Pre-Screen Grade", () => {
  it("never carries a solid-fill or brand-colour class", () => {
    for (const grade of ["A", "B", "C", "Hold", null]) {
      cleanup();
      renderCell(
        <PreScreenGradeCell
          row={row({ pre_screen_grade: grade, pre_screen_label: "label" })}
        />
      );
      const element = screen.getByTestId("pre-screen-grade");
      const classes = element.className;
      for (const forbidden of FORBIDDEN_ON_PRE_SCREEN) {
        expect(
          classes.includes(forbidden),
          `grade ${String(grade)} carries "${forbidden}", which makes an early ` +
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

  it("is visually distinct from the Ready Pick Score, which may look finished", () => {
    // The rule is comparative: the two must not read as equally authoritative.
    // Asserting only that column 3 is plain would still pass if somebody made
    // column 4 plain too, which loses the distinction from the other side.
    renderCell(<PreScreenGradeCell row={row({ pre_screen_grade: "A" })} />);
    const grade = screen.getByTestId("pre-screen-grade").className;

    cleanup();
    renderCell(
      <ReadyPickScoreCell
        row={row({ ready_pick_score: 88, band: BAND_STRONG, band_label: "Ready to Pick, Strong" })}
      />
    );
    const score = screen.getByTestId("ready-pick-score").className;

    expect(score).toContain(BAND_CLASS[BAND_STRONG].split(" ")[1]);
    expect(grade).not.toContain("bg-rating");
    expect(score).not.toContain("bg-transparent");
  });

  it("says 'Not graded' rather than 'Hold' when nothing has graded the resume", () => {
    // A NULL grade means the resume has not been pre-screened. `Hold` is a
    // GRADED outcome meaning a person should look. Rendering them the same way
    // tells a recruiter an untriaged backlog has been triaged.
    renderCell(<PreScreenGradeCell row={row({ pre_screen_grade: null })} />);
    const element = screen.getByTestId("pre-screen-grade");
    expect(element.textContent).toContain("Not graded");
    expect(element.getAttribute("data-graded")).toBe("false");
  });

  it("carries its meaning in words, not only in a letter", () => {
    renderCell(
      <PreScreenGradeCell
        row={row({
          pre_screen_grade: "B",
          pre_screen_label: "B, claims are checkable",
        })}
      />
    );
    expect(screen.getByText(/claims are checkable/)).toBeTruthy();
  });
});
