"use client";

// The React boundary between the proctoring client and the assessment player.
//
// `ProctoringShell` (proctoring-shell.tsx) owns the session: consent, the
// system check, the detectors, the event queue, warnings and termination. It
// provides a `ProctoringBridge` through this context, and the assessment
// conversation reads it with `useProctoring()`. The player never imports a
// detector and the detectors never import the player; the bridge in
// lib/assessment/contracts.ts is the whole of what they share.

import * as React from "react";

import type { ProctoringBridge } from "@/lib/assessment/contracts";

const ProctoringContext = React.createContext<ProctoringBridge | null>(null);

export const ProctoringProvider = ProctoringContext.Provider;

/**
 * The bridge, or a thrown error. Rendering an answer field outside the shell
 * would mean an unmonitored assessment, and proctoring is mandatory, so that
 * is a programming error rather than a state to degrade into.
 */
export function useProctoring(): ProctoringBridge {
  const bridge = React.useContext(ProctoringContext);
  if (bridge === null) {
    throw new Error("useProctoring must be used inside a ProctoringShell");
  }
  return bridge;
}
