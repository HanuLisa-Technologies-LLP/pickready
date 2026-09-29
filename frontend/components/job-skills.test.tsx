// @vitest-environment jsdom
//
// The Skills step (ReadyPick release, Phase 1). What the interface owns, and
// therefore what is tested here: every action reaches the right route with the
// right body, a refusal is shown in the server's own words, the locked state is
// a state sentence rather than a permission sentence, a re-draft never happens
// without a click that named the team's own skills, and nothing on the panel is
// a digit. The refusals themselves are the API's job and are tested there.

import * as React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { READ_ONLY_TITLE } from "@/lib/permissions";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    detail: unknown;
    constructor(status: number, detail: unknown, message = `API error ${status}`) {
      super(message);
      this.status = status;
      this.detail = detail;
    }
  }
  return {
    apiGet: vi.fn(),
    apiPost: vi.fn(),
    apiPatch: vi.fn(),
    apiDelete: vi.fn(),
    ApiError,
  };
});
const { apiGet, apiPost, apiPatch, apiDelete, ApiError } = api;

vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({ can: () => false, loading: false, capabilities: [] }),
}));

import {
  JobSkillsPanel,
  bucketCount,
  parseNames,
  serverSentences,
  type SkillsOut,
} from "./job-skills";

const MOUNT = "/api/v2/assessments/jobs";

function skills(overrides: Partial<SkillsOut> = {}): SkillsOut {
  return {
    job_id: "job-1",
    draft_status: "drafted",
    draft_error: null,
    saved: false,
    locked: false,
    max_per_bucket: 5,
    redraft_available: false,
    human_authored_names: [],
    blocking_reason: null,
    buckets: {
      must_have: [
        { id: "s1", name: "Python", source: "jd", from_swot: null },
        {
          id: "s2",
          name: "Kafka operations",
          source: "swot",
          from_swot: "Nobody on the team can run Kafka.",
        },
      ],
      nice_to_have: [{ id: "s3", name: "Terraform", source: "team", from_swot: null }],
      behavioural: [
        { id: "s4", name: "Owns incidents to closure", source: "swot", from_swot: null },
      ],
    },
    can_edit: { must_have: true, nice_to_have: true, behavioural: true },
    can_save: true,
    ...overrides,
  };
}

function refusal(status: number, detail: unknown) {
  return new ApiError(status, { detail });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiDelete.mockReset();
});

function bucket(name: string) {
  return screen.getByRole("region", { name });
}

describe("pure helpers", () => {
  it("counts in words, never digits", () => {
    expect(bucketCount(3, 5)).toBe("Three of five");
    expect(bucketCount(0, 5)).toBe("None of five");
    expect(bucketCount(6, 5)).toBe("Six of five");
    expect(bucketCount(40, 5)).not.toMatch(/\d/);
  });

  it("splits a pasted list on newlines and commas, dropping blanks and repeats", () => {
    expect(parseNames("Python, SQL\n\nPython\n DSA ,")).toEqual(["Python", "SQL", "DSA"]);
  });

  it("returns the server's sentences verbatim in every shape the routes send", () => {
    expect(serverSentences(refusal(409, "Must-have holds at most five skills."))).toEqual([
      "Must-have holds at most five skills.",
    ]);
    expect(
      serverSentences(refusal(422, ["Add at least one Must-have skill.", "Add at least one Behavioural skill."]))
    ).toEqual(["Add at least one Must-have skill.", "Add at least one Behavioural skill."]);
    expect(
      serverSentences(
        refusal(422, {
          message: "These skills could not be described.",
          refused: [{ name: "Synergy", reason: "It names no observable behaviour." }],
        })
      )
    ).toEqual([
      "These skills could not be described.",
      "Synergy: It names no observable behaviour.",
    ]);
  });
});

describe("a user who may edit every bucket", () => {
  it("shows three buckets with spelled-out counters and no digit anywhere", async () => {
    apiGet.mockResolvedValue(skills());
    const { container } = render(<JobSkillsPanel jobId="job-1" />);

    await screen.findByText("Python");
    expect(apiGet).toHaveBeenCalledWith(`${MOUNT}/job-1/skills`);
    expect(within(bucket("Must-have")).getByText("Two of five")).toBeTruthy();
    expect(within(bucket("Nice-to-have")).getByText("One of five")).toBeTruthy();
    expect(within(bucket("Behavioural")).getByText("One of five")).toBeTruthy();
    // Provenance is a word; the SWOT quote is the reporting authority's own.
    expect(screen.getAllByText("From the SWOT")[0].getAttribute("title")).toBe(
      'You said: "Nobody on the team can run Kafka."'
    );
    // No priority, no weight, no grade, no count as a digit.
    expect(container.textContent ?? "").not.toMatch(/\d/);
    expect(screen.queryByText(READ_ONLY_TITLE)).toBeNull();
  });

  it("adds one skill to the bucket it was typed into", async () => {
    apiGet.mockResolvedValue(skills());
    apiPost.mockResolvedValue(skills());
    const onChanged = vi.fn();
    render(<JobSkillsPanel jobId="job-1" onChanged={onChanged} />);
    await screen.findByText("Python");

    fireEvent.change(screen.getByLabelText("Add a Nice-to-have skill"), {
      target: { value: "  Kubernetes " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add to Nice-to-have" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith(`${MOUNT}/job-1/skills`, {
      bucket: "nice_to_have",
      name: "Kubernetes",
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("pastes a list through the bulk route as one request", async () => {
    apiGet.mockResolvedValue(skills());
    apiPost.mockResolvedValue(skills());
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    const mustHave = bucket("Must-have");
    fireEvent.click(within(mustHave).getByRole("button", { name: "Paste a list" }));
    fireEvent.change(within(mustHave).getByLabelText("Paste a list of Must-have skills"), {
      target: { value: "SQL\nDSA, Agentic AI\nSQL" },
    });
    fireEvent.click(within(mustHave).getByRole("button", { name: "Add the list" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(1));
    expect(apiPost).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/bulk`, {
      bucket: "must_have",
      names: ["SQL", "DSA", "Agentic AI"],
    });
  });

  it("renames, moves and removes through the one skill route", async () => {
    apiGet.mockResolvedValue(skills());
    apiPatch.mockResolvedValue(skills());
    apiDelete.mockResolvedValue(skills());
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    fireEvent.click(screen.getByRole("button", { name: "Rename Python" }));
    fireEvent.change(screen.getByLabelText("Rename Python"), {
      target: { value: "Python services" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save the new name" }));
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/s1`, {
        name: "Python services",
      })
    );

    // Behavioural IS a valid destination now: Miti grades every skill from
    // answers, so the old rubric-versus-judgement objection is gone.
    fireEvent.click(await screen.findByRole("button", { name: "Move Terraform" }));
    fireEvent.click(screen.getByRole("button", { name: "Move to Behavioural" }));
    await waitFor(() =>
      expect(apiPatch).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/s3`, {
        bucket: "behavioural",
      })
    );

    fireEvent.click(await screen.findByRole("button", { name: "Remove Python" }));
    await waitFor(() =>
      expect(apiDelete).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/s1`)
    );
  });

  it("does not offer a move into a full bucket, and hides the add line there", async () => {
    const five = ["A", "B", "C", "D", "E"].map((name, i) => ({
      id: `n${i}`,
      name,
      source: "team" as const,
      from_swot: null,
    }));
    apiGet.mockResolvedValue(
      skills({ buckets: { ...skills().buckets, nice_to_have: five } })
    );
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    expect(within(bucket("Nice-to-have")).getByText("Five of five")).toBeTruthy();
    expect(within(bucket("Nice-to-have")).queryByLabelText("Add a Nice-to-have skill")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Move Python" }));
    const full = screen.getByRole("button", { name: "Nice-to-have is full" });
    expect((full as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a clash refusal in the server's words and re-reads the skills", async () => {
    apiGet.mockResolvedValue(skills());
    apiPatch.mockRejectedValueOnce(
      refusal(409, '"Kafka operations" is already an entry under Must-have on this job.')
    );
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    fireEvent.click(screen.getByRole("button", { name: "Rename Python" }));
    fireEvent.change(screen.getByLabelText("Rename Python"), {
      target: { value: "Kafka operations" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save the new name" }));

    expect(
      await screen.findByText(
        '"Kafka operations" is already an entry under Must-have on this job.'
      )
    ).toBeTruthy();
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
  });

  it("saves, and shows a validation refusal naming every problem", async () => {
    apiGet.mockResolvedValue(skills());
    apiPost.mockRejectedValueOnce(
      refusal(422, [
        "Must-have holds at most five skills; it has six.",
        "Add at least one Behavioural skill.",
      ])
    );
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    fireEvent.click(screen.getByRole("button", { name: "Save skills" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/save`)
    );
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Must-have holds at most five skills; it has six.")).toBeTruthy();
    expect(within(alert).getByText("Add at least one Behavioural skill.")).toBeTruthy();
  });

  it("shows an outage on save as the server's try-again sentence, naming no skill", async () => {
    const outage =
      "Skills could not be saved because the assessment writer is unavailable. Nothing was changed. Try again in a moment.";
    apiGet.mockResolvedValue(skills());
    apiPost.mockRejectedValueOnce(refusal(503, outage));
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    fireEvent.click(screen.getByRole("button", { name: "Save skills" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(outage);
  });

  it("marks the skills saved once the server says so", async () => {
    apiGet.mockResolvedValue(skills());
    apiPost.mockResolvedValueOnce(skills({ saved: true }));
    const onChanged = vi.fn();
    render(<JobSkillsPanel jobId="job-1" onChanged={onChanged} />);
    await screen.findByText("Python");

    fireEvent.click(screen.getByRole("button", { name: "Save skills" }));
    expect(
      await screen.findByText(
        "Saved. Every candidate on this job is assessed against these skills."
      )
    ).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
  });
});

describe("the freeze", () => {
  const FROZEN =
    "The job description and skills are frozen because a candidate has applied. Frozen since 28 Sep 2026.";

  it("is the server's state sentence with no edit or save controls and no permission copy", async () => {
    apiGet.mockResolvedValue(skills({ locked: true, saved: true, frozen_reason: FROZEN }));
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    expect(screen.getByText(FROZEN)).toBeTruthy();
    // The retired assessment-start sentence is never hard-coded again.
    expect(screen.queryByText(/started the assessment/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Rename/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Move/ })).toBeNull();
    expect(screen.queryByLabelText(/Add a/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Save skills/ })).toBeNull();
    expect(screen.queryByText(READ_ONLY_TITLE)).toBeNull();
  });

  it("wins over a stale can_edit answer, and says frozen even without a sentence", async () => {
    apiGet.mockResolvedValue(skills({ locked: true }));
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");
    expect(screen.queryByRole("button", { name: "Rename Python" })).toBeNull();
    expect(screen.getByText("Frozen. These skills can no longer change.")).toBeTruthy();
  });

  it("withholds the re-draft call to action and every draft control once frozen", async () => {
    // A stale redraft_available must not reopen a frozen contract (v10).
    apiGet.mockResolvedValue(
      skills({ locked: true, saved: true, redraft_available: true })
    );
    render(<JobSkillsPanel jobId="job-1" redraftSignal={0} />);
    await screen.findByText("Python");

    expect(screen.getByText("Frozen")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Re-draft skills from the updated SWOT/ })
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /Draft skills/ })).toBeNull();
  });

  it("does not draft even when the SWOT panel signals after the freeze", async () => {
    apiGet.mockResolvedValue(skills({ locked: true, redraft_available: true }));
    const { rerender } = render(<JobSkillsPanel jobId="job-1" redraftSignal={0} />);
    await screen.findByText("Python");

    rerender(<JobSkillsPanel jobId="job-1" redraftSignal={1} />);
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).queryByRole("button", { name: "Replace with a new draft" })
    ).toBeNull();
    expect(apiPost).not.toHaveBeenCalled();
  });
});

describe("drafting without a SWOT (CONTRACT v10)", () => {
  const empty = () =>
    skills({
      draft_status: "not_started",
      buckets: { must_have: [], nice_to_have: [], behavioural: [] },
    });

  it("offers Draft skills on an empty list and says the SWOT is optional", async () => {
    apiGet.mockResolvedValue(empty());
    render(<JobSkillsPanel jobId="job-1" />);

    expect(
      await screen.findByText(
        "No skills yet. Draft them from the JD, or add them yourself. A SWOT is optional."
      )
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Draft skills" })).toBeTruthy();
    // Nothing on the list is a SWOT prerequisite.
    expect(screen.queryByText(/Save the SWOT/)).toBeNull();
  });

  it("drafts on the click, with no confirmation, because nothing would be replaced", async () => {
    apiGet.mockResolvedValue(empty());
    apiPost.mockResolvedValue({ ...empty(), draft_status: "drafting" });
    render(<JobSkillsPanel jobId="job-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Draft skills" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/draft`, {
        confirm_overwrite: false,
      })
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      await screen.findByText(
        "Sutra is drafting the skills from the JD and the Company Profile, and from the saved SWOT when there is one."
      )
    ).toBeTruthy();
  });

  it("offers no Draft skills once there are skills, or to somebody who cannot edit every list", async () => {
    apiGet.mockResolvedValue(skills({ draft_status: "not_started" }));
    const { unmount } = render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");
    expect(screen.queryByRole("button", { name: "Draft skills" })).toBeNull();
    unmount();

    apiGet.mockResolvedValue({
      ...empty(),
      can_edit: { must_have: true, nice_to_have: true, behavioural: false },
    });
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText(
      "No skills yet. Draft them from the JD, or add them yourself. A SWOT is optional."
    );
    expect(screen.queryByRole("button", { name: "Draft skills" })).toBeNull();
  });

  it("shows why a draft cannot run yet, in the server's words, and offers no draft", async () => {
    const thin =
      "Write the job description first. The skills are drafted from it, so it needs a title and a few paragraphs describing the role.";
    apiGet.mockResolvedValue({ ...empty(), draft_blocked_reason: thin });
    render(<JobSkillsPanel jobId="job-1" />);

    expect(await screen.findByText(thin)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Draft skills" })).toBeNull();
    // Adding by hand is still open.
    expect(screen.getByLabelText("Add a Must-have skill")).toBeTruthy();
  });

  it("does not offer Draft again after a thin-JD failure", async () => {
    const thin =
      "Write the job description first. The skills are drafted from it, so it needs a title and a few paragraphs describing the role.";
    apiGet.mockResolvedValue({
      ...empty(),
      draft_status: "failed",
      draft_error: thin,
      draft_blocked_reason: thin,
    });
    render(<JobSkillsPanel jobId="job-1" />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(thin)).toBeTruthy();
    expect(within(alert).queryByRole("button", { name: "Draft again" })).toBeNull();
  });

  it("hands every view to the page for the Final Job Posting preview", async () => {
    apiGet.mockResolvedValue(skills());
    const onLoaded = vi.fn();
    render(<JobSkillsPanel jobId="job-1" onLoaded={onLoaded} />);
    await screen.findByText("Python");
    await waitFor(() =>
      expect(onLoaded).toHaveBeenCalledWith(
        expect.objectContaining({ job_id: "job-1", saved: false })
      )
    );
  });
});

describe("a user who may view but not edit", () => {
  it("reads the skills and is told why there are no controls", async () => {
    apiGet.mockResolvedValue(
      skills({
        can_edit: { must_have: false, nice_to_have: false, behavioural: false },
        can_save: false,
      })
    );
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    expect(screen.getByText(READ_ONLY_TITLE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Rename/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Save skills/ })).toBeNull();
  });

  it("is told per bucket when only some buckets are theirs", async () => {
    apiGet.mockResolvedValue(
      skills({ can_edit: { must_have: true, nice_to_have: true, behavioural: false } })
    );
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    expect(within(bucket("Behavioural")).getByText(READ_ONLY_TITLE)).toBeTruthy();
    expect(within(bucket("Must-have")).queryByText(READ_ONLY_TITLE)).toBeNull();
    // A move is offered only into a bucket the user may also edit.
    fireEvent.click(screen.getByRole("button", { name: "Move Python" }));
    expect(screen.queryByRole("button", { name: "Move to Behavioural" })).toBeNull();
    expect(screen.getByRole("button", { name: "Move to Nice-to-have" })).toBeTruthy();
  });
});

describe("re-drafting", () => {
  it("asks first, names the team's own skills, and only then confirms the overwrite", async () => {
    apiGet.mockResolvedValue(
      skills({ redraft_available: true, human_authored_names: ["Terraform"] })
    );
    apiPost.mockResolvedValue(skills({ draft_status: "drafting" }));
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    fireEvent.click(
      screen.getByRole("button", { name: /Re-draft skills from the updated SWOT/ })
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Terraform")).toBeTruthy();
    expect(apiPost).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Replace with a new draft" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/draft`, {
        confirm_overwrite: true,
      })
    );
  });

  it("keeping the current skills sends nothing", async () => {
    apiGet.mockResolvedValue(skills({ redraft_available: true }));
    render(<JobSkillsPanel jobId="job-1" />);
    await screen.findByText("Python");

    fireEvent.click(
      screen.getByRole("button", { name: /Re-draft skills from the updated SWOT/ })
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Keep the current skills" })
    );
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("opens the same confirmation when the SWOT panel asks, and never drafts on its own", async () => {
    apiGet.mockResolvedValue(skills({ redraft_available: true }));
    const { rerender } = render(<JobSkillsPanel jobId="job-1" redraftSignal={0} />);
    await screen.findByText("Python");
    expect(screen.queryByRole("dialog")).toBeNull();

    rerender(<JobSkillsPanel jobId="job-1" redraftSignal={1} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Re-draft the skills?")).toBeTruthy();
    expect(apiPost).not.toHaveBeenCalled();

    apiPost.mockResolvedValue(skills({ draft_status: "drafting" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Replace with a new draft" }));
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(`${MOUNT}/job-1/skills/draft`, {
        confirm_overwrite: false,
      })
    );
  });

  it("renders a failed draft as the server's sentence, never as template skills", async () => {
    apiGet.mockResolvedValue(
      skills({
        draft_status: "failed",
        draft_error: "The skills writer could not be reached. Draft again in a moment.",
        buckets: { must_have: [], nice_to_have: [], behavioural: [] },
      })
    );
    render(<JobSkillsPanel jobId="job-1" />);

    expect(
      await screen.findByText("The skills writer could not be reached. Draft again in a moment.")
    ).toBeTruthy();
    expect(screen.getAllByText("No skills in this list yet.")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Draft again" })).toBeTruthy();
  });
});

describe("a draft in progress", () => {
  it("disables editing and re-reads until the draft lands", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    apiGet
      .mockResolvedValueOnce(
        skills({
          draft_status: "drafting",
          buckets: { must_have: [], nice_to_have: [], behavioural: [] },
        })
      )
      .mockResolvedValue(skills());
    render(<JobSkillsPanel jobId="job-1" />);

    expect(
      await screen.findByText(
        "Sutra is drafting the skills from the JD and the Company Profile, and from the saved SWOT when there is one."
      )
    ).toBeTruthy();
    expect(screen.queryByLabelText(/Add a/)).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(3100);
    });
    expect(await screen.findByText("Python")).toBeTruthy();
    expect(apiGet).toHaveBeenCalledTimes(2);
  });
});
