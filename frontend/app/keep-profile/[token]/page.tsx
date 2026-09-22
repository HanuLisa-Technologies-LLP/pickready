"use client";

// The one-click renewal link from the six-month consent letter (feature 8).
// PUBLIC BY TOKEN: the reader is somebody who has not signed in for six months
// and is holding a letter that says their profile is about to be removed.
// Asking them for a password to answer "yes, keep it" is how a retention rule
// turns into a deletion queue.
//
// ONE ACT, AND THE PAGE DOES NOT PERFORM IT ON LOAD. A link in an email is
// followed by scanners, preview fetchers and forwarding clients, so the GET
// renders a button and the POST is what the person presses. The token is
// single use, so a scanner that submitted it for them would spend it and the
// person would then be told their own link was no longer valid.
//
// Every sentence about the outcome comes from the SERVER. This page authors
// the prompt and nothing about the rule.

import * as React from "react";
import { useParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiPost, ApiError } from "@/lib/api";

interface RenewedPayload {
  renewed: boolean;
  renewed_at: string;
  message: string;
}

export default function KeepProfilePage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const confirm = React.useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await apiPost<RenewedPayload>("/portal/consent/renew", {
        token,
      });
      setDone(result.message);
    } catch (err: unknown) {
      // The server's own sentence, verbatim. A 404 here deliberately does not
      // distinguish an expired link from one that never existed, so the page
      // must not invent a distinction either.
      setError(
        err instanceof ApiError
          ? err.message
          : "This link could not be opened right now. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }, [token]);

  return (
    <main className="mx-auto flex min-h-screen max-w-xl items-center px-4">
      <Card className="w-full">
        <CardHeader>
          <CardTitle>Keep my profile on Vivekium</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {done ? (
            <p>{done}</p>
          ) : (
            <>
              <p>
                Confirm that you would like your profile kept on the platform
                and visible to employer clients registered on the platform.
              </p>
              {error ? <p role="alert">{error}</p> : null}
              <Button onClick={confirm} disabled={busy}>
                {busy ? "Confirming" : "Keep my profile"}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
