// @vitest-environment jsdom
//
// The turn's countdown renders the SERVER's clock (Appendix B section 3,
// PLAN-p3 section 3.4). What is pinned here is the one property that makes it
// safe to show: the time left comes from the server's two instants, the
// browser's clock contributes only the time since the response arrived, a
// paused turn is frozen, and zero is reported exactly once per deadline.

import * as React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TurnTimer, formatClock, readingInstant, remainingMs } from "./turn-timer";

const SERVER_NOW = "2026-09-24T10:00:00.000Z";
const DEADLINE = "2026-09-24T10:03:00.000Z"; // three minutes after server_now

afterEach(cleanup);

describe("remainingMs", () => {
  it("is the server's deadline minus the server's now, whatever the browser clock says", () => {
    // A laptop an hour fast and a laptop an hour slow read the same time left
    // at the instant of receipt: only the elapsed local time is used.
    const hourMs = 3_600_000;
    for (const skew of [-hourMs, 0, hourMs]) {
      const receivedAt = Date.parse(SERVER_NOW) + skew;
      expect(remainingMs(DEADLINE, SERVER_NOW, receivedAt, receivedAt)).toBe(180_000);
      expect(remainingMs(DEADLINE, SERVER_NOW, receivedAt, receivedAt + 30_000)).toBe(150_000);
    }
  });

  it("is read at the instant a pause began, never at the time left on receipt", () => {
    const receivedAt = 1_000;
    // Running: now.
    expect(readingInstant(95_000, receivedAt, null)).toBe(95_000);
    // Paused forty seconds after the response arrived: frozen there.
    expect(readingInstant(95_000, receivedAt, receivedAt + 40_000)).toBe(receivedAt + 40_000);
    expect(
      remainingMs(DEADLINE, SERVER_NOW, receivedAt, readingInstant(95_000, receivedAt, receivedAt + 40_000))
    ).toBe(140_000);
    // A response that arrived already paused: the server's own reading.
    expect(readingInstant(95_000, receivedAt, receivedAt - 5_000)).toBe(receivedAt);
  });

  it("never reports negative time", () => {
    expect(remainingMs(DEADLINE, SERVER_NOW, 0, 999_999_999)).toBe(0);
  });

  it("refuses an unreadable deadline rather than inventing one", () => {
    expect(() => remainingMs("not a date", SERVER_NOW, 0, 0)).toThrow();
  });
});

describe("formatClock", () => {
  it("reads minutes and seconds, rounding up so zero means gone", () => {
    expect(formatClock(180_000)).toBe("3:00");
    expect(formatClock(179_001)).toBe("3:00");
    expect(formatClock(59_000)).toBe("0:59");
    expect(formatClock(1)).toBe("0:01");
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(1_200_000)).toBe("20:00");
  });
});

describe("TurnTimer", () => {
  let nowMs = 0;
  const now = () => nowMs;

  beforeEach(() => {
    vi.useFakeTimers();
    nowMs = 5_000;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function mount(props: Partial<React.ComponentProps<typeof TurnTimer>> = {}) {
    const onExpire = vi.fn();
    const utils = render(
      <TurnTimer
        deadlineAt={DEADLINE}
        serverNow={SERVER_NOW}
        receivedAtMs={5_000}
        paused={false}
        pauseLabel={null}
        onExpire={onExpire}
        now={now}
        {...props}
      />
    );
    return { onExpire, ...utils };
  }

  const reading = () => screen.getByTestId("turn-timer-value").textContent;

  it("counts down from the server's allocation and reports zero exactly once", () => {
    const { onExpire } = mount();
    expect(reading()).toBe("3:00");
    act(() => {
      nowMs += 120_000;
      vi.advanceTimersByTime(500);
    });
    expect(reading()).toBe("1:00");
    expect(onExpire).not.toHaveBeenCalled();
    act(() => {
      nowMs += 60_000;
      vi.advanceTimersByTime(500);
    });
    expect(reading()).toBe("0:00");
    expect(onExpire).toHaveBeenCalledTimes(1);
    act(() => {
      nowMs += 10_000;
      vi.advanceTimersByTime(2_000);
    });
    expect(onExpire).toHaveBeenCalledTimes(1);
  });

  it("freezes while paused, says why, and never expires", () => {
    const { onExpire } = mount({ paused: true, pauseLabel: "Paused while you read the warning" });
    act(() => {
      nowMs += 600_000;
      vi.advanceTimersByTime(5_000);
    });
    expect(reading()).toBe("3:00");
    expect(screen.getByText("Paused while you read the warning")).toBeTruthy();
    expect(screen.getByTestId("turn-timer").getAttribute("data-paused")).toBe("true");
    expect(onExpire).not.toHaveBeenCalled();
  });

  it("stands still at the value it had when a pause begins mid-countdown", () => {
    const { onExpire, rerender } = mount();
    act(() => {
      nowMs += 60_000;
      vi.advanceTimersByTime(500);
    });
    expect(reading()).toBe("2:00");
    // A warning appears: the face stops where it is. Jumping back to the
    // time left when the response arrived would hand the candidate a minute
    // the server never gave them.
    rerender(
      <TurnTimer
        deadlineAt={DEADLINE}
        serverNow={SERVER_NOW}
        receivedAtMs={5_000}
        paused
        pauseLabel="Paused while you read the warning"
        onExpire={onExpire}
        now={now}
      />
    );
    act(() => {
      nowMs += 300_000;
      vi.advanceTimersByTime(5_000);
    });
    expect(reading()).toBe("2:00");
    expect(onExpire).not.toHaveBeenCalled();
  });

  it("re-arms for a new deadline handed back by the server", () => {
    const { onExpire, rerender } = mount();
    act(() => {
      nowMs += 180_000;
      vi.advanceTimersByTime(500);
    });
    expect(onExpire).toHaveBeenCalledTimes(1);
    // The server extended the deadline by a pause this browser did not see.
    rerender(
      <TurnTimer
        deadlineAt="2026-09-24T10:03:30.000Z"
        serverNow="2026-09-24T10:03:00.000Z"
        receivedAtMs={nowMs}
        paused={false}
        pauseLabel={null}
        onExpire={onExpire}
        now={now}
      />
    );
    expect(reading()).toBe("0:30");
    act(() => {
      nowMs += 30_000;
      vi.advanceTimersByTime(500);
    });
    expect(onExpire).toHaveBeenCalledTimes(2);
  });

  it("says time is nearly up in words, not by colour alone", () => {
    mount({ deadlineAt: "2026-09-24T10:00:20.000Z" });
    expect(screen.getByText("Time is nearly up")).toBeTruthy();
  });
});
