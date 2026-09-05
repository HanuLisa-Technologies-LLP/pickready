"use client";

// The assessment page: mode selection, per-mode consent, then the proctoring
// shell around the chosen experience (dual-mode spec section 2).
//
// The order is fixed: choose a mode, read and accept THAT mode's consent
// terms (the server's own wording, spec 3.2), and only then does the
// proctoring shell take over (its own consent, the system check, the
// monitoring session) before the assessment mounts. Proctoring is mandatory
// in BOTH modes and stores no media; the video mode's recording is the
// separately consented artifact its consent screen describes.
//
// The server enforces every gate this page renders: a hand-crafted request
// that skips a step meets the same 409 the screens prevent.

import * as React from "react";
import { useParams } from "next/navigation";
import { Loader2 } from "lucide-react";

import { AssessmentConsentScreen } from "@/components/assessment/assessment-consent-screen";
import { AssessmentConversation } from "@/components/assessment/assessment-conversation";
import { ModeSelection } from "@/components/assessment/mode-selection";
import { VideoInterview } from "@/components/assessment/video-interview";
import { ProctoringShell } from "@/components/proctoring/proctoring-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, apiPost } from "@/lib/api";
import type { AssessmentMode, AssessmentModeState } from "@/lib/types";

type Step = "loading" | "load_failed" | "mode" | "consent" | "assessment";

export default function UnifiedAssessmentPage() {
  const { link_id: linkId } = useParams<{ link_id: string }>();
  const [step, setStep] = React.useState<Step>("loading");
  const [state, setState] = React.useState<AssessmentModeState | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    apiGet<AssessmentModeState>(
      `/api/v2/assessments/conversations/links/${linkId}/mode`
    )
      .then((loaded) => {
        if (cancelled) return;
        setState(loaded);
        // A session already consented in its mode goes straight back into the
        // assessment (a reload mid-interview must not re-ask settled
        // questions); a frozen mode with no consent goes to that mode's
        // consent; everything else starts at the choice.
        if (loaded.consented) setStep("assessment");
        else if (loaded.mode_frozen) setStep("consent");
        else setStep("mode");
      })
      .catch((loadError: unknown) => {
        if (cancelled) return;
        setError(
          loadError instanceof Error
            ? loadError.message
            : "This assessment could not be loaded."
        );
        setStep("load_failed");
      });
    return () => {
      cancelled = true;
    };
  }, [linkId]);

  const select = React.useCallback(
    async (mode: AssessmentMode) => {
      setBusy(true);
      setError(null);
      try {
        const updated = await apiPost<AssessmentModeState>(
          `/api/v2/assessments/conversations/links/${linkId}/mode`,
          { mode }
        );
        setState(updated);
        setStep(updated.consented ? "assessment" : "consent");
      } catch (selectError: unknown) {
        setError(
          selectError instanceof Error
            ? selectError.message
            : "The mode could not be saved. Please try again."
        );
      } finally {
        setBusy(false);
      }
    },
    [linkId]
  );

  const accept = React.useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await apiPost<AssessmentModeState>(
        `/api/v2/assessments/conversations/links/${linkId}/consent`
      );
      setState(updated);
      setStep("assessment");
    } catch (acceptError: unknown) {
      setError(
        acceptError instanceof Error
          ? acceptError.message
          : "Your agreement could not be recorded. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }, [linkId]);

  if (step === "loading") {
    return (
      <div className="mx-auto flex max-w-2xl items-center gap-3 py-16 text-sm leading-6">
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
            <p className="text-sm leading-6">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (step === "mode" && state) {
    return (
      <div className="space-y-3">
        <ModeSelection
          frozenMode={state.mode_frozen ? state.mode : null}
          busy={busy}
          onSelect={(mode) => void select(mode)}
        />
        {error ? (
          <p role="alert" className="mx-auto max-w-3xl text-sm font-medium leading-6">
            {error}
          </p>
        ) : null}
      </div>
    );
  }

  if (step === "consent" && state) {
    return (
      <AssessmentConsentScreen
        terms={state.consent}
        busy={busy}
        error={error}
        onAccept={() => void accept()}
        onDecline={() => {
          setError(null);
          setStep("mode");
        }}
      />
    );
  }

  return (
    <ProctoringShell linkId={linkId}>
      {state?.mode === "video_interview" ? (
        <VideoInterview linkId={linkId} />
      ) : (
        <AssessmentConversation linkId={linkId} />
      )}
    </ProctoringShell>
  );
}
