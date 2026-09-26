"use client";

// The assessment page: one consent screen, then the proctoring shell around
// the assessment (Appendix B section 1: one mode).
//
// There is no mode choice. Every candidate reads the same terms and agrees to
// each consent item, and only then does the proctoring shell take over (the
// server's rules and the agreement to them, the system check, the monitoring
// session) before the assessment mounts: consent, rules, system check,
// questions, in that order. A session already consented to goes straight
// back into the assessment, so a reload mid-assessment never re-asks a
// settled question.
//
// The server enforces every gate this page renders: a hand-crafted request
// that skips a step meets the same 409 the screens prevent.

import * as React from "react";
import { useParams } from "next/navigation";
import { Loader2 } from "lucide-react";

import { AssessmentConsentScreen } from "@/components/assessment/assessment-consent-screen";
import { AssessmentConversation } from "@/components/assessment/assessment-conversation";
import { ProctoringShell } from "@/components/proctoring/proctoring-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api";
import type { AssessmentConsentState } from "@/lib/types";

type Step = "loading" | "load_failed" | "consent" | "assessment";

export default function AssessmentPage() {
  const { link_id: linkId } = useParams<{ link_id: string }>();
  const [step, setStep] = React.useState<Step>("loading");
  const [state, setState] = React.useState<AssessmentConsentState | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    apiGet<AssessmentConsentState>(`/api/v2/assessments/conversations/links/${linkId}/consent`)
      .then((loaded) => {
        if (cancelled) return;
        setState(loaded);
        setStep(loaded.consented ? "assessment" : "consent");
      })
      .catch((loadError: unknown) => {
        if (cancelled) return;
        setError(
          loadError instanceof Error ? loadError.message : "This assessment could not be loaded."
        );
        setStep("load_failed");
      });
    return () => {
      cancelled = true;
    };
  }, [linkId]);

  const accept = React.useCallback(
    async (consentKeys: string[]) => {
      setBusy(true);
      setError(null);
      try {
        // The ticked items travel with the request: each one is stamped
        // separately server-side, and the server refuses a short list.
        const updated = await apiPost<AssessmentConsentState>(
          `/api/v2/assessments/conversations/links/${linkId}/consent`,
          { consent_keys: consentKeys }
        );
        setState(updated);
        if (updated.consented) setStep("assessment");
        else setError("Your agreement was not recorded. Please try again.");
      } catch (acceptError: unknown) {
        setError(
          acceptError instanceof Error
            ? acceptError.message
            : "Your agreement could not be recorded. Please try again."
        );
      } finally {
        setBusy(false);
      }
    },
    [linkId]
  );

  if (step === "loading") {
    return (
      <div className="mx-auto flex max-w-2xl items-center gap-3 py-16 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading your assessment...
      </div>
    );
  }

  if (step === "load_failed") {
    return (
      <div className="mx-auto max-w-2xl">
        <Card>
          <CardHeader>
            <CardTitle>This assessment is not available</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (step === "consent" && state) {
    return (
      <AssessmentConsentScreen
        terms={state.consent}
        busy={busy}
        error={error}
        onAccept={(consentKeys) => void accept(consentKeys)}
      />
    );
  }

  return (
    <ProctoringShell linkId={linkId}>
      <AssessmentConversation linkId={linkId} />
    </ProctoringShell>
  );
}
