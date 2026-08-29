"use client";

import * as React from "react";
import { Loader2, MessageSquareText, Sparkles } from "lucide-react";

import { apiGet, apiPost, apiPut } from "@/lib/api";
import type {
  CandidateTeamReviews,
  TeamRating,
  TeamReviewRewrite,
} from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

/**
 * A HUMAN reviewer's DECISION, deliberately NOT the four machine grades.
 *
 * The four grades (Highly Matching / Matching / Moderately Matching / Not
 * Matching) are what the product's agents output about a candidate against a
 * job. This is a team member recording what THEY decided. Rendering both on the
 * same words would make a colleague's note read as a machine grade, which is
 * the opposite of what this panel is for.
 *
 * That reasoning is why this panel kept its own vocabulary through the
 * 2026-07-30 scale consolidation, and it still holds. What changed on
 * 2026-08-29 is the vocabulary it protects: Very High / High / Medium / Low /
 * Developing was an ASSESSMENT scale, ordinal, answering "how good", which is
 * the machine's question. Pass / Hold / Reject is a DECISION vocabulary,
 * categorical, answering "what now". `Hold` is not a relabelled `Medium`; it
 * means the reviewer is not deciding yet.
 *
 * Source: the Candidate Dashboard Specification, Column 7. The override-rate
 * mapping onto the machine grades lives in `backend/app/services/team_review.py`
 * and is never rendered here.
 */
const RATING_LABELS: Record<TeamRating, string> = {
  pass: "Pass",
  hold: "Hold",
  reject: "Reject",
};

export function CandidateTeamReviewModal({
  linkId,
  candidateName,
  open,
  onOpenChange,
}: {
  linkId: string | null;
  candidateName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { toast } = useToast();
  const [data, setData] = React.useState<CandidateTeamReviews | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [rewriting, setRewriting] = React.useState(false);
  // Defaults to the abstention, never to an endorsement. A panel that opens
  // pre-set to "Pass" records a decision the reviewer has not made yet, and
  // the whole value of Team Review is that it is an INDEPENDENT judgment.
  const [rating, setRating] = React.useState<TeamRating>("hold");
  const [remarks, setRemarks] = React.useState("");
  const [rewritten, setRewritten] = React.useState("");

  const load = React.useCallback(async () => {
    if (!linkId) return;
    setLoading(true);
    try {
      const next = await apiGet<CandidateTeamReviews>(
        `/candidates/links/${linkId}/team-reviews`
      );
      setData(next);
      const mine = next.reviews.find((review) => review.is_current_user);
      if (mine) {
        setRating(mine.rating);
        setRemarks(mine.remarks);
        setRewritten(mine.ai_rewritten_remarks ?? "");
      } else {
        setRating("hold");
        setRemarks("");
        setRewritten("");
      }
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Could not load team reviews",
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, [linkId, toast]);

  React.useEffect(() => {
    if (open) void load();
  }, [load, open]);

  const rewrite = async () => {
    if (!linkId || remarks.trim().length < 3) return;
    setRewriting(true);
    try {
      const result = await apiPost<TeamReviewRewrite>(
        `/candidates/links/${linkId}/team-reviews/rewrite`,
        { remarks: remarks.trim() }
      );
      setRewritten(result.rewritten_remarks);
      toast({
        title: result.used_ai ? "Remark clarified with AI" : "Remark cleaned up",
        description: "Review the wording before you save it.",
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Could not rewrite the remark",
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setRewriting(false);
    }
  };

  const save = async () => {
    if (!linkId || remarks.trim().length < 3) return;
    setSaving(true);
    try {
      const next = await apiPut<CandidateTeamReviews>(
        `/candidates/links/${linkId}/team-reviews`,
        {
          rating,
          remarks: remarks.trim(),
          ai_rewritten_remarks: rewritten.trim() || null,
        }
      );
      setData(next);
      toast({ title: "Team review saved", description: candidateName });
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Could not save the team review",
        description: error instanceof Error ? error.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Hiring team review · {candidateName}</DialogTitle>
          <DialogDescription>
            Every team member keeps their own verdict and remarks. The consensus appears first.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center gap-2 py-10 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading reviews
          </div>
        ) : (
          <div className="space-y-6">
            <section className="rounded-2xl border border-brand-600/25 bg-brand-100/45 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="font-semibold">Overall team view</h3>
                {data?.overall_rating ? (
                  <Badge variant="brand">{RATING_LABELS[data.overall_rating]}</Badge>
                ) : (
                  <Badge variant="outline">Awaiting reviews</Badge>
                )}
              </div>
              <p className="mt-3 text-sm leading-7">
                {data?.overall_remarks ??
                  "The automatic team summary appears here as reviews are saved."}
              </p>
            </section>

            <section className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-[12rem_1fr]">
                <div className="space-y-1.5">
                  <Label>Your verdict</Label>
                  <Select value={rating} onValueChange={(value) => setRating(value as TeamRating)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {(Object.keys(RATING_LABELS) as TeamRating[]).map((value) => (
                        <SelectItem key={value} value={value}>{RATING_LABELS[value]}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="team-review-remarks">Your remarks</Label>
                  <Textarea
                    id="team-review-remarks"
                    rows={5}
                    maxLength={3000}
                    value={remarks}
                    placeholder="Write the evidence you observed, the strength or concern, and what should happen next."
                    onChange={(event) => {
                      setRemarks(event.target.value);
                      setRewritten("");
                    }}
                  />
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs">{remarks.length}/3000 characters</p>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={rewriting || remarks.trim().length < 3}
                      onClick={() => void rewrite()}
                    >
                      {rewriting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                      Help me clarify with AI
                    </Button>
                  </div>
                </div>
              </div>

              {rewritten ? (
                <div className="space-y-1.5 rounded-xl border border-border bg-surface p-4">
                  <Label htmlFor="team-review-rewritten">AI clarity version · editable</Label>
                  <Textarea
                    id="team-review-rewritten"
                    rows={4}
                    maxLength={3000}
                    value={rewritten}
                    onChange={(event) => setRewritten(event.target.value)}
                  />
                  <p className="text-xs leading-5">
                    Your original remark is preserved. This version is used in the team summary after you save.
                  </p>
                </div>
              ) : null}
            </section>

            {data?.reviews.length ? (
              <section>
                <h3 className="flex items-center gap-2 font-semibold">
                  <MessageSquareText className="h-4 w-4" /> Individual team reviews
                </h3>
                <div className="mt-3 space-y-3">
                  {data.reviews.map((review) => (
                    <article key={review.id} className="rounded-xl border border-border p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-semibold">
                          {review.reviewer_name}{review.is_current_user ? " · You" : ""}
                        </p>
                        <Badge variant="outline">{RATING_LABELS[review.rating]}</Badge>
                      </div>
                      <p className="mt-3 text-sm leading-6">
                        {review.ai_rewritten_remarks || review.remarks}
                      </p>
                    </article>
                  ))}
                </div>
              </section>
            ) : null}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          <Button disabled={saving || remarks.trim().length < 3} onClick={() => void save()}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {saving ? "Saving" : "Save my review"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
