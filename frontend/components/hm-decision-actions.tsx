"use client";

// Hiring Manager profile decision (FR-8.2): three actions, Shortlisted,
// Rejected, Hold, on a profile the HM has been granted access to. Hold
// requires a mandatory remarks field (backend returns 422 without it).
// Capability-gated by the caller on `decide_profile`.

import * as React from "react";
import { ThumbsDown, ThumbsUp, PauseCircle } from "lucide-react";

import { apiPost } from "@/lib/api";
import type { CandidateLink } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type Decision = "shortlisted" | "rejected" | "hold";

export function HmDecisionActions({
  link,
  onDecided,
}: {
  link: CandidateLink;
  onDecided?: () => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);
  const [holdOpen, setHoldOpen] = React.useState(false);
  const [remarks, setRemarks] = React.useState("");

  const decide = async (status: Decision, note?: string) => {
    setBusy(true);
    try {
      await apiPost(`/candidates/links/${link.link_id}/decision`, {
        status,
        remarks: note,
      });
      toast({
        title: `Marked ${status}`,
        description: link.candidate.full_name || link.candidate.email,
      });
      setHoldOpen(false);
      setRemarks("");
      onDecided?.();
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
    <div className="flex flex-wrap items-center gap-2">
      {link.current_status ? (
        <Badge variant="secondary" className="capitalize">
          {link.current_status}
        </Badge>
      ) : null}
      <Button
        size="sm"
        variant="outline"
        className="gap-1"
        disabled={busy}
        onClick={() => void decide("shortlisted")}
      >
        <ThumbsUp className="h-4 w-4" /> Shortlist
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="gap-1"
        disabled={busy}
        onClick={() => void decide("rejected")}
      >
        <ThumbsDown className="h-4 w-4" /> Reject
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="gap-1"
        disabled={busy}
        onClick={() => setHoldOpen(true)}
      >
        <PauseCircle className="h-4 w-4" /> Hold
      </Button>

      <Dialog open={holdOpen} onOpenChange={setHoldOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Hold, remarks required</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="hold-remarks">
              Reason for placing this profile on hold
            </Label>
            <Textarea
              id="hold-remarks"
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Mandatory, e.g. awaiting reference check, revisit next quarter…"
              rows={4}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setHoldOpen(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button
              disabled={busy || remarks.trim().length === 0}
              onClick={() => void decide("hold", remarks.trim())}
            >
              Place on hold
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
