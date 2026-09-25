// @vitest-environment jsdom
//
// The pause screen (Phase 3, 2026-09-24).
//
// Pinned: the countdown is the SERVER's deadline and nothing else; once the
// server has confirmed the pause its own sentence is the explanation, verbatim,
// and the screen words no allowance of its own; before that the browser names
// the device it saw go and guesses nothing it did not see; the expiry asks the
// server once rather than deciding; and nothing on the screen carries a digit
// except the clock.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PauseView } from "@/lib/proctoring/session";

import {
  formatCountdown,
  lostSentence,
  PAUSE_CONFIRMING,
  PAUSE_EXPIRED,
  PAUSE_RESUMING,
  PAUSE_RETRY,
  PAUSE_STAY,
  PauseOverlay,
} from "./pause-overlay";

const NOW = Date.UTC(2026, 8, 24, 10, 0, 0);

/** The shape of `phrasing.pause_message` on the server. */
const SERVER_MESSAGE =
  "Your camera has stopped, so the assessment is paused and its clock has stopped. You have " +
  "two minutes to fix it: check that it is connected and allowed. One more pause is allowed " +
  "after this one. Your answers so far are saved.";

function view(overrides: Partial<PauseView> = {}): PauseView {
  return {
    serverPaused: true,
    message: SERVER_MESSAGE,
    graceDeadlineMs: NOW + 120_000,
    pausesUsed: 1,
    maxPauses: 2,
    lostDevices: ["camera"],
    lossReported: true,
    ...overrides,
  };
}

function renderOverlay(overrides: Partial<PauseView> = {}, props: { retrying?: boolean } = {}) {
  const onRetry = vi.fn();
  const onExpired = vi.fn();
  const utils = render(
    <PauseOverlay
      view={view(overrides)}
      open
      retrying={props.retrying ?? false}
      onRetry={onRetry}
      onExpired={onExpired}
    />
  );
  return { ...utils, onRetry, onExpired };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("formatCountdown", () => {
  it("reads minutes and seconds, rounding up so it never shows zero with time left", () => {
    expect(formatCountdown(120_000)).toBe("2:00");
    expect(formatCountdown(83_000)).toBe("1:23");
    expect(formatCountdown(500)).toBe("0:01");
    expect(formatCountdown(0)).toBe("0:00");
    expect(formatCountdown(-4000)).toBe("0:00");
  });
});

describe("the words", () => {
  it("name the device the browser saw go, and guess nothing after a reload", () => {
    expect(lostSentence(["camera"])).toBe("Your camera stopped working.");
    expect(lostSentence(["microphone"])).toBe("Your microphone stopped working.");
    expect(lostSentence(["camera", "microphone"])).toBe("Your camera and microphone stopped working.");
    expect(lostSentence([])).toBe("Your camera or microphone stopped working.");
  });

});

describe("PauseOverlay", () => {
  it("counts down the server's grace under the server's own sentence", () => {
    renderOverlay();
    expect(screen.getByText(SERVER_MESSAGE)).toBeTruthy();
    // The browser's own sentence gives way to the server's once it arrives.
    expect(screen.queryByText("Your camera stopped working.")).toBeNull();
    expect(screen.getByRole("timer").textContent).toBe("2:00");
    expect(screen.getByText(PAUSE_STAY)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(37_000);
    });
    expect(screen.getByRole("timer").textContent).toBe("1:23");
  });

  it("carries no digit anywhere but the clock", () => {
    const { container } = renderOverlay({ lostDevices: ["camera", "microphone"] });
    const timer = screen.getByRole("timer");
    const text = (container.ownerDocument.body.textContent ?? "").replace(timer.textContent ?? "", "");
    expect(text).not.toMatch(/\d/);
  });

  it("asks the server once when the time runs out, and offers no retry after", () => {
    const { onExpired } = renderOverlay();
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(screen.getByText(PAUSE_EXPIRED)).toBeTruthy();
    expect(onExpired).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(onExpired).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: PAUSE_RETRY }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("hands Try again to the shell, and holds it while an attempt runs", () => {
    const { onRetry } = renderOverlay();
    fireEvent.click(screen.getByRole("button", { name: PAUSE_RETRY }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    cleanup();
    renderOverlay({}, { retrying: true });
    expect((screen.getByRole("button", { name: PAUSE_RETRY }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("words no pause allowance of its own", () => {
    const { container } = renderOverlay({ message: "The server's sentence." });
    const text = container.ownerDocument.body.textContent ?? "";
    expect(text).toContain("The server's sentence.");
    expect(text).not.toMatch(/pauses? (are |is )?allowed/i);
  });

  it("names the device it saw go when the server sent no sentence", () => {
    renderOverlay({ message: null, lostDevices: ["microphone"] });
    expect(screen.getByText("Your microphone stopped working.")).toBeTruthy();
  });

  it("says it is waiting for the server before the pause is confirmed, with no clock", () => {
    renderOverlay({ serverPaused: false, message: null, graceDeadlineMs: null });
    expect(screen.getByText("Your camera stopped working.")).toBeTruthy();
    expect(screen.getByText(PAUSE_CONFIRMING)).toBeTruthy();
    expect(screen.queryByRole("timer")).toBeNull();
    expect(screen.getByRole("button", { name: PAUSE_RETRY })).toBeTruthy();
  });

  it("says the devices are back while the server lifts the pause, and offers nothing to press", () => {
    renderOverlay({ lostDevices: [], lossReported: false });
    expect(screen.getByText(PAUSE_RESUMING)).toBeTruthy();
    expect(screen.queryByRole("button", { name: PAUSE_RETRY })).toBeNull();
  });

  it("renders nothing while closed", () => {
    render(
      <PauseOverlay view={view()} open={false} retrying={false} onRetry={vi.fn()} onExpired={vi.fn()} />
    );
    expect(screen.queryByRole("timer")).toBeNull();
    expect(screen.queryByText(SERVER_MESSAGE)).toBeNull();
  });
});
