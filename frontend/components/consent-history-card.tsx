"use client";

// My consents, on My Profile (vivekium feature 6: the candidate record is one
// of the three places a consent must be visible).
//
// TWO QUESTIONS, TWO LISTS, BOTH FROM `GET /portal/me/consents`.
// * Where do I stand now? The WHOLE catalogue, given or not, so an item that
//   was never asked cannot hide in a short list (the compliance-slot rule).
// * What did I agree to, and when? The append-only history, each act carrying
//   the sentence VERBATIM as it read at the time. A re-affirmation moves the
//   standing stamp, so the current list alone cannot answer this.
//
// READ ONLY, AND WORDS ONLY. This card changes nothing, and it shows no
// version identifier and no digest: those are the record's provenance for an
// auditor, not something a person reads. What a person can use is whether the
// wording has changed since they agreed, and the server computes that.

import * as React from "react";
import { History } from "lucide-react";

import { apiGet } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { InlineError } from "@/components/page-primitives";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** One catalogue item with this candidate's standing stamp
 *  (`consent_catalog.items_for`). */
export interface ConsentItem {
  key: string;
  stage: string;
  text: string;
  required: boolean;
  consented_at: string | null;
  wording_current: boolean;
}

/** One consent act (`consent_catalog.history_for`), newest first. */
export interface ConsentEvent {
  key: string;
  stage: string;
  source: string;
  text: string | null;
  recorded_at: string;
}

export interface MyConsents {
  items: ConsentItem[];
  history: ConsentEvent[];
}

/** Where the act happened, in words. An unknown value is shown as nothing
 *  rather than as its code. */
const SOURCE_LABEL: Record<string, string> = {
  registration: "When you registered",
  assessment: "Before an assessment",
  portal: "On My Profile",
};

const STAGE_LABEL: Record<string, string> = {
  A: "Registration",
  B: "Assessment",
};

const when = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

export function ConsentHistoryCard() {
  const [consents, setConsents] = React.useState<MyConsents | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      setConsents(await apiGet<MyConsents>("/portal/me/consents"));
      setLoadError(null);
    } catch (failure) {
      setLoadError(apiErrorMessage(failure));
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <History className="h-4 w-4" aria-hidden="true" />
          <CardTitle className="text-base">My consents</CardTitle>
        </div>
        <CardDescription>
          Everything you have been asked to agree to, where you stand on each,
          and the exact words you agreed to at the time.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {loadError && !consents ? (
          <div className="space-y-3">
            <InlineError>Your consents could not be loaded. {loadError}</InlineError>
            <Button size="sm" variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        ) : !consents ? (
          <p role="status" className="text-sm">
            Loading your consents
          </p>
        ) : (
          <>
            <section className="space-y-2" aria-labelledby="consents-current">
              <h3 id="consents-current" className="text-sm font-semibold">
                Where you stand now
              </h3>
              <ul className="space-y-2">
                {consents.items.map((item) => (
                  <li key={item.key} className="space-y-1 rounded-md border p-3">
                    <p className="text-sm">{item.text}</p>
                    <p className="text-xs font-medium">
                      {STAGE_LABEL[item.stage] ?? null}
                      {STAGE_LABEL[item.stage] ? " · " : null}
                      {item.required ? "Required" : "Optional"}
                      {" · "}
                      {item.consented_at
                        ? `Agreed on ${when(item.consented_at)}`
                        : "Not agreed"}
                    </p>
                    {item.consented_at && !item.wording_current ? (
                      <p className="text-xs">
                        This wording has changed since you agreed. Your history
                        below shows the words you agreed to.
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>

            <section className="space-y-2" aria-labelledby="consents-history">
              <h3 id="consents-history" className="text-sm font-semibold">
                Your history
              </h3>
              {consents.history.length === 0 ? (
                <p className="text-sm">No consent has been recorded yet.</p>
              ) : (
                <ol className="space-y-2">
                  {consents.history.map((event, index) => (
                    <li
                      key={`${event.key}-${event.recorded_at}-${index}`}
                      className="space-y-1 rounded-md border p-3"
                    >
                      <p className="text-xs font-medium">
                        {when(event.recorded_at)}
                        {SOURCE_LABEL[event.source]
                          ? `, ${SOURCE_LABEL[event.source].toLowerCase()}`
                          : null}
                      </p>
                      {event.text ? (
                        <blockquote className="border-l-2 border-border pl-3 text-sm">
                          {event.text}
                        </blockquote>
                      ) : (
                        // Acts recorded before the wording was kept carry no
                        // text, deliberately: stamping them with today's words
                        // would manufacture the record this list exists to be.
                        <p className="text-sm">
                          The exact wording was not recorded for this earlier
                          consent.
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </>
        )}
      </CardContent>
    </Card>
  );
}
