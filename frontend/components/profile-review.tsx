"use client";

// Shared review layout (FR-7.1): candidate names in the left column;
// selecting one loads the assessment and resume in the right pane. Actions are injected
// per role via renderActions.

import * as React from "react";
import { FileText, Mail } from "lucide-react";

import { apiGet } from "@/lib/api";
import type { CandidateLink, CandidateProfile } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  FunctionalSkillsReportView,
  type FunctionalReport,
} from "@/components/functional-skills-report";
import { ResumeViewer, describeResumeUrl } from "@/components/resume-viewer";
import { SendOutreachModal } from "@/components/send-outreach-modal";
import { TierBadge } from "@/components/tier-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function ProfileReview({
  links,
  jobId,
  canSendOutreach = false,
  renderActions,
  emptyMessage = "No candidates to review.",
}: {
  links: CandidateLink[];
  jobId: string;
  canSendOutreach?: boolean;
  renderActions?: (link: CandidateLink) => React.ReactNode;
  emptyMessage?: string;
}) {
  const [selected, setSelected] = React.useState<CandidateLink | null>(null);
  const [profile, setProfile] = React.useState<CandidateProfile | null>(null);
  const [loadingProfile, setLoadingProfile] = React.useState(false);
  const [report, setReport] = React.useState<FunctionalReport | null>(null);
  const [loadingReport, setLoadingReport] = React.useState(false);
  const [resumeOpen, setResumeOpen] = React.useState(false);
  const [outreachOpen, setOutreachOpen] = React.useState(false);
  const [outreachRecipients, setOutreachRecipients] = React.useState<
    { link_id: string; name: string; email: string }[]
  >([]);
  const [selectedLinkIds, setSelectedLinkIds] = React.useState<Set<string>>(
    () => new Set()
  );

  React.useEffect(() => {
    if (!selected && links.length > 0) {
      setSelected(links[0]);
    }
  }, [links, selected]);

  React.useEffect(() => {
    const validIds = new Set(links.map((link) => link.link_id));
    setSelectedLinkIds(
      (current) =>
        new Set(Array.from(current).filter((id) => validIds.has(id)))
    );
    if (selected && !validIds.has(selected.link_id)) {
      setSelected(links[0] ?? null);
    }
  }, [links, selected]);

  React.useEffect(() => {
    // Never leave one candidate's resume open over another's profile.
    setResumeOpen(false);
    if (!selected) {
      setProfile(null);
      return;
    }
    let cancelled = false;
    setLoadingProfile(true);
    apiGet<CandidateProfile | { profile: CandidateProfile }>(
      `/candidates/${selected.candidate.id}/profile${
        selected.profile_id
          ? `?profile_id=${encodeURIComponent(selected.profile_id)}`
          : ""
      }`
    )
      .then((res) => {
        if (cancelled) return;
        const p =
          "profile" in (res as object) &&
          (res as { profile?: CandidateProfile }).profile
            ? (res as { profile: CandidateProfile }).profile
            : (res as CandidateProfile);
        const aspects = p.aspects_json
          ? Object.entries(p.aspects_json).map(([aspectId, answer]) => ({
              aspect_id: Number(aspectId),
              answer,
            }))
          : p.aspects;
        setProfile({
          ...p,
          profile_id: p.profile_id ?? p.id,
          resume_fields: p.resume_fields ?? p.parsed_fields_json,
          aspects,
          verification: p.verification ?? p.verification_requests,
        });
      })
      .catch(() => {
        if (!cancelled) setProfile(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingProfile(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  React.useEffect(() => {
    if (!selected) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setReport(null);
    setLoadingReport(true);
    apiGet<FunctionalReport>(
      `/api/v2/assessments/reports/links/${selected.link_id}`
    )
      .then((value) => {
        if (!cancelled) setReport(value);
      })
      .catch(() => {
        if (!cancelled) setReport(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingReport(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  if (links.length === 0) {
    return <p className="text-sm">{emptyMessage}</p>;
  }

  const selectedRecipients = links
    .filter((link) => selectedLinkIds.has(link.link_id))
    .filter((link) => Boolean(link.candidate.email))
    .map((link) => ({
      link_id: link.link_id,
      name: link.candidate.full_name || link.candidate.email,
      email: link.candidate.email,
    }));

  const toggleCandidate = (linkId: string) => {
    setSelectedLinkIds((current) => {
      const next = new Set(current);
      if (next.has(linkId)) next.delete(linkId);
      else next.add(linkId);
      return next;
    });
  };

  const allSelected =
    links.length > 0 && links.every((link) => selectedLinkIds.has(link.link_id));

  return (
    <div className="grid gap-6 lg:grid-cols-[280px,1fr]">
      {/* Left column, candidate names (FR-7.1) */}
      <aside className="space-y-3" aria-label="Candidate pool">
        <div className="rounded-md border border-border p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold">Candidates</p>
            <Badge variant={selectedLinkIds.size ? "default" : "secondary"}>
              {selectedLinkIds.size} selected
            </Badge>
          </div>
          <button
            type="button"
            className="mt-2 text-xs font-medium underline underline-offset-2"
            onClick={() =>
              setSelectedLinkIds(
                allSelected
                  ? new Set()
                  : new Set(links.map((link) => link.link_id))
              )
            }
          >
            {allSelected ? "Clear selection" : "Select all"}
          </button>
        </div>
        {links.map((link) => (
          <div
            key={link.link_id}
            className={cn(
              "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors",
              selected?.link_id === link.link_id
                ? "bg-primary text-primary-foreground"
                : "hover:bg-accent"
            )}
          >
            <input
              type="checkbox"
              className="h-4 w-4 shrink-0 cursor-pointer accent-foreground"
              checked={selectedLinkIds.has(link.link_id)}
              aria-label={`Select ${
                link.candidate.full_name || link.candidate.email
              } for email`}
              onChange={() => toggleCandidate(link.link_id)}
            />
            <button
              type="button"
              className="min-w-0 flex-1 truncate text-left font-medium"
              onClick={() => setSelected(link)}
            >
              {link.candidate.full_name || link.candidate.email}
            </button>
          </div>
        ))}
        {canSendOutreach ? (
          <Button
            type="button"
            className="w-full gap-2"
            disabled={selectedLinkIds.size === 0}
            onClick={() => {
              if (!selectedRecipients.length) return;
              setOutreachRecipients(selectedRecipients);
              setOutreachOpen(true);
            }}
          >
            <Mail className="h-4 w-4" />
            Send Email to Selected
          </Button>
        ) : null}
      </aside>

      {/* Right pane, full profile */}
      <div className="min-w-0">
        {selected ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold">
                  {selected.candidate.full_name || selected.candidate.email}
                </h2>
                <p className="text-sm">
                  {selected.candidate.email}
                  {selected.candidate.phone
                    ? ` · ${selected.candidate.phone}`
                    : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <TierBadge tier={selected.tier} />
              </div>
            </div>

            {renderActions ? (
              <div className="flex flex-wrap gap-2">{renderActions(selected)}</div>
            ) : null}

            <Separator />

            {loadingProfile ? (
              <p role="status" className="text-sm">Loading profile</p>
            ) : profile ? (
              <Tabs defaultValue="scores">
                <TabsList>
                  <TabsTrigger value="scores">AI assessment</TabsTrigger>
                  <TabsTrigger value="resume">Resume</TabsTrigger>
                </TabsList>

                <TabsContent value="scores" className="mt-4">
                  {loadingReport ? (
                    <p className="text-sm">
                      Loading PPI Assessment Report
                    </p>
                  ) : report ? (
                    <FunctionalSkillsReportView report={report} />
                  ) : (
                    <div className="rounded-md border border-dashed p-5">
                      <p className="font-medium">PPI Assessment Report not ready</p>
                      <p className="mt-1 text-sm">
                        The candidate&apos;s assessment is still being scored.
                        Run AI matching, then check back for the complete
                        report.
                      </p>
                    </div>
                  )}
                </TabsContent>

                <TabsContent value="resume" className="mt-4">
                  <div className="space-y-4">
                    {/* The document opens in-app, reviewers are never sent to
                        a bare storage URL (resume-viewer.tsx). */}
                    {profile.resume_url ? (
                      <div className="flex flex-wrap items-center gap-3 rounded-md border border-border p-4">
                        <FileText
                          className="h-5 w-5"
                          aria-hidden="true"
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-foreground">
                            {describeResumeUrl(profile.resume_url).fileName}
                          </p>
                          <p className="text-xs">
                            Opens in the in-app document viewer.
                          </p>
                        </div>
                        <Button
                          size="sm"
                          onClick={() => setResumeOpen(true)}
                          aria-haspopup="dialog"
                        >
                          View resume
                        </Button>
                      </div>
                    ) : (
                      <p className="rounded-md border border-dashed border-border p-4 text-sm">
                        No resume file on this profile yet.
                      </p>
                    )}

                    {profile.resume_fields ? (
                      <div className="space-y-3">
                        {profile.resume_fields.skills &&
                        profile.resume_fields.skills.length > 0 ? (
                          <div>
                            <h4 className="mb-1 text-sm font-semibold">Skills</h4>
                            <div className="flex flex-wrap gap-1.5">
                              {profile.resume_fields.skills.map((s) => (
                                <Badge key={s} variant="secondary">
                                  {s}
                                </Badge>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        <details className="rounded-md border border-border">
                          <summary className="cursor-pointer px-3 py-2 text-sm font-semibold">
                            Parsed resume data
                          </summary>
                          <pre className="max-h-96 overflow-auto border-t border-border bg-muted p-3 text-xs">
                            {JSON.stringify(profile.resume_fields, null, 2)}
                          </pre>
                        </details>
                      </div>
                    ) : (
                      <p className="text-sm">
                        Resume not parsed yet, the document above is still
                        readable in the viewer.
                      </p>
                    )}
                  </div>
                </TabsContent>

              </Tabs>
            ) : (
              <p className="text-sm">
                Could not load this candidate&apos;s profile.
              </p>
            )}

            <ResumeViewer
              open={resumeOpen}
              onOpenChange={setResumeOpen}
              resumeUrl={profile?.resume_url}
              profileId={profile?.id ?? profile?.profile_id}
              resumeFileName={profile?.resume_original_filename}
              candidateName={
                selected.candidate.full_name || selected.candidate.email
              }
            />
            <SendOutreachModal
              open={outreachOpen}
              onOpenChange={setOutreachOpen}
              jobId={jobId}
              recipients={outreachRecipients}
              onSent={() => {
                setSelectedLinkIds(new Set());
                setOutreachRecipients([]);
              }}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
