"use client";

// Shortlist / Reject / Hold, inline in the job page's candidate table.
//
// These actions used to live on the standalone Review Screen. That page is gone
// (2026-07-27 spec), so the actions move here rather than disappearing, the
// spec's own permission matrix still lists `shortlist_reject_hold` as a
// capability, so it has to be reachable somewhere.
//
// Hold requires remarks: the backend returns 422 without them, and "on hold"
// with no reason is not useful to whoever picks the application up next.

import * as React from "react";
import { PauseCircle, ThumbsDown, ThumbsUp } from "lucide-react";

import { apiPost } from "@/lib/api";
import type { RankedCandidate } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type Decision = "shortlisted" | "rejected" | "hold";

export function CandidateDecisionActions({
  row,
  onDecided,
}: {
  row: RankedCandidate;
  onDecided?: () => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);
  const [holdOpen, setHoldOpen] = React.useState(false);
  const [remarks, setRemarks] = React.useState("");

  const decide = async (status: Decision, note?: string) => {
    setBusy(true);
    try {
      await apiPost(`/candidates/links/${row.link_id}/decision`, {
        status,
        remarks: note,
      });
      toast({ title: `Marked ${status}`, description: row.full_name });
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
    <>
      <div className="flex flex-col gap-1">
        <Button
          variant="outline"
          size="sm"
          className="justify-start gap-1"
          disabled={busy || Boolean(row.archived_at)}
          onClick={() => void decide("shortlisted")}
        >
          <ThumbsUp className="h-3.5 w-3.5" /> Shortlist
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start gap-1"
          disabled={busy || Boolean(row.archived_at)}
          onClick={() => setHoldOpen(true)}
        >
          <PauseCircle className="h-3.5 w-3.5" /> Hold
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start gap-1"
          disabled={busy || Boolean(row.archived_at)}
          onClick={() => void decide("rejected")}
        >
          <ThumbsDown className="h-3.5 w-3.5" /> Reject
        </Button>
      </div>

      <Dialog open={holdOpen} onOpenChange={setHoldOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Put {row.full_name} on hold</DialogTitle>
            <DialogDescription>
              A reason is required, whoever picks this application up next needs
              to know why it is paused.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="hold-remarks">Reason</Label>
            <Textarea
              id="hold-remarks"
              rows={3}
              value={remarks}
              placeholder="Headcount not confirmed until next quarter…"
              onChange={(e) => setRemarks(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setHoldOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={busy || !remarks.trim()}
              onClick={() => void decide("hold", remarks.trim())}
            >
              {busy ? "Saving" : "Put on hold"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
