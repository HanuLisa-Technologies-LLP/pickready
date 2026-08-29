"use client";

// Bodha states its compiled understanding back, and the client confirms IT.
//
// WHY THE CONFIRMATION CARRIES A TOKEN
// ------------------------------------
// The button sends back the fingerprint of the understanding shown on this
// screen. The server recompiles from the answers as they are now and compares.
// If an answer moved after this was read, the fingerprints differ and the
// completion is refused with that as the reason. Without it, "read it back for
// confirmation" would be a screen somebody clicked through, and the version
// frozen could be one nobody ever saw.
//
// NO NUMBERS. The compiled artifact is a table of multipliers, thresholds and
// counts. What is shown here is what those MEAN, in sentences, because a client
// confirming a multiplier is confirming that the arithmetic looks plausible,
// which is not something they can check.

import * as React from "react";

import { ApiError, apiPost } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Section as Card } from "@/components/page-primitives";
import { useToast } from "@/components/ui/toast";

import { UnderstandingBlocks } from "./understanding-blocks";
import type { CompiledArtifact, IntakeSession } from "./types";
import { companyDnaPath } from "./types";

export function UnderstandingReadback({
  clientId,
  session,
  onRevisit,
  onCompleted,
}: {
  clientId: string;
  session: IntakeSession;
  onRevisit: () => void;
  onCompleted: () => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);
  const [problem, setProblem] = React.useState<string | null>(null);

  if (!session.ready_to_complete || !session.understanding) {
    return (
      <Card
        title="What we understood"
        description="There are still questions to answer before we can put this together."
      >
        <p className="text-sm leading-6">
          Once every section is answered, this is where we state back what your
          answers mean for how candidates will be evaluated, in plain language,
          for you to confirm.
        </p>
        <Button className="mt-4" variant="outline" onClick={onRevisit}>
          Back to the questions
        </Button>
      </Card>
    );
  }

  const confirm = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await apiPost<CompiledArtifact>(
        `${companyDnaPath(clientId)}/${session.id}/complete`,
        { understanding_token: session.understanding_token }
      );
      toast({
        title: "Company DNA confirmed",
        description: "Every job you post is now evaluated against it.",
      });
      onCompleted();
    } catch (error) {
      // A 409 here is the stale-confirmation case and it has a real message
      // from the server. Showing it verbatim is the point: "an answer changed
      // after you read this" is actionable, and a generic failure is not.
      setProblem(
        error instanceof ApiError && typeof error.detail === "object" && error.detail
          ? String((error.detail as { detail?: unknown }).detail ?? error.message)
          : error instanceof Error
            ? error.message
            : "This could not be confirmed. Try again."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="What we understood"
      description="This is what your answers mean for how candidates will be evaluated. Read it, and tell us whether it is right."
    >
      <UnderstandingBlocks blocks={session.understanding} />

      {problem ? (
        <p
          role="alert"
          className="mt-6 rounded-lg border border-warning bg-warning/10 px-4 py-3 text-sm leading-6"
        >
          {problem}
        </p>
      ) : null}

      <div className="mt-8 flex flex-wrap items-center gap-3 border-t border-border pt-6">
        <Button onClick={() => void confirm()} disabled={busy}>
          {busy ? "Confirming" : "This is right, confirm it"}
        </Button>
        <Button variant="outline" onClick={onRevisit} disabled={busy}>
          Something is wrong, take me back
        </Button>
      </div>
      <p className="mt-3 text-sm leading-6">
        Confirming freezes this as a version. You can create a new version any
        time your hiring philosophy changes, and the one you confirm now stays
        readable for every job already assessed against it.
      </p>
    </Card>
  );
}
