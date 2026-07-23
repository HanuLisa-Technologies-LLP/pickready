"use client";

// Shared review layout (FR-7.1): candidate names in the left column;
// selecting one loads the full Profile (resume fields + 40 aspects +
// employer verification) in the right pane. Action buttons are injected
// per role via renderActions.

import * as React from "react";
import { FileText } from "lucide-react";

import { apiGet } from "@/lib/api";
import type { CandidateLink, CandidateProfile } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AspectsReadout } from "@/components/aspects-form";
import { MatchBreakdownView } from "@/components/matching-results";
import { TierBadge } from "@/components/tier-badge";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function ProfileReview({
  links,
  renderActions,
  emptyMessage = "No candidates to review.",
}: {
  links: CandidateLink[];
  renderActions?: (link: CandidateLink) => React.ReactNode;
  emptyMessage?: string;
}) {
  const [selected, setSelected] = React.useState<CandidateLink | null>(null);
  const [profile, setProfile] = React.useState<CandidateProfile | null>(null);
  const [loadingProfile, setLoadingProfile] = React.useState(false);

  React.useEffect(() => {
    if (!selected && links.length > 0) {
      setSelected(links[0]);
    }
  }, [links, selected]);

  React.useEffect(() => {
    if (!selected) {
      setProfile(null);
      return;
    }
    let cancelled = false;
    setLoadingProfile(true);
    apiGet<CandidateProfile | { profile: CandidateProfile }>(
      `/candidates/${selected.candidate.id}/profile`
    )
      .then((res) => {
        if (cancelled) return;
        const p =
          "profile" in (res as object) &&
          (res as { profile?: CandidateProfile }).profile
            ? (res as { profile: CandidateProfile }).profile
            : (res as CandidateProfile);
        setProfile(p);
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

  if (links.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyMessage}</p>;
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[280px,1fr]">
      {/* Left column — candidate names (FR-7.1) */}
      <div className="space-y-1">
        {links.map((link) => (
          <button
            key={link.link_id}
            className={cn(
              "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition-colors",
              selected?.link_id === link.link_id
                ? "bg-primary text-primary-foreground"
                : "hover:bg-accent"
            )}
            onClick={() => setSelected(link)}
          >
            <span className="truncate font-medium">
              {link.candidate.full_name || link.candidate.email}
            </span>
            {typeof link.match_score === "number" ? (
              <span className="ml-2 shrink-0 text-xs opacity-80">
                {Math.round(link.match_score)}%
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {/* Right pane — full profile */}
      <div className="min-w-0">
        {selected ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold">
                  {selected.candidate.full_name || selected.candidate.email}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {selected.candidate.email}
                  {selected.candidate.phone
                    ? ` · ${selected.candidate.phone}`
                    : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="capitalize">
                  {selected.source}
                </Badge>
                <TierBadge tier={selected.tier} />
                <StatusBadge status={selected.status} />
              </div>
            </div>

            {renderActions ? (
              <div className="flex flex-wrap gap-2">{renderActions(selected)}</div>
            ) : null}

            <Separator />

            {loadingProfile ? (
              <p className="text-sm text-muted-foreground">Loading profile…</p>
            ) : profile ? (
              <Tabs defaultValue="aspects">
                <TabsList>
                  <TabsTrigger value="aspects">40 Aspects</TabsTrigger>
                  <TabsTrigger value="scores">Match scores</TabsTrigger>
                  <TabsTrigger value="resume">Resume</TabsTrigger>
                  <TabsTrigger value="verification">Verification</TabsTrigger>
                </TabsList>

                <TabsContent value="scores">
                  {/* 4-parameter ranking breakdown (rev 2): 5 scores + 5
                      comments for this candidate on the selected job. */}
                  <MatchBreakdownView
                    breakdown={selected.breakdown}
                    rationale={selected.rationale}
                  />
                </TabsContent>

                <TabsContent value="aspects">
                  {profile.aspects && profile.aspects.length > 0 ? (
                    <AspectsReadout aspects={profile.aspects} />
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No aspect responses yet
                      {selected.source === "databank"
                        ? " — Databank profile reused as-is."
                        : "."}
                    </p>
                  )}
                </TabsContent>

                <TabsContent value="resume">
                  <div className="space-y-4">
                    {profile.resume_url ? (
                      <a
                        href={profile.resume_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-2 text-sm underline underline-offset-2"
                      >
                        <FileText className="h-4 w-4" /> Open resume file
                      </a>
                    ) : null}
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
                        <div>
                          <h4 className="mb-1 text-sm font-semibold">
                            Parsed resume data
                          </h4>
                          <pre className="max-h-96 overflow-auto rounded-md border bg-muted p-3 text-xs">
                            {JSON.stringify(profile.resume_fields, null, 2)}
                          </pre>
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        Resume not parsed yet.
                      </p>
                    )}
                  </div>
                </TabsContent>

                <TabsContent value="verification">
                  {profile.verification && profile.verification.length > 0 ? (
                    <div className="space-y-3">
                      {profile.verification.map((v) => (
                        <div key={v.id} className="rounded-md border p-4">
                          <div className="mb-2 flex items-center justify-between">
                            <p className="text-sm font-medium">
                              {v.employer_email}
                            </p>
                            <StatusBadge
                              status={v.overridden ? "overridden" : v.status}
                            />
                          </div>
                          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                            <dt className="text-muted-foreground">Designation</dt>
                            <dd>{v.designation ?? "—"}</dd>
                            <dt className="text-muted-foreground">DOJ / DOE</dt>
                            <dd>
                              {v.doj ?? "—"} → {v.doe ?? "—"}
                            </dd>
                            <dt className="text-muted-foreground">
                              Last drawn CTC / Gross
                            </dt>
                            <dd>
                              {v.last_drawn_ctc ?? "—"} /{" "}
                              {v.last_drawn_gross ?? "—"}
                            </dd>
                            <dt className="text-muted-foreground">NOC</dt>
                            <dd>{v.noc_status ?? "—"}</dd>
                            <dt className="text-muted-foreground">
                              Exit formalities
                            </dt>
                            <dd>
                              {v.exit_formalities_complete === true
                                ? "Complete"
                                : v.exit_formalities_complete === false
                                  ? "Incomplete"
                                  : "—"}
                            </dd>
                            <dt className="text-muted-foreground">BGV</dt>
                            <dd>{v.bgv_status ?? "—"}</dd>
                          </dl>
                          {v.override_reason ? (
                            <p className="mt-2 text-xs text-muted-foreground">
                              Override reason: {v.override_reason}
                            </p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No employer-verification records.
                    </p>
                  )}
                </TabsContent>
              </Tabs>
            ) : (
              <p className="text-sm text-muted-foreground">
                Could not load this candidate&apos;s profile.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
