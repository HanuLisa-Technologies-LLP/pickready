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
//
// EACH ITEM IS ITS OWN TICK (vivekium feature 6: consented and timestamped
// individually). A read-only list under one Accept button cannot support that
// claim, whatever the rows behind it look like. The button is disabled until
// every item is ticked, and the ticked keys are what is posted; the SERVER
// still refuses a short list, because a disabled button is a courtesy and not
// a guarantee.

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AssessmentConsentTerms } from "@/lib/types";

export const CONSENT_ACTION = "I agree to each of these";
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
  onAccept: (consentKeys: string[]) => void;
  onDecline: () => void;
}) {
  const items = React.useMemo(() => terms.items ?? [], [terms.items]);
  const [ticked, setTicked] = React.useState<Record<string, boolean>>({});

  // A mode switch replaces the item list, so a tick from the previous list
  // must not carry over into an agreement to different words.
  React.useEffect(() => {
    setTicked({});
  }, [terms.assessment_mode, items]);

  const ticks = items.filter((item) => ticked[item.key]).map((item) => item.key);
  const allTicked = items.length > 0 && ticks.length === items.length;

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
          {items.length > 0 ? (
            // The Stage B per-item catalogue, rendered VERBATIM from the
            // server; this screen authors none of it.
            <fieldset className="space-y-3 rounded-md border border-border bg-muted/40 p-4">
              <legend className="px-1 text-sm font-medium">
                Please agree to each of these
              </legend>
              {items.map((item) => (
                <label
                  key={item.key}
                  className="flex cursor-pointer gap-3 text-sm leading-6"
                >
                  <input
                    type="checkbox"
                    className="mt-1 h-4 w-4 shrink-0"
                    checked={Boolean(ticked[item.key])}
                    disabled={busy}
                    onChange={(event) =>
                      setTicked((current) => ({
                        ...current,
                        [item.key]: event.target.checked,
                      }))
                    }
                  />
                  <span>{item.text}</span>
                </label>
              ))}
            </fieldset>
          ) : null}
          <p className="text-sm">
            Each item you tick is recorded separately, with the time you
            agreed and the wording you were shown. The assessment cannot begin
            until all of them are agreed to.
          </p>
          {error ? (
            <p role="alert" className="text-sm font-medium">
              {error}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-3">
            <Button
              size="lg"
              disabled={busy || !allTicked}
              onClick={() => onAccept(ticks)}
            >
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
