"use client";

// The assessment consent screen (dual-mode spec sections 3.2 and 3.3).
//
// THIS IS NOT THE PROCTORING CONSENT. Proctoring's screen covers monitoring,
// which stores no media; this one covers what the assessment itself collects
// and stores, and it differs per mode: a recorded, transcribed, AI-analyzed
// video in one, the written answers and session data in the other. The two
// are shown as two steps because they are two agreements about two data sets.
//
// THE TEXT IS THE SERVER'S. The wording is configuration (spec 3.2: "the
// exact legal wording should be configurable"), so this screen renders what
// it is given and holds no copy of its own; the versions stamped on the
// consent row are the ones in force server-side at acceptance. Declining
// records nothing and simply goes back.

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AssessmentConsentTerms } from "@/lib/types";

export const CONSENT_ACTION = "I have read this and I agree";
export const CONSENT_DECLINE = "Go back";

const TITLES: Record<string, string> = {
  video_interview: "Before your video interview",
  conversational: "Before your assessment",
};

export function AssessmentConsentScreen({
  terms,
  busy,
  error,
  onAccept,
  onDecline,
}: {
  terms: AssessmentConsentTerms;
  busy: boolean;
  error: string | null;
  onAccept: () => void;
  onDecline: () => void;
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>
            {TITLES[terms.assessment_mode] ?? "Before your assessment"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="whitespace-pre-line text-sm leading-7">{terms.text}</p>
          {terms.items && terms.items.length > 0 ? (
            // The Stage B per-item catalogue (vivekium feature 6). Rendered
            // VERBATIM from the server, which stamps each item individually
            // when the candidate agrees; this screen authors none of it.
            <ul className="space-y-2 rounded-md border border-border bg-muted/40 p-4">
              {terms.items.map((item) => (
                <li key={item.key} className="flex gap-2 text-sm">
                  <span aria-hidden="true" className="select-none">
                    {"•"}
                  </span>
                  <span>{item.text}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="text-sm">
            Agreeing records the time you agreed to each item above and the
            version of these terms. The assessment cannot begin without it.
          </p>
          {error ? (
            <p role="alert" className="text-sm font-medium">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-3">
            <Button size="lg" disabled={busy} onClick={onAccept}>
              {CONSENT_ACTION}
            </Button>
            <Button size="lg" variant="outline" disabled={busy} onClick={onDecline}>
              {CONSENT_DECLINE}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
