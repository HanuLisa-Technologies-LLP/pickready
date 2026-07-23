"use client";

// Granted profiles review (FR-8.2): read-only 40-aspect Profile in the right
// pane, with Rejected / Shortlisted / Hold. Hold requires mandatory remarks.

import * as React from "react";

import { apiGet, apiPost } from "@/lib/api";
import type { CandidateLink, Job } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { ProfileReview } from "@/components/profile-review";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export default function GrantedProfilesPage() {
  const { toast } = useToast();
  const [jobs, setJobs] = React.useState<Job[]>([]);
  const [jobId, setJobId] = React.useState<string>("");
  const [links, setLinks] = React.useState<CandidateLink[]>([]);
  const [loading, setLoading] = React.useState(false);

  // Hold dialog — remarks are MANDATORY (FR-8.2)
  const [holdTarget, setHoldTarget] = React.useState<CandidateLink | null>(null);
  const [holdRemarks, setHoldRemarks] = React.useState("");
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
      const all = Array.isArray(res) ? res : res.links ?? [];
      // Hiring Managers only see profiles HR has granted access to.
      setLinks(all.filter((l) => l.hm_access_granted !== false));
    } catch (e) {
      toast({
        title: "Failed to load profiles",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
      setLinks([]);
    } finally {
      setLoading(false);
    }
  }, [jobId, toast]);

  React.useEffect(() => {
    void loadLinks();
  }, [loadLinks]);

  const decide = async (
    link: CandidateLink,
    status: "rejected" | "shortlisted" | "hold",
    remarks?: string
  ) => {
    setBusy(true);
    try {
      await apiPost(`/candidates/links/${link.link_id}/decision`, {
        status,
        ...(remarks ? { remarks } : {}),
      });
      toast({
        title: `Marked ${status}`,
        description: link.candidate.full_name || link.candidate.email,
      });
      setHoldTarget(null);
      setHoldRemarks("");
      void loadLinks();
    } catch (e) {
      toast({
        title: "Action failed",
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
        title="Granted Profiles"
        description="Profiles HR has granted you access to. Review the 40 aspects and decide."
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
          emptyMessage="No granted profiles for this job yet."
          renderActions={(link) => (
            <>
              <Button
                size="sm"
                disabled={busy}
                onClick={() => void decide(link, "shortlisted")}
              >
                Shortlisted
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setHoldTarget(link)}
              >
                Hold
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => void decide(link, "rejected")}
              >
                Rejected
              </Button>
            </>
          )}
        />
      )}

      <Dialog
        open={holdTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setHoldTarget(null);
            setHoldRemarks("");
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Put{" "}
              {holdTarget?.candidate.full_name || holdTarget?.candidate.email}{" "}
              on Hold
            </DialogTitle>
            <DialogDescription>
              Remarks are mandatory when placing a profile on Hold.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            placeholder="Why is this profile on hold?"
            rows={3}
            value={holdRemarks}
            onChange={(e) => setHoldRemarks(e.target.value)}
          />
          <DialogFooter>
            <Button
              disabled={busy || holdRemarks.trim().length === 0}
              onClick={() => {
                if (holdTarget && holdRemarks.trim()) {
                  void decide(holdTarget, "hold", holdRemarks.trim());
                }
              }}
            >
              {busy ? "Saving…" : "Confirm Hold"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
