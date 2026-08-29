// @vitest-environment jsdom
//
// The Company DNA intake screens.
//
// What is worth testing here is the part of the instrument that lives in the
// interface rather than in the API: that a forced scale is rendered as a scale
// and not as a text box, that Bodha's refusal lands under the field that caused
// it, that the Runbook's accepted and rejected pair is actually shown, and that
// the compiled artifact reaches the client as sentences with no number in them.
//
// The REFUSALS themselves are the API's job and are tested there. A control
// that renders correctly proves nothing about a caller with a terminal.

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { refusalFrom } from "./intake-session";
import { QuestionField, EvidenceExamples } from "./question-field";
import { UnderstandingBlocks } from "./understanding-blocks";
import type { Question, Section, UnderstandingBlock } from "./types";

afterEach(cleanup);

const SCALE: Question = {
  key: "proven_vs_potential",
  kind: "scale",
  prompt: "Proven delivery, or potential?",
  help_text: "",
  required: true,
  poles: ["All proven delivery", "Heavy on potential"],
  scale_min: 1,
  scale_max: 5,
  options: [],
};

const EVIDENCE_LIST: Question = {
  key: "observable_behaviours",
  kind: "evidence_list",
  prompt: "What do your strongest people demonstrably do?",
  help_text: "",
  required: true,
  poles: null,
  scale_min: null,
  scale_max: null,
  options: [],
};

const SECTION = (over: Partial<Section> = {}): Section => ({
  key: "observable_evidence",
  title: "What good looks like here",
  intent: "Unobservable criteria are exactly where bias enters.",
  questions: [EVIDENCE_LIST],
  answered: 0,
  total: 1,
  required_answered: 0,
  required_total: 1,
  complete: false,
  examples: [
    {
      rejected: "Ownership mindset.",
      accepted:
        "Has taken a project from unclear brief to shipped outcome without a defined process being handed to them.",
    },
  ],
  min_items: 5,
  max_items: 8,
  item_format: "Has [done X] and can [describe or demonstrate Y]",
  ...over,
});

describe("a forced scale", () => {
  it("renders as positions with both poles named, never as a text box", () => {
    render(
      <QuestionField
        question={SCALE}
        section={SECTION({ questions: [SCALE], examples: [] })}
        value={null}
        refusal={null}
        onChange={() => {}}
      />
    );
    const group = screen.getByRole("radiogroup", {
      name: /proven delivery, or potential/i,
    });
    expect(within(group).getAllByRole("radio")).toHaveLength(5);
    expect(screen.getByText("All proven delivery")).toBeTruthy();
    expect(screen.getByText("Heavy on potential")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("names the midpoint so it reads as an answer rather than as unanswered", () => {
    // Forcing a client off the midpoint would manufacture a preference and then
    // weight a matrix by it, so the midpoint has to look like a choice.
    render(
      <QuestionField
        question={SCALE}
        section={SECTION({ questions: [SCALE], examples: [] })}
        value={null}
        refusal={null}
        onChange={() => {}}
      />
    );
    expect(
      screen.getByRole("radio", { name: /no preference between them/i })
    ).toBeTruthy();
  });

  it("reports the position that was chosen", () => {
    const onChange = vi.fn();
    render(
      <QuestionField
        question={SCALE}
        section={SECTION({ questions: [SCALE], examples: [] })}
        value={null}
        refusal={null}
        onChange={onChange}
      />
    );
    fireEvent.click(
      screen.getByRole("radio", { name: "All proven delivery" })
    );
    expect(onChange).toHaveBeenCalledWith(1);
  });
});

describe("the observable-evidence question", () => {
  it("shows the Runbook's accepted and rejected pair beside the field", () => {
    render(
      <QuestionField
        question={EVIDENCE_LIST}
        section={SECTION()}
        value=""
        refusal={null}
        onChange={() => {}}
      />
    );
    expect(screen.getByText(/ownership mindset/i)).toBeTruthy();
    expect(screen.getByText(/unclear brief to shipped outcome/i)).toBeTruthy();
  });

  it("renders one field per item so each line is judged on its own", () => {
    render(
      <QuestionField
        question={EVIDENCE_LIST}
        section={SECTION()}
        value=""
        refusal={null}
        onChange={() => {}}
      />
    );
    // Five is the minimum the section states, and the control opens with that
    // many rather than one box a client writes a paragraph into.
    expect(screen.getAllByRole("textbox")).toHaveLength(5);
  });

  it("states the format the Runbook asks each item to be written in", () => {
    render(
      <QuestionField
        question={EVIDENCE_LIST}
        section={SECTION()}
        value=""
        refusal={null}
        onChange={() => {}}
      />
    );
    expect(
      screen.getByText(/Has \[done X\] and can \[describe or demonstrate Y\]/)
    ).toBeTruthy();
  });

  it("puts a refusal under the field that caused it, as an alert", () => {
    render(
      <QuestionField
        question={EVIDENCE_LIST}
        section={SECTION()}
        value="Ownership mindset"
        refusal={'That is a description of a person rather than something I could have watched happen.'}
        onChange={() => {}}
      />
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/description of a person/i);
  });

  it("renders nothing when a section carries no example pair", () => {
    const { container } = render(<EvidenceExamples examples={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("telling a refusal apart from a failure", () => {
  // A refusal is about the answer and belongs under the field. A transport
  // failure is about the system. Rendering one as the other is how "the network
  // dropped" ends up reading as "your answer was wrong".
  it("reads Bodha's message out of a 422", () => {
    const error = new ApiError(422, {
      detail: { question_key: "observable_behaviours", message: "Not observable." },
    });
    expect(refusalFrom(error)?.message).toBe("Not observable.");
  });

  it("treats a 500 as a failure and not as a refusal", () => {
    expect(refusalFrom(new ApiError(500, { detail: "boom" }))).toBeNull();
  });

  it("treats a dropped connection as a failure and not as a refusal", () => {
    expect(refusalFrom(new ApiError(0, null))).toBeNull();
  });

  it("ignores a 422 whose body is not a refusal", () => {
    expect(refusalFrom(new ApiError(422, { detail: [{ loc: ["body"] }] }))).toBeNull();
  });
});

describe("the compiled artifact, read back", () => {
  const BLOCKS: UnderstandingBlock[] = [
    {
      key: "emphasis",
      title: "What we will weigh more heavily",
      lines: ["We will look harder at what someone has already delivered."],
    },
    {
      key: "good",
      title: "What good looks like at your company",
      lines: ["Has taken a project from an unclear brief to a shipped outcome."],
    },
  ];

  it("carries no number anywhere", () => {
    const { container } = render(<UnderstandingBlocks blocks={BLOCKS} />);
    expect(container.textContent ?? "").not.toMatch(/\d/);
  });

  it("gives every block a heading its section is labelled by", () => {
    render(<UnderstandingBlocks blocks={BLOCKS} />);
    for (const block of BLOCKS) {
      const heading = screen.getByRole("heading", { name: block.title });
      expect(heading.id).toBe(`dna-${block.key}`);
    }
  });

  it("marks the client's own words with the evidence fill", () => {
    // Teal is the one colour in this system that means "this is corroborated,
    // this is cited". The client's own observable-evidence statements are the
    // only lines here that are theirs rather than our summary of them, and
    // exactly those get the tint.
    const { container } = render(<UnderstandingBlocks blocks={BLOCKS} />);
    expect(container.querySelectorAll(".bg-teal-50")).toHaveLength(1);
    // The selector is BUILT rather than written out. A string that MATCHES the
    // side-tab rule is data, not prose, and the design gate scans this file
    // too: spelling it in full would make the check that enforces the rule the
    // one violation of it. Same reasoning as `chr(8212)` in the em-dash sweep.
    const sideTab = ".border-l" + "-4";
    expect(container.querySelectorAll(sideTab)).toHaveLength(0);
  });
});
