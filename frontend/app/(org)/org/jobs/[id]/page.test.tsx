// @vitest-environment jsdom
//
// The job page's wiring (Vivekium release, Phase 1). Every panel on it has
// its own test; what is left for a page-level test is what the PAGE owns:
// the one JD document is edited as one document through PATCH /jobs/{id}/jd,
// the job's details are a separate PATCH that sends the grade only when it
// changed, the grade field locks from the setup answer, the grade is labelled
// "Grade" and never "Level", the retired setup review is gone, and closing a
// job says access stops rather than that the pipeline is unchanged.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Job } from "@/lib/types";

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
}));
const { apiGet, apiPost, apiPatch } = api;
const toast = vi.hoisted(() => vi.fn());
const setupAnswer = vi.hoisted(() => ({ grade_locked: false }));

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "job-1" }) }));
vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({ can: () => true, loading: false, capabilities: [] }),
}));
vi.mock("@/lib/ai-activity", () => ({}));
vi.mock("@/components/app-shell", () => ({
  PageHeader: ({
    title,
    description,
    actions,
  }: {
    title: React.ReactNode;
    description?: React.ReactNode;
    actions?: React.ReactNode;
  }) => (
    <header>
      <h1>{title}</h1>
      <p data-testid="page-description">{description}</p>
      {actions}
    </header>
  ),
}));
vi.mock("@/components/role-type-badge", () => ({ RoleTypeBadge: () => null }));
vi.mock("@/components/candidate-ranking-table", () => ({
  CandidateRankingTable: () => null,
}));
vi.mock("@/components/databank-upload", () => ({ DatabankUpload: () => null }));
vi.mock("@/components/pipeline-status", () => ({ PipelineFunnel: () => null }));
vi.mock("@/components/email-composition-modal", () => ({
  EmailCompositionModal: () => null,
}));
vi.mock("@/components/ppi-report-modal", () => ({ PPIReportModal: () => null }));
vi.mock("@/components/assessment-transcript", () => ({
  AssessmentTranscriptModal: () => null,
}));
// Records what the page hands the progress panel, so the run's wiring can be
// asserted without the panel's own rendering (it has its own test).
const matchingPanel = vi.hoisted(() => ({ props: [] as Array<Record<string, unknown>> }));
vi.mock("@/components/matching-reasoning", () => ({
  MatchingReasoning: (props: Record<string, unknown>) => {
    matchingPanel.props.push(props);
    return null;
  },
}));
vi.mock("@/components/ai-activity", () => ({
  AiActivityIndicator: () => null,
  useAiActivity: () => ({ report: vi.fn(), fail: vi.fn() }),
}));
vi.mock("@/components/posting-window", () => ({
  PostingWindowBanner: ({ onClose }: { onClose?: () => void }) =>
    onClose ? (
      <button type="button" onClick={onClose}>
        Close this posting
      </button>
    ) : null,
}));
vi.mock("@/components/job-swot-analysis", () => ({
  JobSwotAnalysisPanel: () => <section aria-label="SWOT analysis" />,
}));
vi.mock("@/components/job-skills", () => ({
  JobSkillsPanel: () => <section aria-label="Skills" />,
}));
vi.mock("@/components/proctoring/monitoring-policy-card", () => ({
  MonitoringPolicyCard: () => <section aria-label="Assessment monitoring" />,
}));
vi.mock("@/components/job-publish-card", async () => {
  const ReactModule = await import("react");
  return {
    JobPublishCard: ({
      onSetup,
    }: {
      onSetup?: (setup: { grade_locked: boolean }) => void;
    }) => {
      ReactModule.useEffect(() => {
        onSetup?.({ grade_locked: setupAnswer.grade_locked });
      }, [onSetup]);
      return <section aria-label="Publish" />;
    },
  };
});

import OrgJobDetailPage from "./page";

const JOB = {
  id: "job-1",
  title: "Backend Engineer",
  department: "Engineering",
  grade: "managerial",
  requirement_period: "Q4 2026",
  experience_min_years: 4,
  experience_max_years: 8,
  jd_markdown: "## Role\nOwn the payments platform.",
  jd: { reporting_to: "Engineering Director" },
  about_company: "We build payments.",
  work_life: null,
  benefits: null,
  overridden_sections: [],
  status: "draft",
  posting_start_date: "2026-09-01T00:00:00Z",
} as unknown as Job;

afterEach(cleanup);
beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  toast.mockReset();
  matchingPanel.props.length = 0;
  setupAnswer.grade_locked = false;
  apiGet.mockImplementation((path: string) =>
    path === "/jobs/job-1"
      ? Promise.resolve(JOB)
      : Promise.resolve({ company_name: "Acme" })
  );
});

describe("the job page", () => {
  it("labels the grade as Grade, never Level, and shows the setup panels in order", async () => {
    const { container } = render(<OrgJobDetailPage />);
    await screen.findByText("Own the payments platform.");

    expect(screen.getByText("Grade: Managerial")).toBeTruthy();
    expect(container.textContent ?? "").not.toMatch(/\bLevel\b/);
    expect(screen.getByTestId("page-description").textContent).toContain(
      "4 to 8 years"
    );
    const order = ["SWOT analysis", "Skills", "Assessment monitoring", "Publish"].map(
      (name) => screen.getByRole("region", { name })
    );
    for (let i = 1; i < order.length; i += 1) {
      expect(
        order[i - 1].compareDocumentPosition(order[i]) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy();
    }
    // The retired criteria editor and ranking category card are not on the
    // page: nothing on it says "matrix" or "categories" any more.
    expect(container.textContent ?? "").not.toMatch(/matrix|categories/i);
  });

  it("edits the JD as one document through the document route", async () => {
    apiPatch.mockResolvedValue({ ...JOB, jd_markdown: "## Role\nRewritten." });
    render(<OrgJobDetailPage />);
    await screen.findByText("Own the payments platform.");

    fireEvent.click(screen.getByRole("button", { name: /Edit description/ }));
    fireEvent.change(screen.getByLabelText("Job description document"), {
      target: { value: "## Role\nRewritten." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save description" }));

    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith("/jobs/job-1/jd", {
        jd_markdown: "## Role\nRewritten.",
      })
    );
    expect(await screen.findByText("Rewritten.")).toBeTruthy();
  });

  it("saves the details without a grade when the grade did not change, and never sends level or per-section JD", async () => {
    apiPatch.mockResolvedValue({ ...JOB, title: "Senior Backend Engineer" });
    render(<OrgJobDetailPage />);
    await screen.findByText("Own the payments platform.");

    fireEvent.click(screen.getByRole("button", { name: /Edit details/ }));
    fireEvent.change(screen.getByLabelText(/^Title/), {
      target: { value: "Senior Backend Engineer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));

    await waitFor(() => expect(apiPatch).toHaveBeenCalledTimes(1));
    const [path, body] = apiPatch.mock.calls[0];
    expect(path).toBe("/jobs/job-1");
    expect(body).toMatchObject({
      title: "Senior Backend Engineer",
      experience_min_years: 4,
      experience_max_years: 8,
    });
    expect(body).not.toHaveProperty("grade");
    expect(body).not.toHaveProperty("level");
    expect(body).not.toHaveProperty("jd");
    expect(body).not.toHaveProperty("jd_markdown");
  });

  it("locks the grade field from the setup answer and says why", async () => {
    setupAnswer.grade_locked = true;
    render(<OrgJobDetailPage />);
    await screen.findByText("Own the payments platform.");

    const sentence =
      "The grade is locked because a candidate has started the assessment. It decides the question budget every candidate on this job receives.";
    expect(await screen.findByText(sentence)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Edit details/ }));
    const trigger = screen.getByRole("combobox");
    expect((trigger as HTMLButtonElement).disabled).toBe(true);
  });

  it("closing a job says the assessment records are withheld, not that the pipeline is unchanged", async () => {
    apiPost.mockResolvedValue({ ...JOB, closed_at: "2026-09-24T00:00:00Z" });
    render(<OrgJobDetailPage />);
    await screen.findByText("Own the payments platform.");

    fireEvent.click(screen.getByRole("button", { name: "Close this posting" }));
    fireEvent.click(await screen.findByRole("button", { name: "Close the job" }));

    await waitFor(() => expect(toast).toHaveBeenCalled());
    const closed = toast.mock.calls.find(([arg]) => arg.title === "Job closed")?.[0];
    expect(closed.description).toBe(
      "New applications have stopped. This job's assessment records are now withheld from the hiring team and are deleted when the retention window ends."
    );
    expect(closed.description).not.toMatch(/unchanged/);
  });

  it("runs AI Matching through the job-scoped routes and keeps a degraded run degraded to the end", async () => {
    const reason =
      "The embedding service was unavailable, so this run found candidates by keywords only.";
    const runningStages = [
      { key: "understanding", label: "Reading the job", detail: "Read.", status: "done" },
      { key: "scoring", label: "Checking resumes", detail: "Checking.", status: "active" },
    ];
    const running = {
      task_id: "run-1",
      state: "PROGRESS",
      done: false,
      stages: runningStages,
      candidate_count: 2,
      scored_count: 1,
      degraded: true,
      degraded_reasons: [reason],
    };
    // What the status route answers once the run has finished: the run-status
    // record holds the task's return value, so the stage list is the empty
    // plan and the degraded flag is gone. Neither may overwrite the run.
    const finished = {
      task_id: "run-1",
      state: "SUCCESS",
      done: true,
      stages: runningStages.map((stage) => ({ ...stage, status: "pending" })),
      candidate_count: 0,
      scored_count: 0,
      degraded: false,
      degraded_reasons: [],
    };
    let polls = 0;
    apiGet.mockImplementation((path: string) => {
      if (path === "/jobs/job-1") return Promise.resolve(JOB);
      if (path === "/matching/jobs/job-1/tasks/run-1") {
        polls += 1;
        return Promise.resolve(polls === 1 ? running : finished);
      }
      return Promise.resolve({ company_name: "Acme" });
    });
    apiPost.mockResolvedValue({ candidate_count: 2, task_id: "run-1" });

    render(<OrgJobDetailPage />);
    await screen.findByText("Own the payments platform.");
    fireEvent.click(screen.getByRole("tab", { name: "Candidates" }));
    fireEvent.click(screen.getByRole("button", { name: "Run AI matching" }));

    await waitFor(
      () =>
        expect(toast).toHaveBeenCalledWith(
          expect.objectContaining({
            title: "AI matching finished, but not everything was checked",
          }),
        ),
      { timeout: 5000 },
    );
    expect(apiPost).toHaveBeenCalledWith("/matching/jobs/job-1/run");
    expect(apiGet).toHaveBeenCalledWith("/matching/jobs/job-1/tasks/run-1");
    expect(apiGet.mock.calls.map(([path]) => path)).not.toContain(
      "/matching/tasks/run-1",
    );

    const last = matchingPanel.props[matchingPanel.props.length - 1] as {
      state: string;
      message: string;
      progress: { stages: unknown; degraded: boolean; degraded_reasons: string[] };
    };
    expect(last.state).toBe("done");
    expect(last.progress.degraded).toBe(true);
    expect(last.progress.degraded_reasons).toEqual([reason]);
    expect(last.progress.stages).toEqual(runningStages);
    expect(last.message).not.toMatch(/complete/i);
  }, 10000);
});
