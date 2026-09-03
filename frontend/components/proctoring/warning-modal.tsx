"use client";

// The live warning (proctoring spec 8.3).
//
// BLOCKING, AND THE TIME IT BLOCKS IS PAID BACK. The candidate cannot answer
// while it is open, so the milliseconds it holds the screen are accumulated
// and sent as `paused_ms` on the next answer, and the server subtracts them
// from that answer's time. Without that a warning would cost the candidate
// thinking time on the question they were in the middle of.
//
// THE MESSAGE COMES FROM THE SERVER. It is composed there, specific and
// actionable ("A phone was detected on camera. Please move it out of view.")
// and it says which warning this is. The client never writes warning text and
// never counts warnings: it renders what it was handed.
//
// Acknowledging is also the user gesture that lets the page return to
// fullscreen, which is why the shell asks for fullscreen from this button.

import * as React from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export const WARNING_TITLE = "Please read this before you carry on";
export const WARNING_ACKNOWLEDGE = "I understand";

export function WarningModal({
  message,
  onAcknowledge,
}: {
  /** Null when no warning is open. */
  message: string | null;
  onAcknowledge: () => void;
}) {
  const open = message !== null;
  return (
    <AlertDialog open={open}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{WARNING_TITLE}</AlertDialogTitle>
          <AlertDialogDescription className="text-ink">{message}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogAction onClick={onAcknowledge}>{WARNING_ACKNOWLEDGE}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/**
 * A stopwatch over the time warnings held the screen.
 *
 * `consumePausedMs` on the bridge reads and clears it, so each answer carries
 * only the pause that happened while it was being written.
 */
export function usePausedTime(): {
  start: () => void;
  stop: () => void;
  consume: () => number;
} {
  const openedAt = React.useRef<number | null>(null);
  const accumulated = React.useRef(0);
  return React.useMemo(
    () => ({
      start: () => {
        if (openedAt.current === null) openedAt.current = Date.now();
      },
      stop: () => {
        if (openedAt.current !== null) {
          accumulated.current += Date.now() - openedAt.current;
          openedAt.current = null;
        }
      },
      consume: () => {
        // A warning still open when the answer is sent cannot happen (the
        // modal blocks the form), but counting it here rather than assuming
        // it keeps the number honest if that ever changes.
        if (openedAt.current !== null) {
          accumulated.current += Date.now() - openedAt.current;
          openedAt.current = Date.now();
        }
        const total = accumulated.current;
        accumulated.current = 0;
        return Math.round(total);
      },
    }),
    []
  );
}
