"use client";

// The device pause (Phase 3, 2026-09-24; master prompt Appendix B section 4).
//
// A camera or microphone that stops no longer ends the assessment. It pauses
// it, says so plainly, and gives the candidate the server's grace to fix it,
// with a countdown they can watch. At most two pauses per session; a third
// loss, or a grace that runs out, ends it. The candidate read those rules
// before they agreed to start, and this screen repeats the part that applies
// right now.
//
// EVERY RULE ON THIS SCREEN IS THE SERVER'S. The explanation is the server's
// own sentence (`pause.message`, from `phrasing.pause_message`): which device
// stopped, what to do, the time allowed and what the next stop will mean. The
// deadline is the server's `grace_deadline_at`, already placed on this
// device's clock by the session (see `PauseView`). This screen words no
// allowance of its own, because a count of pauses it composed would be a
// second author of the number that ends the session. What the browser adds is
// the one thing only it can see, whether a track is live: it names the lost
// device in the moment before the server has answered, and it knows when the
// devices are back before the server has lifted the pause.
//
// THE COUNTDOWN IS A CLOCK, NOT A SCORE. Rule 1 bans numbers ABOUT the
// candidate; a clock telling them how long they have is the one place a digit
// is the kind thing to show (Phase 3 decision 9, which retires the
// `time-guidance` "no digit reads as a clock" rationale). The pause counts are
// spelled out, as every count on the monitoring screens is.
//
// BLOCKING, AND IT SAYS WHAT IS SAFE. It is an alert dialog with nothing
// behind it reachable, because an answer typed while the camera is off is an
// answer nobody watched. It tells the candidate that the question timer is
// held and nothing they answered is lost, because that is the first thing a
// person whose camera just died wants to know (the server's sentence says it
// once the pause is confirmed).

import * as React from "react";
import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import type { Device } from "@/lib/proctoring/device-watch";
import type { PauseView } from "@/lib/proctoring/session";

export const PAUSE_TITLE = "Your assessment is paused";
export const PAUSE_RETRY = "Try again";
export const PAUSE_RESUMING = "Your camera and microphone are working again. Resuming your assessment.";
export const PAUSE_CONFIRMING = "Pausing your assessment while we let the server know.";
export const PAUSE_EXPIRED =
  "The time to reconnect has run out. Checking with the server whether the assessment can continue.";
export const PAUSE_STAY =
  "Stay on this page and in fullscreen while you fix it, then press Try again. Leaving the page still counts as a warning.";

/** "Your camera stopped working." for the devices this browser saw go: what
 *  the screen says before the server's own sentence has arrived. */
export function lostSentence(lost: readonly Device[]): string {
  const camera = lost.includes("camera");
  const microphone = lost.includes("microphone");
  if (camera && microphone) return "Your camera and microphone stopped working.";
  if (camera) return "Your camera stopped working.";
  if (microphone) return "Your microphone stopped working.";
  return "Your camera or microphone stopped working.";
}

/** 83 000 ms reads "1:23"; the remainder is rounded UP so the screen never
 *  shows 0:00 while there is still time. */
export function formatCountdown(remainingMs: number): string {
  const total = Math.max(0, Math.ceil(remainingMs / 1000));
  const minutes = Math.floor(total / 60);
  const secondsPart = String(total % 60).padStart(2, "0");
  return `${minutes}:${secondsPart}`;
}

/** Redraw cadence for the countdown. A rendering resolution, not a rule. */
const TICK_MS = 250;

export function PauseOverlay({
  view,
  open,
  retrying,
  onRetry,
  onExpired,
}: {
  view: PauseView;
  open: boolean;
  retrying: boolean;
  onRetry: () => void;
  /** Called once when the countdown reaches zero, so the shell can ask the
   *  server for its decision at once rather than at the next heartbeat. */
  onExpired: () => void;
}) {
  const deadline = view.serverPaused ? view.graceDeadlineMs : null;
  const [now, setNow] = React.useState(() => Date.now());
  const expiredFor = React.useRef<number | null>(null);

  React.useEffect(() => {
    if (!open || deadline === null) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [deadline, open]);

  const remainingMs = deadline === null ? null : deadline - now;
  const expired = remainingMs !== null && remainingMs <= 0;

  React.useEffect(() => {
    if (!open || !expired || deadline === null || expiredFor.current === deadline) return;
    expiredFor.current = deadline;
    onExpired();
  }, [deadline, expired, onExpired, open]);

  const devicesBack = view.serverPaused && view.lostDevices.length === 0;
  const description = devicesBack
    ? PAUSE_RESUMING
    : view.serverPaused && view.message
      ? view.message
      : lostSentence(view.lostDevices);

  return (
    <AlertDialog open={open}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{PAUSE_TITLE}</AlertDialogTitle>
          <AlertDialogDescription className="text-ink">{description}</AlertDialogDescription>
        </AlertDialogHeader>

        {remainingMs !== null ? (
          expired ? (
            <p className="text-sm" role="status">
              {PAUSE_EXPIRED}
            </p>
          ) : (
            <div className="flex items-baseline gap-3">
              <span className="text-sm font-medium" id="pause-countdown-label">
                Time left to reconnect
              </span>
              <span
                role="timer"
                aria-labelledby="pause-countdown-label"
                className="text-2xl font-semibold tabular-nums"
              >
                {formatCountdown(remainingMs)}
              </span>
            </div>
          )
        ) : !view.serverPaused ? (
          <p className="flex items-center gap-2 text-sm" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {PAUSE_CONFIRMING}
          </p>
        ) : null}

        {devicesBack ? null : (
          <>
            <p className="text-sm">{PAUSE_STAY}</p>
            <AlertDialogFooter>
              <Button onClick={onRetry} disabled={retrying || expired} className="gap-1.5">
                {retrying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                {PAUSE_RETRY}
              </Button>
            </AlertDialogFooter>
          </>
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
