// @vitest-environment jsdom

/**
 * `FormField` wires the error to the control, and this is what keeps it wired.
 *
 * The defect being pinned is not visual and never showed up in review: the
 * error paragraph rendered perfectly, in red, beside the field, and a screen
 * reader read the field's label and stopped. Every caller in the product had
 * the same hole because every caller would have had to remember three
 * attributes and an id, and none of them did.
 *
 * The wiring now lives in the primitive, so these tests are the thing that
 * notices if somebody later "simplifies" the clone away. Assertions use plain
 * DOM reads rather than jest-dom matchers, which this suite does not install.
 */

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FormField } from "./form";

afterEach(cleanup);

const control = (id: string) =>
  document.getElementById(id) as HTMLInputElement;

describe("FormField", () => {
  it("announces the error and points the control at it", () => {
    render(
      <FormField label="Job title" htmlFor="title" error="Enter a job title.">
        <input id="title" />
      </FormField>
    );

    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("Enter a job title.");
    expect(alert.id).toBeTruthy();

    expect(control("title").getAttribute("aria-invalid")).toBe("true");
    expect(control("title").getAttribute("aria-describedby")).toBe(alert.id);
  });

  it("leaves a healthy field entirely alone", () => {
    render(
      <FormField label="Department" htmlFor="dept">
        <input id="dept" />
      </FormField>
    );

    expect(screen.queryByRole("alert")).toBeNull();
    expect(control("dept").hasAttribute("aria-invalid")).toBe(false);
    expect(control("dept").hasAttribute("aria-describedby")).toBe(false);
  });

  it("describes the hint, and swaps to the error rather than reading both", () => {
    const { rerender } = render(
      <FormField label="Skills" htmlFor="skills" hint="Comma separated.">
        <input id="skills" />
      </FormField>
    );
    const hintId = control("skills").getAttribute("aria-describedby");
    expect(hintId).toBeTruthy();
    expect(document.getElementById(hintId as string)?.textContent).toBe(
      "Comma separated."
    );

    rerender(
      <FormField
        label="Skills"
        htmlFor="skills"
        hint="Comma separated."
        error="Name at least one skill."
      >
        <input id="skills" />
      </FormField>
    );
    expect(control("skills").getAttribute("aria-describedby")).toBe(
      screen.getByRole("alert").id
    );
  });

  it("EXTENDS an aria-describedby the caller already set, never replaces it", () => {
    // register-flow points its password box at a requirements checklist.
    // Clobbering that to show an error would trade one announcement for
    // another, which is why the merge concatenates.
    render(
      <>
        <FormField label="Password" htmlFor="pw" error="Too short.">
          <input id="pw" aria-describedby="pw-rules" />
        </FormField>
        <p id="pw-rules">At least eight characters.</p>
      </>
    );

    const described = control("pw")
      .getAttribute("aria-describedby")
      ?.split(" ");
    expect(described).toContain("pw-rules");
    expect(described).toContain(screen.getByRole("alert").id);
  });

  it("does not overwrite an aria-invalid the caller is managing itself", () => {
    render(
      <FormField label="Code" htmlFor="code" error="Wrong code.">
        <input id="code" aria-invalid={false} />
      </FormField>
    );
    expect(control("code").getAttribute("aria-invalid")).toBe("false");
  });

  it("marks a required field required, and hides the asterisk from readers", () => {
    render(
      <FormField label="Email" htmlFor="email" required>
        <input id="email" />
      </FormField>
    );
    expect(control("email").getAttribute("aria-required")).toBe("true");
    // The reader hears "required", not "star".
    expect(screen.getByText("(required)").className).toContain("sr-only");
    expect(screen.getByText("*").getAttribute("aria-hidden")).toBe("true");
  });

  it("leaves a multi-control field alone rather than annotating the first one", () => {
    // An experience band is two boxes. Cloning onto whichever came first would
    // claim the error belongs to Min when it is about the pair.
    render(
      <FormField label="Experience" error="Minimum cannot exceed maximum.">
        <>
          <input id="exp-min" />
          <input id="exp-max" />
        </>
      </FormField>
    );
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(control("exp-min").hasAttribute("aria-invalid")).toBe(false);
    expect(control("exp-max").hasAttribute("aria-invalid")).toBe(false);
  });
});
