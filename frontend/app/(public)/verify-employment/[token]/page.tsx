"use client";

// The employer HR checkbox form (vivekium feature 4). PUBLIC BY TOKEN: the
// link in the HR team's mailbox is the whole credential, single use, three
// day expiry. Under two minutes, no typing: the seven items come from the
// server (`bgv_form.FORM_ITEMS`) and this page authors none of them.
//
// EVERY ITEM MUST BE ANSWERED, yes or no, because an unticked box is
// indistinguishable from an unseen one. The page therefore renders explicit
// Confirm / Cannot confirm choices rather than bare checkboxes, and submits
// only when every row has one.

import * as React from "react";
import { useParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, apiPost, ApiError } from "@/lib/api";

interface FormItem {
  key: string;
  label: string;
  required: boolean;
}

interface FormPayload {
  candidate_name: string | null;
  employer_name: string | null;
  items: FormItem[];
  expires_at: string;
}

export default function VerifyEmploymentPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const [form, setForm] = React.useState<FormPayload | null>(null);
  const [answers, setAnswers] = React.useState<Record<string, boolean>>({});
  const [blocked, setBlocked] = React.useState<string | null>(null);
  const [done, setDone] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    apiGet<FormPayload>(`/bgv/form/${token}`)
      .then(setForm)
      .catch((err: unknown) => {
        // 409 already completed, 410 expired, 404 unknown: the server's own
        // sentence is the page, verbatim.
        setBlocked(
          err instanceof ApiError
            ? err.message
            : "This link could not be opened right now. Please try again."
        );
      });
  }, [token]);

  const submit = React.useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await apiPost(`/bgv/form/${token}`, { answers });
      setDone(true);
    } catch (err: unknown) {
      setError(
        err instanceof ApiError
          ? err.message
          : "The submission did not go through. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }, [token, answers]);

  if (blocked) {
    return (
      <main className="mx-auto max-w-xl px-4 py-16">
        <Card>
          <CardHeader>
            <CardTitle>Employment verification</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-6">{blocked}</p>
          </CardContent>
        </Card>
      </main>
    );
  }

  if (done) {
    return (
      <main className="mx-auto max-w-xl px-4 py-16">
        <Card>
          <CardHeader>
            <CardTitle>Thank you</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-6">
              Your response has been recorded. Nothing further is needed, and
              this link no longer works.
            </p>
          </CardContent>
        </Card>
      </main>
    );
  }

  if (!form) {
    return (
      <main className="mx-auto max-w-xl px-4 py-16">
        <p role="status" className="text-sm">
          Opening the verification form
        </p>
      </main>
    );
  }

  const unanswered = form.items.filter((item) => !(item.key in answers));

  return (
    <main className="mx-auto max-w-xl px-4 py-16">
      <Card>
        <CardHeader>
          <CardTitle>
            Employment verification for {form.candidate_name ?? "a candidate"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="text-sm leading-6">
            {form.candidate_name ?? "This candidate"} has named{" "}
            {form.employer_name ?? "your organisation"} as a previous employer.
            Please confirm each statement below. This takes under two minutes,
            and the link works once.
          </p>
          <ul className="space-y-4">
            {form.items.map((item) => (
              <li key={item.key} className="space-y-1.5">
                <p className="text-sm font-medium leading-6">
                  {item.label}
                  {item.required ? null : (
                    <span className="ml-2 text-xs font-normal">Optional</span>
                  )}
                </p>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant={answers[item.key] === true ? "default" : "outline"}
                    onClick={() =>
                      setAnswers((a) => ({ ...a, [item.key]: true }))
                    }
                  >
                    Confirm
                  </Button>
                  <Button
                    size="sm"
                    variant={
                      answers[item.key] === false ? "default" : "outline"
                    }
                    onClick={() =>
                      setAnswers((a) => ({ ...a, [item.key]: false }))
                    }
                  >
                    Cannot confirm
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          {error ? (
            <p role="alert" className="text-sm font-medium leading-6">
              {error}
            </p>
          ) : null}
          <Button
            size="lg"
            disabled={busy || unanswered.length > 0}
            onClick={() => void submit()}
          >
            {busy
              ? "Submitting"
              : unanswered.length > 0
                ? `Answer ${unanswered.length} more to submit`
                : "Submit verification"}
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
