// @vitest-environment jsdom
//
// Behavioural capture keeps TIMINGS and AGGREGATES, and nothing else.
//
// The first test is the one that matters: the collected record is compared
// against the exact field set of `AnswerBehaviour`, so a field carrying a key
// name, a character or a coordinate list would fail here rather than reach a
// request body. The rest check that the timings it does keep are right.

import { afterEach, describe, expect, it } from "vitest";

import { BehaviourCapture } from "./capture";

let capture: BehaviourCapture | null = null;
let clock = 0;

afterEach(() => {
  capture?.release();
  capture = null;
});

function build(maxKeystrokeSamples = 100): BehaviourCapture {
  clock = 1000;
  capture = new BehaviourCapture({
    maxKeystrokeSamples,
    mouseSampleHz: 10,
    now: () => clock,
  });
  return capture;
}

/** jsdom implements MouseEvent and not PointerEvent, and the capture reads
 *  only `clientX`/`clientY`, which both carry. */
function move(x: number, y: number): void {
  document.dispatchEvent(new MouseEvent("pointermove", { clientX: x, clientY: y, bubbles: true }));
}

describe("behaviour capture", () => {
  it("collects exactly the fields of AnswerBehaviour, and no others", () => {
    const hooks = build().hooksFor("q1");
    hooks.onKeyDown(1100, false);
    const behaviour = capture!.collect("q1");
    expect(behaviour).not.toBeNull();
    expect(Object.keys(behaviour as object).sort()).toEqual(
      [
        "backspace_offsets_ms",
        "blocked_action_count",
        "focus_ms",
        "keydown_offsets_ms",
        "mouse_clicks",
        "mouse_idle_ms",
        "mouse_path_px",
        "mouse_samples",
        "option_click_offsets_ms",
        "scroll_events",
      ].sort()
    );
  });

  it("records a keystroke as an offset from the capture's start, never a key", () => {
    const hooks = build().hooksFor("q1");
    hooks.onKeyDown(1250, false);
    hooks.onKeyDown(1600, true);
    hooks.onKeyDown(1800, false);
    const behaviour = capture!.collect("q1");
    expect(behaviour?.keydown_offsets_ms).toEqual([250, 600, 800]);
    expect(behaviour?.backspace_offsets_ms).toEqual([600]);
  });

  it("counts focused time across several visits to the field", () => {
    const hooks = build().hooksFor("q1");
    hooks.onFieldFocus();
    clock = 5000;
    hooks.onFieldBlur();
    clock = 6000;
    hooks.onFieldFocus();
    clock = 6500;
    const behaviour = capture!.collect("q1");
    // 4000 from the first visit plus 500 still open when it was collected.
    expect(behaviour?.focus_ms).toBe(4500);
  });

  it("reduces the pointer to distance and counts, keeping no coordinate", () => {
    const hooks = build().hooksFor("q1");
    hooks.onFieldFocus();
    move(0, 0);
    clock += 200;
    move(30, 40);
    clock += 200;
    move(30, 140);
    document.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    const behaviour = capture!.collect("q1");
    expect(behaviour?.mouse_samples).toBe(3);
    expect(behaviour?.mouse_path_px).toBe(150);
    expect(behaviour?.mouse_clicks).toBe(1);
    expect(JSON.stringify(behaviour)).not.toContain("140");
  });

  it("counts a blocked action, an option click and a scroll on the answer they were aimed at", () => {
    const hooks = build().hooksFor("q1");
    hooks.onBlockedAction();
    hooks.onBlockedAction();
    hooks.onOptionClick(1400);
    hooks.onScroll();
    const behaviour = capture!.collect("q1");
    expect(behaviour?.blocked_action_count).toBe(2);
    expect(behaviour?.option_click_offsets_ms).toEqual([400]);
    expect(behaviour?.scroll_events).toBe(1);
  });

  it("bounds the keystroke samples and counts what it dropped", () => {
    const hooks = build(3).hooksFor("q1");
    for (let index = 0; index < 10; index += 1) hooks.onKeyDown(1000 + index * 10, false);
    expect(capture!.droppedKeystrokes("q1")).toBe(7);
    expect(capture!.collect("q1")?.keydown_offsets_ms).toHaveLength(3);
  });

  it("keeps the previous question's capture until it is collected", () => {
    // The player asks for the behaviour after it has already moved on, so a
    // new capture must not discard the one being submitted.
    const instance = build();
    const first = instance.hooksFor("q1");
    first.onKeyDown(1100, false);
    const second = instance.hooksFor("q2");
    second.onKeyDown(1200, false);
    expect(instance.collect("q1")?.keydown_offsets_ms).toEqual([100]);
    expect(instance.collect("q2")?.keydown_offsets_ms).toEqual([200]);
  });

  it("answers null for a question nothing was captured for", () => {
    expect(build().collect("never-shown")).toBeNull();
  });

  it("clears a capture once collected, so nothing is submitted twice", () => {
    const instance = build();
    instance.hooksFor("q1").onKeyDown(1100, false);
    expect(instance.collect("q1")).not.toBeNull();
    expect(instance.collect("q1")).toBeNull();
  });
});
