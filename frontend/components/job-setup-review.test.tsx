// @vitest-environment jsdom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SetupStatus, type Setup } from "./job-setup-review";

afterEach(cleanup);

function pendingSetup(overrides: Partial<Setup> = {}): Setup {
  return {
    job_id: "workify-job",
    status: "questions_pending_review",
    grade: "non_managerial",
    framework_approved: false,
    ready_for_candidates: false,
    framework_pending: false,
    ...overrides,
  };
}

describe("SetupStatus", () => {
  it("explains the invitation block and links directly to the required action", () => {
    render(<SetupStatus setup={pendingSetup()} />);

    expect(screen.getByText("Framework pending review")).toBeTruthy();
    expect(
      screen.getByText(/No candidate can be invited.*save the PPI framework/s)
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Review and save framework" }).getAttribute("href")
    ).toBe("#ppi-framework");
  });

  it("does not offer approval while framework generation is still running", () => {
    render(<SetupStatus setup={pendingSetup({ framework_pending: true })} />);

    expect(screen.getByText(/still writing the criteria/i)).toBeTruthy();
    expect(
      screen.queryByRole("link", { name: "Review and save framework" })
    ).toBeNull();
  });
});
