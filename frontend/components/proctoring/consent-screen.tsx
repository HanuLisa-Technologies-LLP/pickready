"use client";

// The consent screen (proctoring spec 8.1; Phase 3, 2026-09-24).
//
// A LEGAL REQUIREMENT, NOT A FORMALITY. What a candidate reads here is the
// whole of what they are told about the monitoring before the camera and
// microphone are opened, and the button is the explicit action. Nothing on
// this screen opens a device, starts a check or creates a session: the
// candidate reads first, agrees second, and the browser asks for the camera
// only after that.
//
// THE RULES ARE THE SERVER'S SENTENCES. They used to be seven statements
// hard-coded in this file (`CONSENT_POINTS`). Phase 3 added rules with numbers
// in them, the timers per question, the two-minute grace for a lost camera or
// microphone, the two pauses allowed, speaking during a question that takes
// no spoken answer, and a hard-coded copy of those would be a second author of
// numbers the server enforces from its own settings. So `GET
// /proctoring/config` serves `candidate_rules`, composed from the same
// configuration that applies them, and this screen renders them verbatim, in
// order. The recording itself is described by the ASSESSMENT consent the
// candidate accepted one screen earlier, which is the one that carries its
// retention and who may view it.
//
// NO RULES, NO AGREEMENT. While the rules load the button is not offered, and
// when they cannot be loaded the screen says so and offers a retry. Agreeing
// to rules nobody was shown is not agreement, and there is no fallback text.

import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const CONSENT_TITLE = "This assessment is monitored";
export const CONSENT_ACTION = "I understand and agree";
export const CONSENT_LOADING = "Loading the assessment rules";
export const CONSENT_RETRY = "Try again";

export function ConsentScreen({
  rules,
  error,
  onAgree,
  onRetry,
}: {
  /** The server's rules, or null while they load. */
  rules: readonly string[] | null;
  /** Why the rules could not be loaded, when they could not. */
  error: string | null;
  onAgree: () => void;
  onRetry: () => void;
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>{CONSENT_TITLE}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {error !== null ? (
            <div className="space-y-3">
              <p role="alert" className="text-sm font-medium">
                {error}
              </p>
              <Button variant="outline" onClick={onRetry}>
                {CONSENT_RETRY}
              </Button>
            </div>
          ) : rules === null ? (
            <p className="flex items-center gap-2 text-sm" role="status">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {CONSENT_LOADING}
            </p>
          ) : (
            <>
              <p className="text-sm">Please read these rules before you begin.</p>
              <ul className="space-y-3">
                {rules.map((rule) => (
                  <li key={rule} className="flex gap-3 text-sm">
                    {/* Teal, because this is the evidence of what was disclosed.
                        A rule and not a word, so nothing here reads as a grade. */}
                    <span aria-hidden className="mt-2 h-1 w-4 shrink-0 bg-teal-600" />
                    <span>{rule}</span>
                  </li>
                ))}
              </ul>
              <p className="text-sm">
                Agreeing records the time you agreed. The assessment cannot begin without it.
              </p>
              <Button size="lg" onClick={onAgree}>
                {CONSENT_ACTION}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
