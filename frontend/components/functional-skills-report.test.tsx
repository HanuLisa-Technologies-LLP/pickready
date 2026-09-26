// @vitest-environment jsdom

import * as React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Recharts measures its container, which jsdom reports as zero, so the radar
// never renders and every chart assertion would be about a mock anyway. Stubbed
// to keep these tests about the SECTIONS, which is what changed.
vi.mock("recharts", () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  );
  return {
    Legend: Passthrough,
    PolarAngleAxis: Passthrough,
    PolarRadiusAxis: Passthrough,
    Radar: Passthrough,
    RadarChart: Passthrough,
    ResponsiveContainer: Passthrough,
  };
});

import {
  EVIDENCE_LOAD_FAILED,
  NO_CITED_EVIDENCE,
  TRAIL_UNAVAILABLE,
  type ReportCitations,
} from "./report-citations";
import {
  AI_MATCH_TITLE,
  CLAIM_EVIDENCE_TITLE,
  FunctionalSkillsReportView,
  REPORT_SECTION_ORDER,
  VALIDATION_POINTS_TITLE,
  type FunctionalReport,
} from "./functional-skills-report";

afterEach(cleanup);

function dimension(name: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    grade: "Matching" as const,
    required_level: "Matching" as const,
    remark: "Owned the migration end to end and named the rollback they wrote.",
    ...extra,
  };
}

/**
 * A report as it is written TODAY: every rated line carries its evidence
 * confidence and both new sections are present.
 */
function report(overrides: Partial<FunctionalReport> = {}): FunctionalReport {
  return {
    id: "report-1",
    job_candidate_link_id: "link-1",
    grade: "non_managerial",
    ai_score: [dimension("Skills present", { evidence_confidence: "Low" })],
    overall_grade: "Matching",
    overall_summary: "Consistent evidence of ownership across the stack.",
    must_have: [
      dimension("Distributed Systems", {
        evidence_confidence: "Moderate",
        evidence_sources: ["Assessment responses", "Resume"],
      }),
    ],
    nice_to_have: [],
    behavioural: [],
    validation: { captured: true, fields: [] },
    claim_evidence: {
      note: "What this person asserted, and what the record holds.",
      entries: [
        {
          area: "Distributed Systems",
          claim: "Led the migration of the ingest path onto Kafka.",
          evidence: "Identified in Assessment responses and Resume.",
          confidence: "Moderate",
        },
      ],
      no_claims_statement: null,
    },
    validation_points: {
      note: "Areas where the evidence base is thinner than the grade beside it.",
      points: [
        {
          area: "Judgement under pressure",
          driver: "confidence",
          confidence: "Low",
          reason: "The evidence is this person's own account, uncorroborated.",
          probe: "Ask for a specific instance, and for who else could describe it.",
        },
      ],
      no_points_statement: null,
    },
    synthesized_at: "2026-09-22T00:00:00Z",
    ...overrides,
  } as FunctionalReport;
}

describe("the section order", () => {
  it("carries both new sections and keeps Gap Analysis before Validation", () => {
    // Pinned against `report_pdf.SECTION_ORDER` by the backend suite; asserted
    // here for the property the 2026-08-23 reversal bought, which inserting two
    // sections must not undo.
    expect(REPORT_SECTION_ORDER).toContain("claim_evidence");
    expect(REPORT_SECTION_ORDER).toContain("validation_points");
    expect(REPORT_SECTION_ORDER.indexOf("gap_analysis")).toBeLessThan(
      REPORT_SECTION_ORDER.indexOf("validation"),
    );
  });
});

describe("Evidence Confidence on a rated line", () => {
  // Scoped to the Must-have SECTION. The two new sections print the same
  // words about their own rows, so an unscoped query matches three places and
  // would pass while the rated card rendered nothing.
  it("renders the word and the sources beside the grade", () => {
    render(<FunctionalSkillsReportView report={report()} />);
    const section = within(screen.getByLabelText("Must-have"));
    expect(section.getByText(/Evidence confidence: Moderate/)).toBeDefined();
    expect(section.getByText(/Assessment responses, Resume/)).toBeDefined();
  });

  it("renders nothing at all when the report predates the field", () => {
    // A report is immutable, so this is every report stored before 0107. The
    // honest rendering of a value that was never computed is no line: a word
    // invented here would be the only uncheckable statement on the card.
    render(
      <FunctionalSkillsReportView
        report={report({
          must_have: [dimension("Distributed Systems")],
          ai_score: [dimension("Skills present")],
        })}
      />,
    );
    expect(
      within(screen.getByLabelText("Must-have")).queryByText(/Evidence confidence/),
    ).toBeNull();
  });

  it("renders the word without a source line when no source was named", () => {
    render(
      <FunctionalSkillsReportView
        report={report({
          must_have: [
            dimension("Distributed Systems", {
              evidence_confidence: "Insufficient evidence",
            }),
          ],
        })}
      />,
    );
    const section = within(screen.getByLabelText("Must-have"));
    expect(
      section.getByText(/Evidence confidence: Insufficient evidence/),
    ).toBeDefined();
    expect(section.queryByText(/Sources:/)).toBeNull();
  });
});

describe("Evidence vs Claim Summary", () => {
  it("renders the claim, the evidence and the confidence", () => {
    render(<FunctionalSkillsReportView report={report()} />);
    expect(screen.getByRole("heading", { name: CLAIM_EVIDENCE_TITLE })).toBeDefined();
    expect(
      screen.getByText("Led the migration of the ingest path onto Kafka."),
    ).toBeDefined();
    expect(
      screen.getByText("Identified in Assessment responses and Resume."),
    ).toBeDefined();
  });

  it("prints the server's absent-evidence sentence verbatim", () => {
    // The component never writes its own. The server's wording says in so many
    // words that an unevidenced claim is a gap in what was examined rather than
    // a finding about the claim, and shortening it to "no evidence found" is
    // exactly the reading the sentence exists to prevent.
    const sentence =
      "This area was assessed and nothing in the record addressed the claim. " +
      "That is a gap in what was examined, not a finding about the claim itself.";
    render(
      <FunctionalSkillsReportView
        report={report({
          claim_evidence: {
            note: "",
            entries: [
              {
                area: "Distributed Systems",
                claim: "Led the migration.",
                evidence: sentence,
                confidence: "Insufficient evidence",
              },
            ],
            no_claims_statement: null,
          },
        })}
      />,
    );
    expect(screen.getByText(sentence)).toBeDefined();
  });

  it("says so in words when there are no material claims", () => {
    render(
      <FunctionalSkillsReportView
        report={report({
          claim_evidence: {
            note: "",
            entries: [],
            no_claims_statement: "No claims material to this role were recorded.",
          },
        })}
      />,
    );
    expect(
      screen.getByText("No claims material to this role were recorded."),
    ).toBeDefined();
  });

  it("renders no section at all for a report written before it existed", () => {
    render(<FunctionalSkillsReportView report={report({ claim_evidence: undefined })} />);
    expect(screen.queryByRole("heading", { name: CLAIM_EVIDENCE_TITLE })).toBeNull();
  });
});

describe("Recommended Human Validation Points", () => {
  it("renders the area, the reason and the one probe", () => {
    render(<FunctionalSkillsReportView report={report()} />);
    expect(
      screen.getByRole("heading", { name: VALIDATION_POINTS_TITLE }),
    ).toBeDefined();
    expect(screen.getByText("Judgement under pressure")).toBeDefined();
    expect(
      screen.getByText("The evidence is this person's own account, uncorroborated."),
    ).toBeDefined();
    expect(
      screen.getByText(
        "Ask for a specific instance, and for who else could describe it.",
      ),
    ).toBeDefined();
  });

  it("is a separate section from the Gap Analysis", () => {
    // They answer different questions from different inputs and they will
    // disagree, which is the point of rendering both.
    render(<FunctionalSkillsReportView report={report()} />);
    expect(screen.getByLabelText(VALIDATION_POINTS_TITLE)).toBeDefined();
    expect(screen.queryByLabelText("Gap Analysis and Action Plan")).toBeNull();
  });

  it("says so in words when nothing needs checking", () => {
    render(
      <FunctionalSkillsReportView
        report={report({
          validation_points: {
            note: "",
            points: [],
            no_points_statement: "Every area assessed rests on corroborated evidence.",
          },
        })}
      />,
    );
    expect(
      screen.getByText("Every area assessed rests on corroborated evidence."),
    ).toBeDefined();
  });

  it("renders no section at all for a report written before it existed", () => {
    render(
      <FunctionalSkillsReportView report={report({ validation_points: undefined })} />,
    );
    expect(screen.queryByRole("heading", { name: VALIDATION_POINTS_TITLE })).toBeNull();
  });
});

describe("the number ban, on the screen as in the PDF", () => {
  it("renders no digit in either new section", () => {
    const { container } = render(<FunctionalSkillsReportView report={report()} />);
    const claims = container.querySelector(`[aria-label="${CLAIM_EVIDENCE_TITLE}"]`);
    const points = container.querySelector(`[aria-label="${VALIDATION_POINTS_TITLE}"]`);
    for (const section of [claims, points]) {
      expect(section).not.toBeNull();
      expect(section?.textContent ?? "").not.toMatch(/\d/);
    }
  });
});

describe("a skill the evaluation could not complete", () => {
  it("states Not assessed and the server's sentence, never a grade", () => {
    render(
      <FunctionalSkillsReportView
        report={report({
          must_have: [
            dimension("Distributed Systems", {
              grade: "Not assessed",
              status: "not_assessed",
              status_note: "Not assessed: the evaluation could not be completed.",
            }),
          ],
        })}
      />,
    );
    const section = within(screen.getByLabelText("Must-have"));
    expect(section.getByText("Not assessed")).toBeDefined();
    expect(
      section.getByText("Not assessed: the evaluation could not be completed."),
    ).toBeDefined();
  });

  it("prints the template and support markers under the remark", () => {
    render(
      <FunctionalSkillsReportView
        report={report({
          must_have: [
            dimension("Distributed Systems", {
              remark_note: "Written from a fixed template while the writing model was unavailable.",
              support_note: "The cited answer does not clearly support this remark.",
            }),
          ],
        })}
      />,
    );
    const section = within(screen.getByLabelText("Must-have"));
    expect(
      section.getByText("Written from a fixed template while the writing model was unavailable."),
    ).toBeDefined();
    expect(
      section.getByText("The cited answer does not clearly support this remark."),
    ).toBeDefined();
  });
});

describe("the AI Match section", () => {
  it("renders the frozen snapshot: the word, the header and tagged evidence", () => {
    const { container } = render(
      <FunctionalSkillsReportView
        report={report({
          ai_score: [],
          ai_score_snapshot: {
            status: "scored",
            grade: "Matching",
            header: "Resume check only. Real skills are tested in the assessment.",
            tags: [
              { text: "Kafka in production", polarity: "positive" },
              { text: "No on-call history", polarity: "negative" },
            ],
          },
        })}
      />,
    );
    const section = within(screen.getByLabelText(AI_MATCH_TITLE));
    expect(screen.getByRole("heading", { name: "AI Match" })).toBeDefined();
    expect(section.getByText("Matching")).toBeDefined();
    expect(
      section.getByText("Resume check only. Real skills are tested in the assessment."),
    ).toBeDefined();
    // A screen reader hears which side of the evidence each tag is on.
    expect(section.getByText("Evidenced:")).toBeDefined();
    expect(section.getByText("Not evidenced:")).toBeDefined();
    expect(section.getByText("Kafka in production")).toBeDefined();
    const text = container.querySelector(`[aria-label="${AI_MATCH_TITLE}"]`)?.textContent ?? "";
    expect(text).not.toMatch(/\d/);
    expect(text).not.toMatch(/AI Score/);
  });

  it("still renders an older report's four legacy rows as they were written", () => {
    render(<FunctionalSkillsReportView report={report()} />);
    expect(within(screen.getByLabelText(AI_MATCH_TITLE)).getByText("Skills present")).toBeDefined();
  });
});

function citations(overrides: Partial<ReportCitations> = {}): ReportCitations {
  return {
    trail_available: true,
    statements: [
      {
        section: "must_have",
        item: "Distributed Systems",
        kind: "finding",
        text: "Owned the migration end to end and named the rollback they wrote.",
        support: null,
        evidence: [
          {
            kind: "The candidate's answer",
            question: "How did you run the orders migration?",
            excerpt: "I planned the partitions and wrote the rollback myself.",
          },
        ],
      },
    ],
    ...overrides,
  };
}

describe("clicking a remark shows what it rests on", () => {
  const remark = /Owned the migration end to end/;

  it("fetches on the first click only and renders the cited answer", async () => {
    const load = vi.fn().mockResolvedValue(citations());
    render(<FunctionalSkillsReportView report={report()} loadCitations={load} />);
    // Opening the report is not reading the evidence: nothing is fetched yet.
    expect(load).not.toHaveBeenCalled();

    const button = within(screen.getByLabelText("Must-have")).getByRole("button", {
      name: remark,
    });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(
      await screen.findByText(/I planned the partitions and wrote the rollback myself/),
    ).toBeDefined();
    expect(screen.getByText("Asked: How did you run the orders migration?")).toBeDefined();
    expect(screen.getByText("The candidate's answer")).toBeDefined();

    // Closing and reopening reuses what was fetched.
    fireEvent.click(button);
    fireEvent.click(button);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("says so when the report predates its evidence trail", async () => {
    const load = vi.fn().mockResolvedValue(citations({ trail_available: false, statements: [] }));
    render(<FunctionalSkillsReportView report={report()} loadCitations={load} />);
    fireEvent.click(screen.getByRole("button", { name: remark }));
    expect(await screen.findByText(TRAIL_UNAVAILABLE)).toBeDefined();
  });

  it("says so when the trail holds nothing for this remark", async () => {
    const load = vi.fn().mockResolvedValue(citations({ statements: [] }));
    render(<FunctionalSkillsReportView report={report()} loadCitations={load} />);
    fireEvent.click(screen.getByRole("button", { name: remark }));
    expect(await screen.findByText(NO_CITED_EVIDENCE)).toBeDefined();
  });

  it("reports a failed fetch rather than an empty panel", async () => {
    const load = vi.fn().mockRejectedValue(new Error("Report not found"));
    render(<FunctionalSkillsReportView report={report()} loadCitations={load} />);
    fireEvent.click(screen.getByRole("button", { name: remark }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(EVIDENCE_LOAD_FAILED);
    expect(alert.textContent).toContain("Report not found");
  });

  it("is plain text, not a control, where no citations route exists", () => {
    render(<FunctionalSkillsReportView report={report()} />);
    expect(screen.queryByRole("button", { name: remark })).toBeNull();
    expect(
      within(screen.getByLabelText("Must-have")).getByText(remark),
    ).toBeDefined();
  });
});
