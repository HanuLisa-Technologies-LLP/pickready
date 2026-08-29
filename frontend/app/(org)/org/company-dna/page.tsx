"use client";

// Customer Portal -> Company DNA. Layer 2, owned by the HR Manager.
//
// FOUR STATES, AND NONE OF THEM IS A DEAD END
// -------------------------------------------
//   nothing yet, and you may author   an entry point that explains the
//                                     consequence and starts the intake
//   a draft is open                   the session, resumable, section by section
//   an artifact exists                what it means, in plain language, with a
//                                     clear way to write a new version
//   read-only                         the artifact in force, and a sentence
//                                     saying who can change it
//
// The read-only case is the one worth being careful about. A Recruiter or
// Hiring Manager reaching this page is not doing something wrong: spec-doc6 D3
// gives them the compiled artifact deliberately, because they need it to
// understand why weights landed where they did. What they never get is the raw
// session, and the server does not put it in their response at all.

import * as React from "react";

import { ApiError, apiGet, apiPost } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  Section as Card,
} from "@/components/page-primitives";
import { UnderstandingBlocks } from "@/components/company-dna/understanding-blocks";
import { IntakeSessionSurface } from "@/components/company-dna/intake-session";
import { VersionHistory } from "@/components/company-dna/version-history";
import type {
  CompanyDnaOverview,
  IntakeSession,
} from "@/components/company-dna/types";
import { companyDnaPath } from "@/components/company-dna/types";

export default function CompanyDnaPage() {
  const { user } = useAuth();
  const tenantId = user?.tenant_id ?? null;

  const [overview, setOverview] = React.useState<CompanyDnaOverview | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [forbidden, setForbidden] = React.useState(false);
  const [starting, setStarting] = React.useState(false);
  const [startError, setStartError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (!tenantId) return;
    setLoadError(null);
    try {
      setOverview(await apiGet<CompanyDnaOverview>(companyDnaPath(tenantId)));
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setForbidden(true);
      } else {
        setLoadError(
          error instanceof Error
            ? error.message
            : "Your Company DNA could not be loaded."
        );
      }
    }
  }, [tenantId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const start = async (copyFromVersion: number | null) => {
    if (!tenantId) return;
    setStarting(true);
    setStartError(null);
    try {
      await apiPost<IntakeSession>(companyDnaPath(tenantId), {
        copy_from_version: copyFromVersion,
      });
      await load();
    } catch (error) {
      setStartError(
        error instanceof Error ? error.message : "The session could not be started."
      );
    } finally {
      setStarting(false);
    }
  };

  const onSession = React.useCallback((session: IntakeSession) => {
    setOverview((current) => (current ? { ...current, session } : current));
  }, []);

  if (forbidden) {
    return (
      <div>
        <PageHeader
          eyebrow="Customer Portal"
          title="Company DNA"
          description="How your company hires, captured once and applied to every job."
        />
        <ErrorState
          title="Not yours to view"
          description="Ask your Super Admin or HR Manager for access to your company's hiring philosophy."
        />
      </div>
    );
  }

  if (loadError) {
    return (
      <div>
        <PageHeader eyebrow="Customer Portal" title="Company DNA" />
        <ErrorState
          title="Company DNA could not be loaded"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          }
        />
      </div>
    );
  }

  if (!overview) {
    return (
      <div>
        <PageHeader eyebrow="Customer Portal" title="Company DNA" />
        <LoadingRows rows={6} label="Loading your Company DNA" />
      </div>
    );
  }

  const { permissions, compiled, session, scorecard } = overview;

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Company DNA"
        description="How your company hires, captured once and applied to every job you post. Answered by your HR Manager or Super Admin."
        actions={
          permissions.can_author && compiled && !session ? (
            <Button
              variant="outline"
              disabled={starting}
              onClick={() => void start(compiled.version)}
            >
              Create a new version
            </Button>
          ) : null
        }
      />

      {scorecard.blocked ? (
        <div className="mb-6 rounded-xl border border-navy-200 bg-navy-50 p-4">
          <p className="text-sm font-semibold">{scorecard.message}</p>
          <p className="mt-1 text-sm leading-6">
            You can create jobs and draft descriptions in the meantime. What
            needs this is the evaluation criteria a job is assessed against.
          </p>
        </div>
      ) : null}

      {startError ? (
        <p role="alert" className="mb-6 text-sm font-medium">
          {startError}
        </p>
      ) : null}

      {session ? (
        <IntakeSessionSurface
          clientId={overview.client_id}
          session={session}
          onSession={onSession}
          onCompleted={() => void load()}
        />
      ) : compiled ? (
        <Card
          title="What this means for how candidates are evaluated"
          description={`Version ${compiled.version}, confirmed by ${
            compiled.authored_by ?? "a member of your team"
          }.`}
        >
          <UnderstandingBlocks blocks={compiled.understanding} />
          {permissions.can_author ? null : (
            <p className="mt-8 border-t border-border pt-6 text-sm leading-6">
              Your Super Admin or HR Manager writes and revises this. You are
              seeing it because it decides what every candidate on your jobs is
              graded against.
            </p>
          )}
        </Card>
      ) : permissions.can_author ? (
        <Card title="Start your Company DNA">
          <p className="max-w-2xl text-sm leading-6">
            A structured session, answered once. It asks how your company
            actually hires: the trade-offs you make, what your strongest people
            demonstrably do, what has gone wrong before, and what is
            non-negotiable. Everything you say is compiled into the criteria
            every job you post is evaluated against, and we state that back to
            you before anything is saved.
          </p>
          <p className="mt-3 max-w-2xl text-sm leading-6">
            You can stop at any point and come back to it.
          </p>
          <Button
            className="mt-6"
            disabled={starting}
            onClick={() => void start(null)}
          >
            {starting ? "Starting" : "Start the intake"}
          </Button>
        </Card>
      ) : (
        <EmptyState
          title="Your company has not set this up yet"
          description="Your Super Admin or HR Manager answers it once. Until they do, a job's evaluation criteria cannot be locked."
        />
      )}

      {permissions.can_author ? (
        <div className="mt-8">
          <VersionHistory clientId={overview.client_id} />
        </div>
      ) : null}
    </div>
  );
}
