"use client";

// The countdown for the turn on screen (Appendix B section 3).
//
// THE SERVER OWNS THE CLOCK, AND THIS IS ONLY ITS FACE. Every turn arrives
// with `deadline_at` and the server's own `server_now`; the difference is the
// time left at the instant the server wrote the response. The browser's clock
// is used for one thing only, the distance between that response arriving and
// now, so a laptop whose clock is wrong by an hour counts down exactly as a
// correct one does. Nothing measured here is sent back as time: the server
// keeps its own pause intervals and decides what counts.
//
// A CLOCK, NOT A SCORE. The mm:ss reading is a deadline the candidate must be
// able to plan against, and it is shown in digits because a countdown written
// in words cannot be read at a glance. It says nothing about how the candidate
// is doing. This supersedes the "no digit reads as a clock" rule of the
// suggested-time phrase this replaced: that phrase described advisory time
// that nothing enforced, and the time is now enforced.
//
// PAUSED MEANS FROZEN. While a warning is on screen, a camera or microphone is
// being recovered, or a spoken answer is being transcribed, the server holds
// the deadline still, and so does this face. Reaching zero does not submit
// anything by itself: `onExpire` asks the caller to check with the server
// first, because a pause this browser did not see may have moved the deadline.

import * as React from "react";
import { PauseCircle, Timer } from "lucide-react";

/** How often the face redraws. Well under a second so the displayed second
 *  never lags the real one by a visible amount. */
export const TICK_MS = 250;

/** At or below this many seconds the face says time is short. A plain word,
 *  not a colour alone, so it reads the same to everyone. */
export const LOW_TIME_SECONDS = 30;

export interface TurnClockReading {
  /** Milliseconds left, never negative. */
  remainingMs: number;
  paused: boolean;
}

/**
 * The milliseconds left on a turn, as the server would count them at the
 * browser instant `atMs`.
 *
 * `serverNow` was the server's clock when the response was written and
 * `receivedAtMs` is this browser's clock when it arrived, so the only local
 * time that enters is the distance from `receivedAtMs` to `atMs`. A browser
 * clock that is wrong by an hour therefore reads exactly what a correct one
 * does.
 */
export function remainingMs(
  deadlineAt: string,
  serverNow: string,
  receivedAtMs: number,
  atMs: number
): number {
  const deadline = Date.parse(deadlineAt);
  const serverAtReceipt = Date.parse(serverNow);
  if (Number.isNaN(deadline) || Number.isNaN(serverAtReceipt)) {
    throw new Error("The turn clock arrived without a readable deadline.");
  }
  const serverNowEstimate = serverAtReceipt + Math.max(0, atMs - receivedAtMs);
  return Math.max(0, deadline - serverNowEstimate);
}

/**
 * The browser instant a turn's face is read at. Running, that is now. Paused,
 * it is the instant the pause began, or the instant the response arrived if
 * the server already said the turn was paused when it wrote it: the face then
 * stands still at the value it had, rather than jumping back to the time left
 * at receipt, and the server extends the deadline for exactly as long as the
 * pause lasts.
 */
export function readingInstant(
  nowMs: number,
  receivedAtMs: number,
  pausedSinceMs: number | null
): number {
  if (pausedSinceMs === null) return nowMs;
  return Math.max(pausedSinceMs, receivedAtMs);
}

/** "2:59", "0:07", "20:00". Seconds are rounded UP, so the face shows 0:00
 *  only when the time is actually gone, never a second early. */
export function formatClock(ms: number): string {
  const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/** The screen-reader line, announced at the points a person would want to be
 *  told rather than every second. */
function spokenMilestone(totalSeconds: number): string | null {
  if (totalSeconds === 60) return "One minute left for this question.";
  if (totalSeconds === LOW_TIME_SECONDS) return "Thirty seconds left for this question.";
  if (totalSeconds === 10) return "Ten seconds left for this question.";
  return null;
}

export function TurnTimer({
  deadlineAt,
  serverNow,
  receivedAtMs,
  paused,
  pauseLabel,
  onExpire,
  now = Date.now,
}: {
  deadlineAt: string;
  serverNow: string;
  /** This browser's clock when the response carrying the two instants
   *  arrived. */
  receivedAtMs: number;
  paused: boolean;
  /** Why the clock is still, in the candidate's words. */
  pauseLabel: string | null;
  /** Called once when the face reaches zero while running. */
  onExpire: () => void;
  now?: () => number;
}) {
  // When this face stopped running: set on the render the pause arrives,
  // cleared when it lifts. A ref, so the instant is captured once and not
  // re-taken by every re-render while paused.
  const pausedSince = React.useRef<number | null>(paused ? now() : null);
  if (paused && pausedSince.current === null) pausedSince.current = now();
  if (!paused) pausedSince.current = null;

  const read = React.useCallback(
    () =>
      remainingMs(
        deadlineAt,
        serverNow,
        receivedAtMs,
        readingInstant(now(), receivedAtMs, paused ? pausedSince.current : null)
      ),
    [deadlineAt, serverNow, receivedAtMs, now, paused]
  );
  const [left, setLeft] = React.useState(read);
  const expiredFor = React.useRef<string | null>(null);
  const onExpireRef = React.useRef(onExpire);
  onExpireRef.current = onExpire;

  React.useEffect(() => {
    setLeft(read());
    if (paused) return;
    const timer = window.setInterval(() => setLeft(read()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [paused, read]);

  // Fired once per deadline: a resync that hands back the same deadline must
  // not re-fire, and a new deadline (a new turn, or one a pause extended) arms
  // it again.
  React.useEffect(() => {
    if (paused || left > 0) return;
    const key = `${deadlineAt}|${serverNow}`;
    if (expiredFor.current === key) return;
    expiredFor.current = key;
    onExpireRef.current();
  }, [deadlineAt, left, paused, serverNow]);

  const totalSeconds = Math.max(0, Math.ceil(left / 1000));
  const low = !paused && totalSeconds <= LOW_TIME_SECONDS;
  const milestone = paused ? null : spokenMilestone(totalSeconds);

  return (
    <div
      className={`inline-flex items-center gap-2 border px-3 py-1.5 text-sm ${
        low ? "border-warning bg-warning/10" : "border-border bg-surface"
      }`}
      data-testid="turn-timer"
      data-paused={paused ? "true" : "false"}
    >
      {paused ? (
        <PauseCircle className="h-4 w-4" aria-hidden="true" />
      ) : (
        <Timer className="h-4 w-4" aria-hidden="true" />
      )}
      <span className="sr-only">Time left for this question:</span>
      <span
        role="timer"
        aria-live="off"
        className="font-semibold tabular-nums"
        data-testid="turn-timer-value"
      >
        {formatClock(left)}
      </span>
      {paused ? (
        <span className="font-medium">{pauseLabel ?? "Paused"}</span>
      ) : low ? (
        <span className="font-medium">Time is nearly up</span>
      ) : null}
      <span className="sr-only" aria-live="polite">
        {milestone}
      </span>
    </div>
  );
}
