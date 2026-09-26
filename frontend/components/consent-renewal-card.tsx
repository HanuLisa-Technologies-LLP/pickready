"use client";

// Keep my profile, on My Profile (vivekium feature 8).
//
// The consent letter asks a candidate to confirm that their profile should
// stay; until this card, the only place to answer was the one-click link in
// that letter. Signed in, the same act is `POST /portal/me/consent/renew`.
//
// NOTHING HERE IS A RULE OF THIS PAGE'S OWN. Whether confirmation is due, when
// it next will be, and the sentence explaining what happens otherwise are all
// read from `GET /portal/me/consent/renewal`, which computes them with the
// same pure functions and the same settings the retention sweep uses. The page
// renders the two dates and the server's message; it adds no deadline, no
// countdown and no count of days.
//
// READING IS NOT RENEWING. Opening My Profile renews nothing; only the button
// does, and the server's confirmation sentence is what the candidate reads.

import * as React from "react";
import { CalendarCheck2, Loader2 } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
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

/** Mirrors `schemas.portal.ConsentRenewalOut`. */
export interface ConsentRenewal {
  consented_at: string;
  renewal_due_at: string;
  stage: string;
  renewal_needed: boolean;
  message: string;
}

/** Mirrors `schemas.portal.ConsentRenewedOut`. */
interface ConsentRenewed {
  renewed: boolean;
  renewed_at: string;
  message: string;
}

const dateOnly = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });

export function ConsentRenewalCard() {
  const [renewal, setRenewal] = React.useState<ConsentRenewal | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [confirmation, setConfirmation] = React.useState<string | null>(null);
  const [renewError, setRenewError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      setRenewal(await apiGet<ConsentRenewal>("/portal/me/consent/renewal"));
      setLoadError(null);
    } catch (failure) {
      setLoadError(apiErrorMessage(failure));
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const renew = async () => {
    setBusy(true);
    setRenewError(null);
    try {
      const result = await apiPost<ConsentRenewed>("/portal/me/consent/renew");
      setConfirmation(result.message);
      // Re-read rather than computing the next date here: the period, the
      // stage and the sentence are the server's, so the card shows what the
      // sweep will now act on.
      await load();
    } catch (failure) {
      setRenewError(apiErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CalendarCheck2 className="h-4 w-4" aria-hidden="true" />
          <CardTitle className="text-base">Keep my profile</CardTitle>
        </div>
        <CardDescription>
          We ask you from time to time whether you want us to keep your
          profile. You can confirm it here at any time.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {loadError && !renewal ? (
          <div className="space-y-3">
            <InlineError>
              Your profile renewal status could not be loaded. {loadError}
            </InlineError>
            <Button size="sm" variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        ) : !renewal ? (
          <p role="status" className="text-sm">
            Loading your renewal status
          </p>
        ) : (
          <>
            <p className="text-sm">{renewal.message}</p>
            <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              <div className="flex flex-wrap gap-x-2">
                <dt>Current period began</dt>
                <dd className="font-semibold">{dateOnly(renewal.consented_at)}</dd>
              </div>
              <div className="flex flex-wrap gap-x-2">
                <dt>{renewal.renewal_needed ? "Confirmation was due" : "Next confirmation"}</dt>
                <dd className="font-semibold">{dateOnly(renewal.renewal_due_at)}</dd>
              </div>
            </dl>
            {confirmation ? (
              <p role="status" className="text-sm font-medium">
                {confirmation}
              </p>
            ) : null}
            {renewError ? <InlineError>{renewError}</InlineError> : null}
            <Button
              size="sm"
              variant={renewal.renewal_needed ? "default" : "outline"}
              disabled={busy}
              onClick={() => void renew()}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : null}
              <span className={busy ? "ml-1" : undefined}>
                {busy ? "Confirming" : "Keep my profile"}
              </span>
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
