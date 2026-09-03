/**
 * Behavioural capture (proctoring spec 4.5): the timings of an answer, never
 * its characters.
 *
 * WHAT IS KEPT. For the field answering one question: the millisecond offset
 * of every keydown from the moment its capture started, which of those were
 * deletions, how long the field held focus, how many blocked actions were
 * aimed at it, when each option was clicked, how many times the page scrolled,
 * and the pointer reduced to four aggregates: samples taken, distance moved in
 * pixels, time idle, clicks. That is the whole of `AnswerBehaviour`.
 *
 * WHAT IS NOT KEPT. No key name, no character, no coordinate list. The pointer
 * is sampled at `mouse_sample_hz` and each sample is folded into the running
 * distance the instant it arrives; the only point retained is the previous
 * one, so the path can never be replayed. What was typed is the answer, and
 * the answer is stored by the assessment, separately.
 *
 * ONE ACTIVE CAPTURE. `hooksFor(key)` starts the capture for that question
 * and makes it the one the document-level pointer listeners feed. The
 * previous key's capture is kept until `collect` reads it, because the player
 * asks for the behaviour AFTER it has already moved to the next question.
 * Offsets are on the `performance.now()` clock, which is the clock a DOM
 * event's `timeStamp` is on, so the offsets the field reports and the ones
 * this module measures agree.
 */
import type { AnswerBehaviour, ProctoringFieldHooks } from "@/lib/assessment/contracts";

interface Capture {
  startedAt: number;
  keydown: number[];
  backspace: number[];
  droppedKeystrokes: number;
  blockedActions: number;
  focusMs: number;
  focusedAt: number | null;
  optionClicks: number[];
  scrollEvents: number;
  mouseSamples: number;
  mousePathPx: number;
  mouseIdleMs: number;
  mouseClicks: number;
  lastSample: { x: number; y: number; at: number } | null;
}

export interface CaptureOptions {
  maxKeystrokeSamples: number;
  mouseSampleHz: number;
  document?: Document;
  now?: () => number;
}

function emptyCapture(startedAt: number): Capture {
  return {
    startedAt,
    keydown: [],
    backspace: [],
    droppedKeystrokes: 0,
    blockedActions: 0,
    focusMs: 0,
    focusedAt: null,
    optionClicks: [],
    scrollEvents: 0,
    mouseSamples: 0,
    mousePathPx: 0,
    mouseIdleMs: 0,
    mouseClicks: 0,
    lastSample: null,
  };
}

export class BehaviourCapture {
  private readonly captures = new Map<string, Capture>();
  private activeKey: string | null = null;
  private readonly now: () => number;
  private readonly doc: Document;
  private readonly sampleIntervalMs: number;

  constructor(private readonly options: CaptureOptions) {
    this.now = options.now ?? (() => performance.now());
    this.doc = options.document ?? document;
    this.sampleIntervalMs = 1000 / options.mouseSampleHz;
    this.doc.addEventListener("pointermove", this.onPointerMove);
    this.doc.addEventListener("pointerdown", this.onPointerDown);
  }

  hooksFor(questionKey: string): ProctoringFieldHooks {
    const capture = emptyCapture(this.now());
    this.captures.set(questionKey, capture);
    this.activeKey = questionKey;
    const offset = (timeStampMs: number) => Math.max(0, Math.round(timeStampMs - capture.startedAt));
    return {
      onFieldFocus: () => {
        if (capture.focusedAt === null) capture.focusedAt = this.now();
      },
      onFieldBlur: () => {
        if (capture.focusedAt !== null) {
          capture.focusMs += this.now() - capture.focusedAt;
          capture.focusedAt = null;
        }
      },
      onKeyDown: (timeStampMs, isDeletion) => {
        if (capture.keydown.length >= this.options.maxKeystrokeSamples) {
          // The ceiling bounds the request body, not the answer. Keystrokes
          // beyond it are counted so the typed-to-length ratio stays honest.
          capture.droppedKeystrokes += 1;
          return;
        }
        const at = offset(timeStampMs);
        capture.keydown.push(at);
        if (isDeletion) capture.backspace.push(at);
      },
      onBlockedAction: () => {
        capture.blockedActions += 1;
      },
      onOptionClick: (timeStampMs) => {
        capture.optionClicks.push(offset(timeStampMs));
      },
      onScroll: () => {
        capture.scrollEvents += 1;
      },
    };
  }

  collect(questionKey: string): AnswerBehaviour | null {
    const capture = this.captures.get(questionKey);
    if (!capture) return null;
    this.captures.delete(questionKey);
    if (this.activeKey === questionKey) this.activeKey = null;
    const at = this.now();
    if (capture.focusedAt !== null) capture.focusMs += at - capture.focusedAt;
    if (capture.lastSample && at - capture.lastSample.at > this.sampleIntervalMs) {
      capture.mouseIdleMs += at - capture.lastSample.at;
    }
    return {
      keydown_offsets_ms: capture.keydown,
      backspace_offsets_ms: capture.backspace,
      blocked_action_count: capture.blockedActions,
      focus_ms: Math.round(capture.focusMs),
      mouse_samples: capture.mouseSamples,
      mouse_path_px: Math.round(capture.mousePathPx),
      mouse_idle_ms: Math.round(capture.mouseIdleMs),
      mouse_clicks: capture.mouseClicks,
      option_click_offsets_ms: capture.optionClicks,
      scroll_events: capture.scrollEvents,
    };
  }

  /** Keystrokes beyond the sample ceiling for a capture still open. */
  droppedKeystrokes(questionKey: string): number {
    return this.captures.get(questionKey)?.droppedKeystrokes ?? 0;
  }

  release(): void {
    this.doc.removeEventListener("pointermove", this.onPointerMove);
    this.doc.removeEventListener("pointerdown", this.onPointerDown);
    this.captures.clear();
    this.activeKey = null;
  }

  private active(): Capture | null {
    return this.activeKey === null ? null : (this.captures.get(this.activeKey) ?? null);
  }

  private readonly onPointerMove = (event: PointerEvent) => {
    const capture = this.active();
    if (!capture) return;
    const at = this.now();
    const previous = capture.lastSample;
    if (previous !== null && at - previous.at < this.sampleIntervalMs) return;
    if (previous !== null) {
      const gap = at - previous.at;
      // Pointer events arrive many times a second while the pointer moves,
      // so a gap longer than one sampling interval is a pointer that stopped.
      if (gap > this.sampleIntervalMs) capture.mouseIdleMs += gap;
      capture.mousePathPx += Math.hypot(event.clientX - previous.x, event.clientY - previous.y);
    }
    capture.lastSample = { x: event.clientX, y: event.clientY, at };
    capture.mouseSamples += 1;
  };

  private readonly onPointerDown = () => {
    const capture = this.active();
    if (capture) capture.mouseClicks += 1;
  };
}
