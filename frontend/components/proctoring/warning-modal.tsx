"use client";

// The live warning (proctoring spec 8.3).
//
// BLOCKING, AND THE TIME IT BLOCKS IS THE SERVER'S TO PAY BACK. The candidate
// cannot answer while it is open. The server holds the question timer from
// the moment it issued the warning until the candidate acknowledges it
// (`POST /proctoring/sessions/{id}/warnings/ack`, capped on the server), so a
// warning does not cost them thinking time on the question they were in the
// middle of. SUPERSEDES the client stopwatch (`usePausedTime`) that measured
// the hold here and sent it as `paused_ms` on the next answer (Phase 3,
// 2026-09-24): a pause length the client reports is a number the client
// chose, and it lived in a React ref, so a reload between the warning and the
// answer lost it.
//
// THE MESSAGE COMES FROM THE SERVER. It is composed there, specific and
// actionable ("A phone was detected on camera. Please move it out of view.")
// and it says which warning this is. The client never writes warning text and
// never counts warnings: it renders what it was handed.
//
// Acknowledging is also the user gesture that lets the page return to
// fullscreen, which is why the shell asks for fullscreen from this button.

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
