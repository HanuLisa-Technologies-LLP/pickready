// @vitest-environment jsdom

/**
 * The AI activity layer's client-side rules.
 *
 * The copy is the backend's and is swept there (`tests/test_ai_activity.py`).
 * What is tested here is what only exists on this side: which of two concurrent
 * operations a payload belongs to, that a failure removes the running line
 * instead of sitting beside it, and that a fast operation does not flash.
 */
import * as React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  activitySentence,
  belongsToOperation,
  isNewerLine,
  resolveActivityState,
  type AiActivityLine,
  type AiActivityPayload,
} from "@/lib/ai-activity";
import { AiActivityIndicator } from "./ai-activity-indicator";
import {
  APPEAR_AFTER_MS,
  MIN_LINE_MS,
  useAiActivity,
  type AiActivityView,
} from "./use-ai-activity";

afterEach(cleanup);

function makeLine(over: Partial<AiActivityLine> = {}): AiActivityLine {
  return {
    operation_id: "run-a",
    task: "job_candidate_matching",
    kind: "SKILLS_COMPARED",
    sequence: 1,
    text: "Assessing each candidate against this job's own matching categories.",
    detail: "",
    source: "event",
    terminal: false,
    ...over,
  };
}

function makePayload(over: Partial<AiActivityPayload> = {}): AiActivityPayload {
  return {
    operation_id: "run-a",
    task: "job_candidate_matching",
    label: "Matching candidates to this job",
    state: "running",
    line: makeLine(),
    log: [],
    dropped_after_terminal: 0,
    ...over,
  };
}

// ── The pure rules ──────────────────────────────────────────────────────────

describe("attribution", () => {
  it("refuses a payload belonging to another operation", () => {
    expect(belongsToOperation(makePayload(), "run-a")).toBe(true);
    expect(belongsToOperation(makePayload({ operation_id: "run-b" }), "run-a")).toBe(
      false
    );
  });

  it("accepts nothing while no operation is being watched", () => {
    expect(belongsToOperation(makePayload(), "")).toBe(false);
    expect(belongsToOperation(null, "run-a")).toBe(false);
  });

  it("never walks the activity backwards when polls arrive out of order", () => {
    const first = makeLine({ sequence: 4 });
    expect(isNewerLine(makeLine({ sequence: 5 }), first)).toBe(true);
    expect(isNewerLine(makeLine({ sequence: 3 }), first)).toBe(false);
    // A different operation starts its own numbering, so its first line is new.
    expect(
      isNewerLine(makeLine({ operation_id: "run-b", sequence: 1 }), first)
    ).toBe(true);
  });
});

describe("state resolution", () => {
  it("lets a transport failure override a stream that never heard about it", () => {
    // The activity stream lives inside the process that died, so it cannot
    // report its own death. The run status can.
    expect(resolveActivityState(makePayload({ state: "running" }), "FAILURE")).toBe(
      "error"
    );
  });

  it("keeps a recorded error visible even when the transport succeeded", () => {
    expect(resolveActivityState(makePayload({ state: "error" }), "SUCCESS")).toBe(
      "error"
    );
  });

  it("is idle when nothing has been reported", () => {
    expect(resolveActivityState(null, "PENDING")).toBe("idle");
  });
});

describe("the sentence", () => {
  it("prefers the finding the workflow computed over the catalogue line", () => {
    const line = makeLine({ detail: "12 resumes matched on meaning." });
    expect(activitySentence(line)).toBe("12 resumes matched on meaning.");
  });

  it("falls back to the catalogue line when the step found nothing to report", () => {
    expect(activitySentence(makeLine())).toBe(makeLine().text);
  });
});

// ── The hook ────────────────────────────────────────────────────────────────

function Harness({
  operationId,
  onView,
}: {
  operationId: string;
  onView: (view: AiActivityView) => void;
}) {
  const view = useAiActivity(operationId);
  onView(view);
  return <AiActivityIndicator activity={view} />;
}

describe("useAiActivity", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function mount(operationId: string) {
    let view!: AiActivityView;
    const { container } = render(
      <Harness
        operationId={operationId}
        onView={(v) => {
          view = v;
        }}
      />
    );
    // The sentence appears twice by design: once as visible text and once in
    // the visually-hidden live region. Assert on the rendered text as a whole
    // rather than on one node, so the duplication cannot make a test pass or
    // fail for the wrong reason.
    return { view: () => view, text: () => container.textContent ?? "" };
  }

  it("shows nothing while a fast operation is inside the flicker guard", () => {
    const { view, text } = mount("run-a");
    act(() => view().report(makePayload(), "PROGRESS"));
    expect(text()).toBe("");

    act(() => {
      vi.advanceTimersByTime(APPEAR_AFTER_MS + 10);
    });
    expect(text()).toContain("Assessing each candidate");
  });

  it("holds a line long enough to be read before the next one replaces it", () => {
    const { view, text } = mount("run-a");
    act(() => view().report(makePayload(), "PROGRESS"));
    act(() => {
      vi.advanceTimersByTime(APPEAR_AFTER_MS + 10);
    });

    act(() =>
      view().report(
        makePayload({
          line: makeLine({
            sequence: 2,
            kind: "RECOMMENDATIONS_GENERATED",
            text: "Writing the rated comment for each category.",
          }),
        }),
        "PROGRESS"
      )
    );
    // Still the first sentence: the second was produced, not discarded.
    expect(text()).toContain("Assessing each candidate");

    act(() => {
      vi.advanceTimersByTime(MIN_LINE_MS);
    });
    expect(text()).toContain("Writing the rated comment");
  });

  it("shows a failure at once and stops describing the step that was running", () => {
    const { view, text } = mount("run-a");
    act(() => view().report(makePayload(), "PROGRESS"));
    act(() => {
      vi.advanceTimersByTime(APPEAR_AFTER_MS + 10);
    });
    expect(text()).toContain("Assessing each candidate");

    act(() => view().report(makePayload(), "FAILURE"));
    expect(text()).not.toContain("Assessing each candidate");
    expect(text()).toMatch(/could not finish/i);
  });

  it("ends the activity when the caller cancels", () => {
    const { view, text } = mount("run-a");
    act(() => view().report(makePayload(), "PROGRESS"));
    act(() => view().cancel());
    expect(view().state).toBe("cancelled");
    expect(text()).toMatch(/stopped before it finished/i);
  });

  it("ignores a payload stamped with another operation", () => {
    const { view, text } = mount("run-a");
    act(() =>
      view().report(
        makePayload({
          operation_id: "run-b",
          line: makeLine({
            operation_id: "run-b",
            text: "Somebody else's run.",
          }),
        }),
        "PROGRESS"
      )
    );
    act(() => {
      vi.advanceTimersByTime(APPEAR_AFTER_MS + 10);
    });
    expect(text()).not.toContain("Somebody else's run.");
    expect(view().state).toBe("idle");
  });

  it("does not report a run that never started", () => {
    const { view, text } = mount("run-a");
    expect(view().state).toBe("idle");
    expect(view().sentence).toBe("");
    expect(text()).toBe("");
  });
});

// ── The indicator ───────────────────────────────────────────────────────────

function staticView(over: Partial<AiActivityView> = {}): AiActivityView {
  return {
    operationId: "run-a",
    state: "running",
    line: makeLine(),
    sentence: makeLine().text,
    label: "Matching candidates to this job",
    visible: true,
    report: () => undefined,
    fail: () => undefined,
    cancel: () => undefined,
    ...over,
  };
}

describe("AiActivityIndicator", () => {
  it("announces politely rather than assertively", () => {
    const { container } = render(
      <AiActivityIndicator activity={staticView()} />
    );
    const live = container.querySelector("[aria-live]");
    expect(live?.getAttribute("aria-live")).toBe("polite");
  });

  it("renders no progress bar and no percentage", () => {
    const { container } = render(
      <AiActivityIndicator activity={staticView()} />
    );
    expect(container.querySelector("progress")).toBeNull();
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
    expect(container.textContent).not.toMatch(/%/);
  });

  it("uses the screen's own error sentence rather than inventing one", () => {
    render(
      <AiActivityIndicator
        activity={staticView({ state: "error" })}
        errorMessage="AI matching is taking longer than expected. Refresh to check its results."
      />
    );
    expect(screen.getAllByText(/taking longer than expected/).length).toBe(2);
  });

  it("marks a line that carries a computed finding as the evidenced one", () => {
    // DESIGN.md: teal is evidence. A step merely running is navy.
    const { container } = render(
      <AiActivityIndicator
        activity={staticView({
          line: makeLine({ detail: "12 resumes matched on meaning." }),
          sentence: "12 resumes matched on meaning.",
        })}
      />
    );
    expect(container.innerHTML).toContain("bg-teal-600");
  });

  it("keeps every animation behind a reduced-motion guard", () => {
    const { container } = render(
      <AiActivityIndicator activity={staticView()} />
    );
    const animated = Array.from(container.querySelectorAll("[class]")).filter(
      (el) => /\banimate-(pulse|in)\b/.test(el.className)
    );
    expect(animated.length).toBeGreaterThan(0);
    for (const el of animated) {
      expect(el.className).toContain("motion-reduce:");
    }
  });

  it("renders nothing at all when there is no activity to report", () => {
    const { container } = render(
      <AiActivityIndicator
        activity={staticView({ state: "idle", line: null, sentence: "" })}
      />
    );
    expect(container.textContent).toBe("");
  });
});
