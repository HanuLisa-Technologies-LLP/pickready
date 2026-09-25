// @vitest-environment jsdom
//
// The inline AI matching progress panel. What is pinned here is the Vivekium
// release's addition: a DEGRADED run is shown as degraded, with the server's
// own sentences, and is never announced as a plain "finished".

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  DEGRADED_WITHOUT_REASON,
  MatchingReasoning,
  type MatchingProgress,
} from "./matching-reasoning";

const REASON =
  "The embedding service was unavailable, so this run found candidates by keywords only.";

function progress(overrides: Partial<MatchingProgress> = {}): MatchingProgress {
  return {
    stages: [
      { key: "understanding", label: "Reading the job", detail: "Read.", status: "done" },
      {
        key: "semantic_retrieval",
        label: "Finding candidates by meaning",
        detail: "Skipped: the embedding service was unavailable.",
        status: "skipped",
      },
      { key: "scoring", label: "Checking resumes against the skills", detail: "Checked.", status: "done" },
    ],
    candidate_count: 2,
    scored_count: 2,
    degraded: false,
    degraded_reasons: [],
    ...overrides,
  };
}

afterEach(cleanup);

describe("MatchingReasoning", () => {
  it("says a clean run finished and shows no degraded notice", () => {
    render(<MatchingReasoning state="done" progress={progress()} message="" />);
    expect(screen.getByRole("heading").textContent).toBe("AI matching finished");
    expect(screen.queryByTestId("matching-degraded")).toBeNull();
  });

  it("shows a degraded run as degraded, with the server's reasons verbatim", () => {
    render(
      <MatchingReasoning
        state="done"
        progress={progress({ degraded: true, degraded_reasons: [REASON] })}
        message=""
      />,
    );
    expect(screen.getByRole("heading").textContent).toBe(
      "AI matching finished, but not everything was checked",
    );
    const notice = screen.getByTestId("matching-degraded");
    expect(notice.getAttribute("role")).toBe("note");
    expect(notice.querySelector("li")?.textContent).toBe(REASON);
  });

  it("shows the degraded notice while the run is still going", () => {
    render(
      <MatchingReasoning
        state="running"
        progress={progress({ degraded: true, degraded_reasons: [REASON] })}
        message=""
      />,
    );
    expect(screen.getByRole("heading").textContent).toBe("AI matching is running");
    expect(screen.getByTestId("matching-degraded").textContent).toContain(REASON);
  });

  it("says so rather than nothing when a degraded run gives no reason", () => {
    render(
      <MatchingReasoning
        state="done"
        progress={progress({ degraded: true, degraded_reasons: [] })}
        message=""
      />,
    );
    expect(screen.getByTestId("matching-degraded").textContent).toContain(
      DEGRADED_WITHOUT_REASON,
    );
  });
});
