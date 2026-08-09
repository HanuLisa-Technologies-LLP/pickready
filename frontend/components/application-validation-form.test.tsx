// @vitest-environment jsdom
//
// The mandatory-fields block on the application form (2026-08-09 changes).

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  ApplicationValidationForm,
  VALIDATION_INTRO_FALLBACK,
  type ValidationFieldSpec,
} from "./application-validation-form";

afterEach(cleanup);

const FIELDS: ValidationFieldSpec[] = [
  { key: "current_ctc", label: "Current CTC", type: "text" },
  {
    key: "document_readiness",
    label: "Document readiness",
    type: "select",
    options: ["All documents ready"],
    documents: ["PAN card", "Passport size photograph"],
  },
  { key: "role_interest", label: "Why does this role interest you?", type: "textarea" },
];

describe("application validation form", () => {
  it("states the reuse behaviour rather than the old placeholder heading", () => {
    render(
      <ApplicationValidationForm fields={FIELDS} values={{}} onChange={() => {}} />
    );
    expect(screen.queryByText(/a few required details/i)).toBeNull();
    expect(
      screen.getByText(/only one time and automatically applicable to all other jobs/i)
    ).toBeTruthy();
  });

  it("falls back to the same sentence when the server sends no intro", () => {
    // The copy is served so it cannot drift from the behaviour; the fallback
    // exists so an older cached page never renders a heading-shaped blank.
    render(
      <ApplicationValidationForm
        fields={FIELDS}
        values={{}}
        onChange={() => {}}
        intro=""
      />
    );
    expect(screen.getByText(VALIDATION_INTRO_FALLBACK)).toBeTruthy();
  });

  it("names the documents the readiness answer refers to", () => {
    render(
      <ApplicationValidationForm fields={FIELDS} values={{}} onChange={() => {}} />
    );
    const panel = screen.getByTestId("validation-documents-document_readiness");
    expect(panel.textContent).toContain("PAN card");
    expect(panel.textContent).toContain("Passport size photograph");
  });

  it("renders no document panel for a field that names none", () => {
    render(
      <ApplicationValidationForm
        fields={[FIELDS[0]]}
        values={{}}
        onChange={() => {}}
      />
    );
    expect(screen.queryByTestId("validation-documents-current_ctc")).toBeNull();
  });

  it("prefills carried-forward answers so they are edited, not retyped", () => {
    render(
      <ApplicationValidationForm
        fields={FIELDS}
        values={{ current_ctc: "18 LPA" }}
        onChange={() => {}}
      />
    );
    expect(
      (screen.getByLabelText(/current ctc/i) as HTMLInputElement).value
    ).toBe("18 LPA");
  });
});
