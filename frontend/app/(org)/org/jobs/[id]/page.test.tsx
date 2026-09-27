// @vitest-environment jsdom
//
// The job page's setup, as the owner ruled it (CONTRACT v10, 2026-09-28):
//
//     JD -> Skills -> Final Job Posting -> Publish
//     SWOT: separate internal hiring intelligence, editable independently
//     First genuine application -> FREEZE JD + Skills
//
// What the PAGE owns, and therefore what is pinned here: the order of the
// setup, the SWOT living in its own internal section outside that chain, and
// the frozen state (a banner with the server's sentence and date, the JD and
// the title, band and grade read-only, the SWOT still editable, and a details
// save that sends none of the frozen fields). The panels themselves are
// tested in their own files, so they are stubbed here down to the props the
// page hands them.

import * as React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobSetupStatus } from "@/components/job-publish-card";
import type { SkillsOut } from "@/components/job-skills";
import type { PostingPreview } from "@/lib/types";

const permissions = vi.hoisted(() => ({
  can: () => true,
  loading: false,
  capabilities: [] as string[],
}));
const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiPost: vi.fn(),
}));
const fixtures = vi.hoisted(() => ({
  setup: null as unknown as JobSetupStatus,
  skills: null as unknown as SkillsOut,
  swotProps: [] as { canRedraftSkills?: boolean }[],
  skillsProps: [] as { reloadKey?: number }[],
  preview: null as unknown as PostingPreview,
}));

vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "job-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
// A STABLE toast: the page's load callback depends on it, and a fresh
// function per render re-runs the load effect for ever.
const toaster = vi.hoisted(() => ({ toast: vi.fn() }));
const activity = vi.hoisted(() => ({ report: vi.fn(), fail: vi.fn() }));
vi.mock("@/components/ui/toast", () => ({ useToast: () => toaster }));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => permissions,
}));
vi.mock("@/components/app-shell", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("@/components/role-type-badge", () => ({ RoleTypeBadge: () => null }));
vi.mock("@/components/posting-window", () => ({ PostingWindowBanner: () => null }));
vi.mock("@/components/assessment-retention-panel", () => ({
  AssessmentRetentionPanel: () => null,
}));
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
vi.mock("@/components/matching-reasoning", () => ({ MatchingReasoning: () => null }));
vi.mock("@/components/ai-activity", () => ({
  AiActivityIndicator: () => null,
  useAiActivity: () => activity,
}));
vi.mock("@/components/proctoring/monitoring-policy-card", () => ({
  MonitoringPolicyCard: () => <section aria-label="Monitoring stub" />,
}));
vi.mock("@/components/job-publish-card", () => ({
  JobPublishCard: ({ onSetup }: { onSetup?: (setup: JobSetupStatus) => void }) => {
    React.useEffect(() => {
      onSetup?.(fixtures.setup);
    }, [onSetup]);
    return <section aria-label="Publish stub" />;
  },
}));
vi.mock("@/components/job-skills", () => ({
  JobSkillsPanel: (props: { onLoaded?: (view: SkillsOut) => void; reloadKey?: number }) => {
    fixtures.skillsProps.push(props);
    const { onLoaded } = props;
    React.useEffect(() => {
      onLoaded?.(fixtures.skills);
    }, [onLoaded]);
    return <section aria-label="Skills stub" />;
  },
}));
vi.mock("@/components/job-swot-analysis", () => ({
  JobSwotAnalysisPanel: (props: { canRedraftSkills?: boolean }) => {
    fixtures.swotProps.push(props);
    return <section aria-label="SWOT stub" />;
  },
}));

import OrgJobDetailPage from "./page";

const JOB = {
  id: "job-1",
  title: "Backend Engineer",
  department: "Platform",
  requirement_period: "Immediate",
  grade: "managerial",
  status: "published",
  experience_min_years: 3,
  experience_max_years: 6,
  jd_markdown: "# Backend Engineer\n\nBuild the ingestion services.",
  jd: { description: "Build the ingestion services.", reporting_to: "CTO" },
  about_company: "We build payroll software.",
  work_life: null,
  benefits: null,
  overridden_sections: [],
};

function setup(overrides: Partial<JobSetupStatus> = {}): JobSetupStatus {
  return {
    job_id: "job-1",
    jd_ready: true,
    swot_status: "not_started",
    swot_saved: false,
    skills_draft_status: "drafted",
    skills_saved: true,
    skills_locked: false,
    grade_locked: false,
    published: true,
    ready_for_candidates: true,
    publish_blocked_reason: null,
    ...overrides,
  };
}

function skills(overrides: Partial<SkillsOut> = {}): SkillsOut {
  return {
    job_id: "job-1",
    draft_status: "drafted",
    draft_error: null,
    saved: true,
    locked: false,
    max_per_bucket: 5,
    redraft_available: false,
    human_authored_names: [],
    blocking_reason: null,
    buckets: {
      must_have: [{ id: "s1", name: "Python", source: "jd", from_swot: null }],
      nice_to_have: [{ id: "s2", name: "Terraform", source: "team", from_swot: null }],
      behavioural: [
        { id: "s3", name: "Owns incidents to closure", source: "jd", from_swot: null },
      ],
    },
    can_edit: { must_have: true, nice_to_have: true, behavioural: true },
    can_save: true,
    ...overrides,
  };
}

afterEach(cleanup);
beforeEach(() => {
  api.apiGet.mockReset();
  api.apiPatch.mockReset();
  api.apiPost.mockReset();
  api.apiGet.mockImplementation(async (path: string) => {
    if (path === "/jobs/job-1") return { ...JOB };
    if (path === "/companies/me/profile") return { company_name: "Acrm Corp" };
    if (path === "/api/v2/assessments/jobs/job-1/posting-preview") return fixtures.preview;
    throw new Error(`unexpected read ${path}`);
  });
  fixtures.setup = setup();
  fixtures.skills = skills();
  fixtures.preview = preview();
  fixtures.swotProps = [];
  fixtures.skillsProps = [];
});

function preview(overrides: Partial<PostingPreview> = {}): PostingPreview {
  return {
    job_id: "job-1",
    title: "Backend Engineer",
    department: "Platform",
    grade: "managerial",
    grade_label: "Managerial",
    experience_band: "3 to 6 years",
    jd_markdown: "Build the ingestion services.",
    company_name: "Acrm Corp",
    about_company: "We build payroll software.",
    work_life: null,
    benefits: null,
    skill_buckets: [
      { bucket: "must_have", label: "Must-have skills", names: ["Python"] },
      { bucket: "nice_to_have", label: "Nice-to-have skills", names: ["Terraform"] },
      {
        bucket: "behavioural",
        label: "Behavioural competencies",
        names: ["Owns incidents to closure"],
      },
    ],
    skills_saved: true,
    published: true,
    public_application_url: null,
    publish_blocked_reason: null,
    frozen: false,
    frozen_at: null,
    frozen_reason: null,
    ...overrides,
  };
}

const previewReads = () =>
  api.apiGet.mock.calls.filter(([path]) => String(path).endsWith("/posting-preview")).length;

/** True when `a` comes before `b` in the document. */
function before(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

async function renderPage() {
  render(<OrgJobDetailPage />);
  await screen.findByRole("heading", { level: 1, name: "Backend Engineer" });
  await screen.findByRole("region", { name: "Publish stub" });
}

describe("the setup chain (unfrozen)", () => {
  it("runs JD, Skills, Final Job Posting, Publish, with the SWOT outside it", async () => {
    await renderPage();

    const jd = screen.getByRole("heading", { level: 3, name: "Job description" });
    const skillsPanel = screen.getByRole("region", { name: "Skills stub" });
    const preview = screen.getByRole("heading", { level: 3, name: "Final job posting" });
    const publish = screen.getByRole("region", { name: "Publish stub" });
    const internal = screen.getByRole("region", { name: "Hiring intelligence (internal)" });

    expect(before(jd, skillsPanel)).toBe(true);
    expect(before(skillsPanel, preview)).toBe(true);
    expect(before(preview, publish)).toBe(true);
    expect(before(publish, internal)).toBe(true);
    // The SWOT sits inside the internal section and nowhere in the chain.
    expect(within(internal).getByRole("region", { name: "SWOT stub" })).toBeTruthy();
    expect(
      within(internal).getByText(
        "Candidates never see this section, and it can be edited at any time, including after the job is frozen."
      )
    ).toBeTruthy();
  });

  it("previews the server's posting and shows no frozen banner", async () => {
    await renderPage();

    const posting = await screen.findByRole("article", { name: "Posting preview" });
    expect(within(posting).getByText("Python")).toBeTruthy();
    expect(within(posting).getByText("Must-have skills")).toBeTruthy();
    expect(within(posting).getByText("Behavioural competencies")).toBeTruthy();
    expect(within(posting).getByText("Acrm Corp")).toBeTruthy();
    expect(screen.queryByText("This job is frozen")).toBeNull();
    expect(screen.getByRole("button", { name: /Edit description/ })).toBeTruthy();
    // Unfrozen, the SWOT may offer a skills re-draft.
    expect(fixtures.swotProps.at(-1)?.canRedraftSkills).toBe(true);
  });

  it("shows a legacy job with no skills honestly in the preview", async () => {
    fixtures.preview = preview({ skill_buckets: [], skills_saved: false });
    await renderPage();

    const posting = await screen.findByRole("article", { name: "Posting preview" });
    expect(within(posting).getByText(/No skills yet\./)).toBeTruthy();
    expect(within(posting).queryByRole("region", { name: "Must-have skills" })).toBeNull();
  });

  it("re-reads the skills and the preview after a JD save (a draft may start)", async () => {
    api.apiPatch.mockResolvedValue({ ...JOB, jd_markdown: "Build the warehouse." });
    await renderPage();
    await screen.findByRole("article", { name: "Posting preview" });
    const readsBefore = previewReads();
    const skillsKeyBefore = fixtures.skillsProps.at(-1)?.reloadKey ?? 0;

    fireEvent.click(screen.getByRole("button", { name: /Edit description/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save description" }));

    await waitFor(() =>
      expect(fixtures.skillsProps.at(-1)?.reloadKey).toBe(skillsKeyBefore + 1)
    );
    await waitFor(() => expect(previewReads()).toBeGreaterThan(readsBefore));
  });
});

describe("a frozen job", () => {
  const FROZEN_AT = "2026-09-28T10:15:00Z";
  // The server's own sentence (s4-api-shapes.md section 2), carrying its date.
  const SENTENCE =
    "The job description and skills are frozen because a candidate has applied. Frozen since 28 Sep 2026.";

  beforeEach(() => {
    fixtures.setup = setup({
      skills_locked: true,
      grade_locked: true,
      frozen: true,
      frozen_at: FROZEN_AT,
      frozen_reason: SENTENCE,
    });
    fixtures.skills = skills({ locked: true });
  });

  it("shows the server's sentence, with its date, in a banner", async () => {
    await renderPage();

    const banner = (await screen.findByText("This job is frozen")).closest(
      "[role='status']"
    ) as HTMLElement;
    expect(within(banner).getByText(SENTENCE)).toBeTruthy();
  });

  it("says the date from frozen_at when the server sends no sentence", async () => {
    fixtures.setup = setup({
      skills_locked: true,
      grade_locked: true,
      frozen: true,
      frozen_at: FROZEN_AT,
    });
    await renderPage();

    const expectedDate = new Date(FROZEN_AT).toLocaleDateString(undefined, {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
    expect(await screen.findByText("This job is frozen")).toBeTruthy();
    expect(screen.getByText(`Frozen on ${expectedDate}.`)).toBeTruthy();
    // The retired assessment-start sentences are never hard-coded again.
    expect(screen.queryByText(/started the assessment/)).toBeNull();
  });

  it("reads a frozen job from skills_locked on a server without the frozen flag", async () => {
    fixtures.setup = setup({ skills_locked: true, grade_locked: true });
    await renderPage();
    expect(await screen.findByText("This job is frozen")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Edit description/ })).toBeNull();
  });

  it("offers no JD edit, and keeps the title, band and grade read-only", async () => {
    await renderPage();
    await screen.findByText("This job is frozen");

    expect(screen.queryByRole("button", { name: /Edit description/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Edit details/ }));
    expect((screen.getByLabelText(/^Title/) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/Experience from/) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/Experience to/) as HTMLInputElement).disabled).toBe(true);
    expect((document.getElementById("job-grade") as HTMLButtonElement).disabled).toBe(true);
    // Each frozen field says why in the server's own sentence.
    expect(screen.getAllByText(SENTENCE).length).toBeGreaterThanOrEqual(4);
    // The company sections are not frozen.
    expect((screen.getByLabelText(/About company/) as HTMLTextAreaElement).disabled).toBe(false);
    expect((screen.getByLabelText(/Department/) as HTMLInputElement).disabled).toBe(false);
  });

  it("saves the details without sending a single frozen field", async () => {
    api.apiPatch.mockResolvedValue({ ...JOB, about_company: "We build payroll and HR software." });
    await renderPage();
    await screen.findByText("This job is frozen");

    fireEvent.click(screen.getByRole("button", { name: /Edit details/ }));
    fireEvent.change(screen.getByLabelText(/About company/), {
      target: { value: "We build payroll and HR software." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));

    await waitFor(() => expect(api.apiPatch).toHaveBeenCalledTimes(1));
    const [path, body] = api.apiPatch.mock.calls[0];
    expect(path).toBe("/jobs/job-1");
    expect(body).toEqual(
      expect.objectContaining({ about_company: "We build payroll and HR software." })
    );
    for (const frozenField of [
      "title",
      "experience_min_years",
      "experience_max_years",
      "grade",
    ]) {
      expect(body).not.toHaveProperty(frozenField);
    }
  });

  it("keeps the SWOT editable in its internal section, and withholds the re-draft offer", async () => {
    await renderPage();
    await screen.findByText("This job is frozen");

    const internal = screen.getByRole("region", { name: "Hiring intelligence (internal)" });
    expect(within(internal).getByRole("region", { name: "SWOT stub" })).toBeTruthy();
    expect(fixtures.swotProps.at(-1)?.canRedraftSkills).toBe(false);
  });
});

describe("the unfrozen details save", () => {
  it("still sends the title and the band", async () => {
    api.apiPatch.mockResolvedValue({ ...JOB });
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: /Edit details/ }));
    expect((screen.getByLabelText(/^Title/) as HTMLInputElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save details" }));

    await waitFor(() => expect(api.apiPatch).toHaveBeenCalledTimes(1));
    expect(api.apiPatch.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        title: "Backend Engineer",
        experience_min_years: 3,
        experience_max_years: 6,
      })
    );
  });
});
