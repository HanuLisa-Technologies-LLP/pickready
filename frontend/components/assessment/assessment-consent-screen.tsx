"use client";

// The one consent screen before the assessment (Appendix B section 1: one
// mode, so one set of terms).
//
// ONE SCREEN, ONE MODE. There is no mode choice before it and no second
// variant of it: every candidate takes the same proctored assessment, so
// every candidate reads the same terms. What the recording is, how long it is
// kept and who may watch it, that spoken answers are transcribed and only the
// text kept, and that paste is blocked and recorded, are all in the server's
// one consent text.
//
// THE RULES ARE THE NEXT SCREEN, AND THEY ARE SHOWN ONCE. The rules the
// monitoring enforces (the timer per question, the fixed order, the camera
// and microphone pauses and their grace, speaking during a question that
// takes no spoken answer) are the proctoring layer's `candidate_rules`,
// rendered by the monitoring consent screen the shell opens immediately after
// this one and before the system check. Showing them here as well would put
// the same sentences in front of the candidate twice, one click apart, with
// two agreements to them; so this screen carries the consent and that one
// carries the rules, in the order "consent, rules, system check".
//
// THE WORDS ARE THE SERVER'S. The consent text, its versions and the per-item
// catalogue come from the consent route; this screen authors none of them.
//
// EACH ITEM IS ITS OWN TICK (vivekium feature 6: consented and timestamped
// individually). The button stays disabled until every item is ticked and the
// ticked keys are what is posted; the SERVER still refuses a short list,
// because a disabled button is a courtesy and not a guarantee. Declining
// records nothing: the candidate simply goes back to their applications.

import * as React from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AssessmentConsentTerms } from "@/lib/types";

export const CONSENT_TITLE = "Before your assessment";
export const CONSENT_ACTION = "I agree to each of these";
export const CONSENT_DECLINE = "Not now";
/** What happens after this screen, so the candidate is not surprised by a
 *  second one. */
export const CONSENT_NEXT =
  "Next you will read the rules of the assessment, then check your camera and microphone.";

export function AssessmentConsentScreen({
  terms,
  busy,
  error,
  onAccept,
}: {
  terms: AssessmentConsentTerms;
  busy: boolean;
  error: string | null;
  onAccept: (consentKeys: string[]) => void;
}) {
  const items = React.useMemo(() => terms.items ?? [], [terms.items]);
  const [ticked, setTicked] = React.useState<Record<string, boolean>>({});

  // A new item list is a different agreement, so ticks never carry across.
  React.useEffect(() => {
    setTicked({});
  }, [items]);

  const ticks = items.filter((item) => ticked[item.key]).map((item) => item.key);
  const allTicked = items.length > 0 && ticks.length === items.length;

  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>{CONSENT_TITLE}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="whitespace-pre-line text-sm leading-7">{terms.text}</p>

          {items.length > 0 ? (
            // The Stage B per-item catalogue, rendered VERBATIM from the
            // server; this screen authors none of it.
            <fieldset className="space-y-3 rounded-md border border-border bg-muted/40 p-4">
              <legend className="px-1 text-sm font-medium">Please agree to each of these</legend>
              {items.map((item) => (
                <label key={item.key} className="flex cursor-pointer gap-3 text-sm leading-6">
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
            Each item you tick is recorded separately, with the time you agreed and the wording you
            were shown. The assessment cannot begin until all of them are agreed to.
          </p>
          <p className="text-sm">{CONSENT_NEXT}</p>
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
            <Button size="lg" variant="outline" disabled={busy} asChild>
              <Link href="/portal/applications">{CONSENT_DECLINE}</Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
