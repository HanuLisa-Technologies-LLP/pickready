// @vitest-environment jsdom
//
// Leadership Intelligence (2026-09-29, spec 17 and 28). What the screen owns:
// a Functional Head's department is shown and never sent, an AI draft fills
// the fields and is marked as NOT SAVED with the sources it read, nothing is
// saved until Save is pressed, the save carries which draft it started from,
// and the lines the server did not use come back in words. The rules
// themselves (whose stream, which department, what is refused) are the
// server's and are tested there.

import * as React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast: vi.fn() }) }));
// The shell's header pulls the sign-in client; what is under test is the form.
vi.mock("@/components/app-shell", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("@/lib/use-permissions", () => ({
  usePermissions: () => ({ can: () => true, loading: false, capabilities: [] }),
}));

import {
  LeadershipEditor,
  REASON_WORDS,
  type LeadershipMe,
} from "./leadership-intelligence";

const HEAD: LeadershipMe = {
  can_author: true,
  refusal: null,
  author_role: "functional_head",
  author_label: "Functional Head",
  department: { id: "dep-eng", name: "Engineering" },
  departments: [],
  profile: null,
};

const CEO: LeadershipMe = {
  can_author: true,
  refusal: null,
  author_role: "ceo",
  author_label: "CEO",
  department: null,
  departments: [
    { id: "dep-eng", name: "Engineering" },
    { id: "dep-fin", name: "Finance" },
  ],
  profile: null,
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  api.apiGet.mockReset();
  api.apiPost.mockReset();
  api.apiPut.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("a Functional Head's page", () => {
  it("names the fixed department and never sends one", async () => {
    api.apiPut.mockResolvedValue({ ...HEAD, profile: null });
    render(<LeadershipEditor me={HEAD} onSaved={() => undefined} />);
    expect(screen.getByText("Leadership Intelligence, Engineering")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Department requirements"), {
      target: { value: "We need engineers who have shipped services to production." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.apiPut).toHaveBeenCalled());
    const [path, body] = api.apiPut.mock.calls[0];
    expect(path).toBe("/leadership/me");
    expect(body).not.toHaveProperty("department_id");
    expect(body).not.toHaveProperty("department_expectations");
    expect(body).not.toHaveProperty("company_requirements");
    expect(body.ai_draft_sources).toBeNull();
  });
});

describe("the AI draft", () => {
  it("fills the fields, says it is not saved, lists its sources, and saves nothing by itself", async () => {
    api.apiPost.mockResolvedValue({ task_id: "run-1" });
    api.apiGet
      .mockResolvedValueOnce({ status: "pending", fields: {}, sources: [], message: null, generated_by_ai: false })
      .mockResolvedValueOnce({
        status: "drafted",
        fields: {
          department_requirements: "We need engineers who run services in production.",
          ideal_employee_expectations: "A new engineer has shipped a service end to end.",
        },
        sources: [
          { key: "company_profile", label: "Company Profile" },
          { key: "department_jobs", label: "Existing jobs" },
        ],
        message: null,
        generated_by_ai: true,
      });
    render(<LeadershipEditor me={HEAD} onSaved={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate draft with AI/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await waitFor(() => expect(screen.getByTestId("ai-draft-badge").textContent).toBe("AI draft, not saved"));
    expect(
      (screen.getByLabelText("Department requirements") as HTMLTextAreaElement).value
    ).toBe("We need engineers who run services in production.");
    expect(screen.getByTestId("draft-sources").textContent).toContain("Company Profile");
    expect(screen.getByTestId("draft-sources").textContent).toContain("Existing jobs");
    // Rule 37.11: nothing was saved by the draft.
    expect(api.apiPut).not.toHaveBeenCalled();

    api.apiPut.mockResolvedValue({ ...HEAD });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.apiPut).toHaveBeenCalledTimes(1));
    expect(api.apiPut.mock.calls[0][1].ai_draft_sources).toEqual(["company_profile", "department_jobs"]);
  });

  it("shows the server's sentence when there is nothing to draft from", async () => {
    api.apiPost.mockResolvedValue({ task_id: "run-2" });
    api.apiGet.mockResolvedValue({
      status: "empty",
      fields: {},
      sources: [],
      message: "Write this section yourself.",
      generated_by_ai: false,
    });
    render(<LeadershipEditor me={HEAD} onSaved={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: /Generate draft with AI/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Write this section yourself."));
    expect(screen.queryByTestId("ai-draft-badge")).toBeNull();
  });
});

describe("a CEO's page", () => {
  it("offers one field per department and sends them by id", async () => {
    api.apiPut.mockResolvedValue({ ...CEO });
    render(<LeadershipEditor me={CEO} onSaved={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Finance"), {
      target: { value: "Finance closes the books on a fixed calendar every month." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.apiPut).toHaveBeenCalled());
    expect(api.apiPut.mock.calls[0][1].department_expectations).toEqual({
      "dep-fin": "Finance closes the books on a fixed calendar every month.",
    });
    expect(api.apiPut.mock.calls[0][1]).not.toHaveProperty("department_requirements");
  });

  it("shows the lines the server did not use, in words", () => {
    render(
      <LeadershipEditor
        me={{
          ...CEO,
          profile: {
            id: "p1",
            author_role: "ceo",
            author_label: "CEO",
            department_id: null,
            version: 2,
            saved_by: "Asha",
            saved_at: "2026-09-29T10:00:00Z",
            company_requirements: "We need aggressive people.",
            department_requirements: "",
            ideal_employee_expectations: "",
            department_expectations: {},
            lines_not_used: [{ text: "We need aggressive people.", reason: "not_observable" }],
            used_ai_draft: false,
          },
        }}
        onSaved={() => undefined}
      />
    );
    const notUsed = screen.getByTestId("lines-not-used").textContent ?? "";
    expect(notUsed).toContain("We need aggressive people.");
    expect(notUsed).toContain(REASON_WORDS.not_observable);
    expect(screen.getByText(/saved by Asha/)).toBeTruthy();
  });
});
