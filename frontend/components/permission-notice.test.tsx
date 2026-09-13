// @vitest-environment jsdom

/**
 * The read-only rule, pinned (2026-09-13 spec, section 7).
 *
 * The bug this exists to prevent shipped once already: the company profile
 * page rendered "You have read-only access" to users who HELD
 * `edit_company_profile` and simply had not clicked Edit yet, because the
 * sentence sat in the else-branch of `canEdit && editing`. Moving the sentence
 * into a component that refuses to render it when `canEdit` is true makes the
 * mistake unwriteable; these tests are what keep it that way.
 */

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ReadOnlyNotice } from "./permission-notice";
import { READ_ONLY_TITLE, resolvePermission } from "@/lib/permissions";

afterEach(cleanup);

describe("ReadOnlyNotice", () => {
  it("says nothing at all to a user who may edit", () => {
    const { container } = render(
      <ReadOnlyNotice canEdit resource="the company profile" />
    );
    expect(container.innerHTML).toBe("");
    expect(screen.queryByText(READ_ONLY_TITLE)).toBeNull();
  });

  it("explains the restriction to a user who genuinely may not edit", () => {
    render(<ReadOnlyNotice canEdit={false} resource="the company profile" />);

    expect(screen.getByText(READ_ONLY_TITLE)).toBeTruthy();
    expect(screen.getByText(/the company profile/)).toBeTruthy();
    expect(screen.getByText(/Ask an administrator/)).toBeTruthy();
  });

  it("lets a surface supply its own sentence without losing the rule", () => {
    const { rerender, container } = render(
      <ReadOnlyNotice
        canEdit={false}
        resource="this SWOT analysis"
        message="You can read this SWOT but not change it."
      />
    );
    expect(screen.getByText("You can read this SWOT but not change it.")).toBeTruthy();

    rerender(
      <ReadOnlyNotice
        canEdit
        resource="this SWOT analysis"
        message="You can read this SWOT but not change it."
      />
    );
    expect(container.innerHTML).toBe("");
  });
});

describe("resolvePermission", () => {
  it("prefers the server's resource-scoped answer when it has arrived", () => {
    // The capability says yes; this job's assignment scope says no. The
    // resource answer is the one that matches what the write route will do.
    expect(resolvePermission(true, false)).toBe(false);
    expect(resolvePermission(false, true)).toBe(true);
  });

  it("falls back to the capability while the resource answer is loading", () => {
    expect(resolvePermission(true, undefined)).toBe(true);
    expect(resolvePermission(false, undefined)).toBe(false);
    expect(resolvePermission(true, null)).toBe(true);
  });
});
