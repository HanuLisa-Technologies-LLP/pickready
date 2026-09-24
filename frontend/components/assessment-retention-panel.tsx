"use client";

// A closed job's assessment records: where they are in the thirty day
// retention window, and the dispute path back into them.
//
// WHY THIS EXISTS (vivekium release, Phase 6)
// Closing a job withholds its assessment records at once and deletes them
// thirty days later (change request 22, the 2026-09-22 section of claude.md).
// The Close dialog tells the person clicking it that the records "can be
// retrieved only through the assessment dispute process", and until this
// panel the process had three complete routes and no screen: the promise was
// printed and could not be kept by anybody who did not own a terminal.
//
// THE SERVER WRITES EVERY SENTENCE THAT MATTERS
// The withheld and deleted explanations come from
// `job_assessment_retention` through the route's `message`, verbatim. A
// second copy written here would be a second author of the sentence the 410
// gate answers with, and the day the two disagreed a recruiter would be told
// two different things about the same records.
//
// WHO SEES WHAT
// Everybody who can open the job may read the state; the route is
// deliberately wider than the dispute capability, because the person who
// finds a report gone is owed the reason and the date. Only a holder of
// `retrieve_disputed_assessment` is offered the controls, and everybody else
// is told so through `ReadOnlyNotice`, the one author of that sentence. The
// server re-authorizes both writes regardless.
//
// NO COUNT AND NO CANDIDATE DETAIL
// The route carries dates only, and so does this panel. It shows the deletion
// DATE rather than `days_remaining`, which says the same thing without a
// number standing next to the word "assessment".

import * as React from "react";
import { Archive, Loader2 } from "lucide-react";

import { apiDelete, apiGet, apiPost } from "@/lib/api";
import { CAP } from "@/lib/permissions";
import type { AssessmentRetention } from "@/lib/types";
import { usePermissions } from "@/lib/use-permissions";
import { apiErrorMessage } from "@/lib/validation-errors";
import { InlineError, Section } from "@/components/page-primitives";
import { ReadOnlyNotice } from "@/components/permission-notice";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form";
import { Textarea } from "@/components/ui/textarea";

/** The dispute reason's ceiling, matching `AssessmentDisputeIn.reason`. */
const REASON_MAX = 1000;

function readableDate(value: string | null): string | null {
  if (!value) return null;
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function AssessmentRetentionPanel({ jobId }: { jobId: string }) {
  const { can } = usePermissions();
  const canDispute = can(CAP.retrieveDisputedAssessment);
  const [retention, setRetention] = React.useState<AssessmentRetention | null>(
    null
  );
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);
  const [reason, setReason] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const path = `/jobs/${jobId}`;

  React.useEffect(() => {
    let cancelled = false;
    setRetention(null);
    setLoadError(null);
    apiGet<AssessmentRetention>(`${path}/assessment-retention`)
      .then((state) => {
        if (!cancelled) setRetention(state);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(apiErrorMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  async function act(request: () => Promise<AssessmentRetention>) {
    setBusy(true);
    setActionError(null);
    try {
      setRetention(await request());
      setReason("");
    } catch (error) {
      // The server's refusal, verbatim: a 410 once the records are gone, a
      // 409 while the job is still open, a 422 for an empty reason.
      setActionError(apiErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const openDispute = () =>
    act(() =>
      apiPost<AssessmentRetention>(`${path}/assessment-dispute`, {
        reason: reason.trim(),
      })
    );
  const closeDispute = () =>
    act(() => apiDelete<AssessmentRetention>(`${path}/assessment-dispute`));

  if (loadError) {
    return (
      <Section title="Assessment records">
        <InlineError>{loadError}</InlineError>
      </Section>
    );
  }
  // A live job's records are available in the normal way; there is nothing
  // to say about retention until it closes.
  if (!retention || retention.state === "live") return null;

  const deleteOn = readableDate(retention.purge_due_at);
  const deletedOn = readableDate(retention.purged_at);
  const pending = retention.state === "pending_deletion";

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <Archive className="h-4 w-4" aria-hidden="true" />
          Assessment records
        </span>
      }
      contentClassName="space-y-4"
    >
      {retention.message ? (
        <p className="text-sm" data-testid="retention-message">
          {retention.message}
        </p>
      ) : null}

      {pending && deleteOn ? (
        <p className="text-sm">They will be permanently deleted on {deleteOn}.</p>
      ) : null}
      {!pending && deletedOn ? (
        <p className="text-sm">Deleted on {deletedOn}.</p>
      ) : null}

      {pending && retention.dispute_open ? (
        <div className="space-y-2 rounded-lg border p-4">
          <p className="text-sm font-medium">A dispute is open on this job.</p>
          <p className="text-sm">
            The report and transcript screens answer again for the people who
            hold the dispute permission, until the records are deleted. Opening
            a dispute does not move that date.
          </p>
          {retention.dispute_reason ? (
            <p className="text-sm">
              Reason recorded: {retention.dispute_reason}
            </p>
          ) : null}
          {canDispute ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void closeDispute()}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : null}
              Close the dispute
            </Button>
          ) : null}
        </div>
      ) : null}

      {pending && !retention.dispute_open && canDispute ? (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void openDispute();
          }}
        >
          <FormField
            label="Why are these records being retrieved?"
            htmlFor="dispute-reason"
            required
            hint="Recorded with the retrieval, so a later reviewer can see why the records were read."
          >
            <Textarea
              id="dispute-reason"
              rows={3}
              maxLength={REASON_MAX}
              value={reason}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
            />
          </FormField>
          <Button type="submit" size="sm" disabled={busy || !reason.trim()}>
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : null}
            Open a dispute
          </Button>
        </form>
      ) : null}

      {pending ? (
        <ReadOnlyNotice
          canEdit={canDispute}
          resource="this job's assessment dispute"
        />
      ) : null}

      {actionError ? <InlineError>{actionError}</InlineError> : null}
    </Section>
  );
}
