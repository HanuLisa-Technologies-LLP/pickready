"use client";

// HR Review Screen (FR-7.1, nav-gated by `view_review_screen`): candidate
// names on the left; the selected candidate's 40 aspects + match-score
// breakdown + verification + resume on the right, with grant-HM-access.
// The candidate links (from GET /candidates/jobs/{job_id}) now carry the
// 4-parameter `breakdown` (rev 2), surfaced in the "Match scores" tab.

import * as React from "react";
import { UserCheck } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { CandidateLink, Job } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { ProfileReview } from "@/components/profile-review";
import { HmDecisionActions } from "@/components/hm-decision-actions";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export default function OrgReviewScreen() {
  const { toast } = useToast();
  const { hasCapability } = useAuth();
  // A Hiring Manager acts on profiles (FR-8.2); HR grants access (FR-8.1).
  const canDecide = hasCapability("decide_profile");
  const canGrant = hasCapability("view_review_screen") && !canDecide;
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [jobId, setJobId] = React.useState<string>("");
  const [links, setLinks] = React.useState<CandidateLink[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    apiGet<Job[] | { jobs: Job[] }>("/jobs")
      .then((res) => {
        const all = Array.isArray(res) ? res : res.jobs ?? [];
        setJobs(all);
        if (all.length > 0) setJobId(all[0].id);
      })
      .catch(() => {});
  }, []);

  const loadLinks = React.useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    try {
      const res = await apiGet<CandidateLink[] | { links: CandidateLink[] }>(
        `/candidates/jobs/${jobId}`
      );
      setLinks(Array.isArray(res) ? res : res.links ?? []);
    } catch {
      setLinks([]);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  React.useEffect(() => {
    void loadLinks();
  }, [loadLinks]);

  const grantAccess = async (link: CandidateLink) => {
    setBusy(true);
    try {
      await apiPost(`/candidates/links/${link.link_id}/grant-access`);
      toast({
        title: "Hiring Manager access granted",
        description: link.candidate.full_name || link.candidate.email,
      });
      void loadLinks();
    } catch (e) {
      toast({
        title: "Grant failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="HR Review Screen"
        description="Review each candidate's complete Profile — resume, 40 aspects, match scores and employer verification."
        actions={
          <Select value={jobId} onValueChange={setJobId}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder="Select a job" />
            </SelectTrigger>
            <SelectContent>
              {jobs.map((j) => (
                <SelectItem key={j.id} value={j.id}>
                  {j.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ProfileReview
          links={links}
          emptyMessage="No candidates linked to this job yet."
          renderActions={(link) => {
            // Hiring Manager: act on profiles they've been granted (FR-8.2).
            if (canDecide) {
              return link.hm_access_granted ? (
                <HmDecisionActions link={link} onDecided={() => void loadLinks()} />
              ) : (
                <Badge variant="outline">Awaiting HR access</Badge>
              );
            }
            // HR: grant Hiring Manager access to a reviewed profile (FR-8.1).
            if (canGrant) {
              return link.hm_access_granted ? (
                <Badge variant="secondary">HM access granted</Badge>
              ) : (
                <Button
                  size="sm"
                  className="gap-2"
                  disabled={busy}
                  onClick={() => void grantAccess(link)}
                >
                  <UserCheck className="h-4 w-4" /> Grant Hiring Manager access
                </Button>
              );
            }
            return null;
          }}
        />
      )}
    </div>
  );
}
