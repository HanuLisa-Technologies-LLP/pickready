"use client";

// The candidate's employment declaration, and the one-way door at the end.
//
// THIS IS NOT `background-verification-card.tsx`. That card is the candidate's
// OWN verification of their history: departmental mailboxes, the candidate
// sends it, the candidate chooses who sees the result, and it gates nothing.
// This one is what a hiring company verifies before making an offer: named HR
// contacts, the recruitment team sends it, and an unverified employer holds an
// offer. Two features, two owners, deliberately separate screens.
//
// THE WARNING IS THE FEATURE, NOT DECORATION
// The submission is irreversible, so the screen has to make that impossible to
// miss BEFORE the click: the exact sentence comes from the server
// (`submission_warning`) so the wording and the rule cannot drift, the confirm
// step restates it, and the destructive-looking button is the one that is
// actually destructive. A one-way door that is not announced is a trap.
//
// AFTER SUBMISSION the form is not merely disabled, it is REPLACED by a
// read-only record. A disabled form invites somebody to hunt for the way to
// re-enable it; a record says the decision is behind them. The server refuses
// the write regardless, and so does a database trigger.
//
// ONE THING STAYS CORRECTABLE, AND ONLY WHERE THE SERVER SAYS SO
// When a verification request to an employer's HR address could not be
// delivered, the server marks that employer `correction_needed` and accepts a
// new address for it (`PUT /bgv/me/employers/{id}/hr-email`), which resends
// every bounced request with a fresh link. The control appears on exactly
// those rows and nowhere else: the flag is computed by the same condition the
// route accepts a correction on, so the screen never offers a correction the
// server would refuse. The claim itself (employer, title, dates) never
// becomes editable; a correction is recorded beside it.

import * as React from "react";
import {
  Lock,
  MailWarning,
  Plus,
  ShieldCheck,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { ApiError, apiGet, apiPost, apiPut } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { FormField } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { InlineError } from "@/components/page-primitives";

type Background = "fresher" | "experienced";

interface EmploymentRow {
  id?: string;
  employer_name: string;
  designation: string;
  started_on: string;
  ended_on: string;
  hr_name: string;
  hr_email: string;
  /** Server-computed on a stored row: a request to this HR address bounced
   *  and has not been answered, so a corrected address is accepted. */
  correction_needed?: boolean;
}

/** The fields the declaration route takes. `id` and `correction_needed` are
 *  server-owned facts about a stored row, not part of what is declared. */
function declared(row: EmploymentRow) {
  return {
    employer_name: row.employer_name,
    designation: row.designation,
    started_on: row.started_on,
    ended_on: row.ended_on,
    hr_name: row.hr_name,
    hr_email: row.hr_email,
  };
}

interface HistoryOut {
  background: Background | null;
  finalized: boolean;
  finalized_at: string | null;
  employments: EmploymentRow[];
  submission_warning: string;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function emptyRow(): EmploymentRow {
  return {
    employer_name: "",
    designation: "",
    started_on: "",
    ended_on: "",
    hr_name: "",
    hr_email: "",
  };
}

/** Every problem with one employer, so the candidate fixes them in one pass
 *  rather than discovering them one refusal at a time. */
function rowProblems(row: EmploymentRow): string[] {
  const problems: string[] = [];
  if (!row.employer_name.trim()) problems.push("company name");
  if (!row.designation.trim()) problems.push("job title");
  if (!row.started_on) problems.push("start date");
  if (!row.ended_on) problems.push("end date");
  if (!row.hr_name.trim()) problems.push("HR contact name");
  if (!EMAIL_RE.test(row.hr_email.trim())) problems.push("a valid HR email");
  if (row.started_on && row.ended_on && row.ended_on < row.started_on) {
    problems.push("an end date that is not before the start date");
  }
  return problems;
}

export function EmploymentHistoryCard() {
  const { toast } = useToast();
  const [history, setHistory] = React.useState<HistoryOut | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [hidden, setHidden] = React.useState(false);
  const [background, setBackground] = React.useState<Background | null>(null);
  const [rows, setRows] = React.useState<EmploymentRow[]>([]);
  const [confirming, setConfirming] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      const result = await apiGet<HistoryOut>("/bgv/me");
      setHistory(result);
      setBackground(result.background);
      setRows(result.employments.length ? result.employments : []);
      setLoadError(null);
    } catch (error) {
      // A 404 is a normal state, not a failure: a signed-in person with no
      // candidate record yet has no history to declare, and the card does not
      // render. Anything else is a failure and is said as one, because a card
      // that vanished on a 500 would read as "nothing to do here".
      if (error instanceof ApiError && error.status === 404) {
        setLoadError(null);
        setHidden(true);
        return;
      }
      setLoadError(apiErrorMessage(error));
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  if (hidden) return null;
  if (loadError && !history) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Employment history</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <InlineError>
            Your employment history could not be loaded. {loadError}
          </InlineError>
          <Button size="sm" variant="outline" onClick={() => void load()}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }
  if (!history) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Employment history</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm">Loading.</p>
        </CardContent>
      </Card>
    );
  }

  // ── Submitted: a record, not a disabled form ───────────────────────────────
  if (history.finalized) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Lock className="h-4 w-4" aria-hidden="true" />
            <CardTitle className="text-base">Employment history</CardTitle>
          </div>
          <CardDescription>
            Submitted on{" "}
            {history.finalized_at
              ? new Date(history.finalized_at).toLocaleDateString()
              : "an earlier date"}
            . These details are final and are used to verify your employment.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {history.background === "fresher" ? (
            <p className="text-sm">
              You told us you are a fresher, so no previous employment needs to
              be verified.
            </p>
          ) : (
            <ul className="space-y-3">
              {history.employments.map((row) => (
                <li key={row.id} className="space-y-2 rounded-md border p-3">
                  <div>
                    <p className="text-sm font-medium">
                      {row.designation} at {row.employer_name}
                    </p>
                    <p className="text-xs">
                      {row.started_on} to {row.ended_on}
                    </p>
                    <p className="text-xs">
                      HR contact: {row.hr_name} ({row.hr_email})
                    </p>
                  </div>
                  {row.correction_needed && row.id ? (
                    <HrEmailCorrection
                      employmentId={row.id}
                      employerName={row.employer_name}
                      onCorrected={(updated) => setHistory(updated)}
                    />
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <p className="text-xs">
            If something here is wrong, tell the recruitment team through your
            conversation with them. They can record a correction; the submitted
            record itself does not change.
          </p>
          {history.background === "experienced" ? (
            <AppendEmployerSection
              onAdded={(updated) => setHistory(updated)}
            />
          ) : null}
        </CardContent>
      </Card>
    );
  }

  // ── Not yet submitted ─────────────────────────────────────────────────────
  const problems = rows.flatMap((row, index) =>
    rowProblems(row).map((problem) => `Employer ${index + 1} needs ${problem}.`),
  );
  const canSubmit =
    background === "fresher" ||
    (background === "experienced" && rows.length > 0 && problems.length === 0);

  async function save(finalize: boolean) {
    if (!background) return;
    setBusy(true);
    try {
      const result = await apiPut<HistoryOut>("/bgv/me", {
        background,
        employments: background === "fresher" ? [] : rows.map(declared),
        finalize,
      });
      setHistory(result);
      setBackground(result.background);
      setRows(result.employments);
      setConfirming(false);
      toast({
        title: finalize
          ? "Employment history submitted"
          : "Saved. You can still make changes.",
      });
    } catch (error) {
      toast({ title: apiErrorMessage(error), variant: "destructive" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4" aria-hidden="true" />
          <CardTitle className="text-base">Employment history</CardTitle>
        </div>
        <CardDescription>
          Employers verify previous employment before making an offer. Tell us
          whether you have worked before, and if you have, who to contact.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        <div className="space-y-2">
          <p className="text-sm font-medium">Are you a fresher or experienced?</p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant={background === "fresher" ? "default" : "outline"}
              disabled={busy}
              onClick={() => {
                setBackground("fresher");
                setRows([]);
              }}
            >
              Fresher
            </Button>
            <Button
              size="sm"
              variant={background === "experienced" ? "default" : "outline"}
              disabled={busy}
              onClick={() => {
                setBackground("experienced");
                if (rows.length === 0) setRows([emptyRow()]);
              }}
            >
              Experienced
            </Button>
          </div>
        </div>

        {background === "fresher" ? (
          <p className="text-sm">
            No previous employment is needed. Nothing about background
            verification will hold up an offer for you.
          </p>
        ) : null}

        {background === "experienced" ? (
          <div className="space-y-4">
            {rows.map((row, index) => (
              <div key={index} className="space-y-3 rounded-md border p-3">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium">Employer {index + 1}</p>
                  {rows.length > 1 ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => setRows(rows.filter((_, i) => i !== index))}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      <span className="ml-1">Remove</span>
                    </Button>
                  ) : null}
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <FormField label="Company name" htmlFor={`co-${index}`} required>
                    <Input
                      id={`co-${index}`}
                      value={row.employer_name}
                      disabled={busy}
                      onChange={(event) => {
                        const next = [...rows];
                        next[index] = { ...row, employer_name: event.target.value };
                        setRows(next);
                      }}
                    />
                  </FormField>
                  <FormField label="Job title" htmlFor={`role-${index}`} required>
                    <Input
                      id={`role-${index}`}
                      value={row.designation}
                      disabled={busy}
                      onChange={(event) => {
                        const next = [...rows];
                        next[index] = { ...row, designation: event.target.value };
                        setRows(next);
                      }}
                    />
                  </FormField>
                  <FormField label="Start date" htmlFor={`from-${index}`} required>
                    <Input
                      id={`from-${index}`}
                      type="date"
                      value={row.started_on}
                      disabled={busy}
                      onChange={(event) => {
                        const next = [...rows];
                        next[index] = { ...row, started_on: event.target.value };
                        setRows(next);
                      }}
                    />
                  </FormField>
                  <FormField label="End date" htmlFor={`to-${index}`} required>
                    <Input
                      id={`to-${index}`}
                      type="date"
                      value={row.ended_on}
                      disabled={busy}
                      onChange={(event) => {
                        const next = [...rows];
                        next[index] = { ...row, ended_on: event.target.value };
                        setRows(next);
                      }}
                    />
                  </FormField>
                  <FormField label="HR contact name" htmlFor={`hr-${index}`} required>
                    <Input
                      id={`hr-${index}`}
                      value={row.hr_name}
                      disabled={busy}
                      onChange={(event) => {
                        const next = [...rows];
                        next[index] = { ...row, hr_name: event.target.value };
                        setRows(next);
                      }}
                    />
                  </FormField>
                  <FormField
                    label="HR contact email"
                    htmlFor={`hrmail-${index}`}
                    required
                    hint="The verification request goes to this address."
                  >
                    <Input
                      id={`hrmail-${index}`}
                      type="email"
                      value={row.hr_email}
                      disabled={busy}
                      onChange={(event) => {
                        const next = [...rows];
                        next[index] = { ...row, hr_email: event.target.value };
                        setRows(next);
                      }}
                    />
                  </FormField>
                </div>
              </div>
            ))}

            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setRows([...rows, emptyRow()])}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="ml-1">Add another employer</span>
            </Button>
          </div>
        ) : null}

        {background && problems.length > 0 ? (
          <ul className="space-y-1 rounded-md border p-3">
            {problems.map((problem) => (
              <li key={problem} className="text-xs">
                {problem}
              </li>
            ))}
          </ul>
        ) : null}

        {background ? (
          <div className="space-y-3 rounded-md border p-3">
            <div className="flex items-start gap-2">
              <TriangleAlert
                className="mt-0.5 h-4 w-4 shrink-0"
                aria-hidden="true"
              />
              {/* Verbatim from the server, so the warning and the rule it
                  describes can never drift apart. */}
              <p className="text-xs font-medium">{history.submission_warning}</p>
            </div>

            {confirming ? (
              <div className="space-y-2">
                <p className="text-sm font-medium">
                  Submit these details? They cannot be changed afterwards.
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() => void save(true)}
                  >
                    {busy ? "Submitting" : "Yes, submit and lock"}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => setConfirming(false)}
                  >
                    Go back and check
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !background}
                  onClick={() => void save(false)}
                >
                  Save for now
                </Button>
                <Button
                  size="sm"
                  disabled={busy || !canSubmit}
                  onClick={() => setConfirming(true)}
                >
                  Submit for verification
                </Button>
              </div>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ── Add the newest employer after submission (vivekium feature 5) ────────────
//
// The submitted record stays final row by row; what a candidate MAY do is
// append the job they have since moved to. The platform keeps the last two
// employers only, so adding a third automatically and permanently replaces
// the oldest, verifications included, and a new verification for the added
// employer starts on its own. All of that is said BEFORE the button works.

function AppendEmployerSection({
  onAdded,
}: {
  onAdded: (updated: HistoryOut) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [row, setRow] = React.useState<EmploymentRow>(emptyRow());
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const problems = rowProblems(row);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const updated = await apiPost<HistoryOut>("/bgv/me/employers", {
        employer_name: row.employer_name,
        designation: row.designation,
        started_on: row.started_on,
        ended_on: row.ended_on,
        hr_name: row.hr_name,
        hr_email: row.hr_email,
      });
      onAdded(updated);
      setOpen(false);
      setRow(emptyRow());
    } catch (err) {
      // The server's own sentence (the cap, a personal mailbox, a date the
      // history already covers), never the transport's "API error 409".
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="rounded-md border p-3">
        <p className="text-sm font-medium">Moved to a new job since?</p>
        <p className="mt-1 text-xs">
          You can add your newest employer. Your record keeps your last two
          employers only: adding one permanently replaces the oldest,
          including any completed verification for it, and a verification
          request for the new employer is sent automatically.
        </p>
        <Button
          size="sm"
          variant="outline"
          className="mt-2"
          onClick={() => setOpen(true)}
        >
          Add newest employer
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <p className="text-sm font-medium">Your newest employer</p>
      <input
        className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
        placeholder="Company name"
        value={row.employer_name}
        onChange={(e) => setRow({ ...row, employer_name: e.target.value })}
      />
      <input
        className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
        placeholder="Your designation"
        value={row.designation}
        onChange={(e) => setRow({ ...row, designation: e.target.value })}
      />
      <div className="flex gap-2">
        <input
          type="date"
          aria-label="Started on"
          className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
          value={row.started_on}
          onChange={(e) => setRow({ ...row, started_on: e.target.value })}
        />
        <input
          type="date"
          aria-label="Ended on"
          className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
          value={row.ended_on}
          onChange={(e) => setRow({ ...row, ended_on: e.target.value })}
        />
      </div>
      <input
        className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
        placeholder="HR contact name"
        value={row.hr_name}
        onChange={(e) => setRow({ ...row, hr_name: e.target.value })}
      />
      <input
        type="email"
        className="w-full rounded border border-border bg-background px-2 py-1.5 text-sm"
        placeholder="HR department email"
        value={row.hr_email}
        onChange={(e) => setRow({ ...row, hr_email: e.target.value })}
      />
      {error ? (
        <p role="alert" className="text-xs font-medium">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={busy || problems.length > 0}
          onClick={() => void submit()}
        >
          {busy ? "Adding" : "Add and start verification"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => setOpen(false)}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}

// Correct the HR address after a bounce.
//
// ONE FIELD. The route takes an address and nothing else, so this form offers
// nothing else. Its refusals (the address already tried, a personal mailbox)
// are the server's sentences, shown verbatim.

function HrEmailCorrection({
  employmentId,
  employerName,
  onCorrected,
}: {
  employmentId: string;
  employerName: string;
  onCorrected: (updated: HistoryOut) => void;
}) {
  const { toast } = useToast();
  const [address, setAddress] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const fieldId = `hr-correction-${employmentId}`;
  const valid = EMAIL_RE.test(address.trim());

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid) {
      setError("Enter the corrected HR email address.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await apiPut<HistoryOut>(
        `/bgv/me/employers/${employmentId}/hr-email`,
        { hr_email: address.trim() },
      );
      onCorrected(updated);
      toast({
        title: "HR address updated",
        description: `The verification request to ${employerName} has been sent again to the new address.`,
      });
    } catch (failure) {
      setError(apiErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="space-y-2 rounded-md border border-destructive/40 bg-destructive/5 p-3"
      onSubmit={submit}
      noValidate
    >
      <p className="flex items-start gap-2 text-sm font-medium">
        <MailWarning className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        We could not deliver the verification request to this HR address.
      </p>
      <p className="text-xs">
        Check the address with {employerName} and enter the corrected one. We
        will send the request again automatically. Your employment details stay
        exactly as you submitted them.
      </p>
      <FormField label="Corrected HR email" htmlFor={fieldId} required>
        <Input
          id={fieldId}
          type="email"
          autoComplete="off"
          value={address}
          disabled={busy}
          onChange={(event) => setAddress(event.target.value)}
        />
      </FormField>
      {error ? <InlineError>{error}</InlineError> : null}
      <Button type="submit" size="sm" disabled={busy || !valid}>
        {busy ? "Sending" : "Update address and resend"}
      </Button>
    </form>
  );
}
