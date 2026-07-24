"use client";

// Matching results table (FR-4.5, contract rev 2): tier badge plus an
// expandable, comments-only AI explanation. Stored scores remain available to
// the backend for ranking and audit purposes but are never rendered here.

import * as React from "react";
import { ChevronDown, ChevronRight, RefreshCw, Sparkles } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { MatchBreakdown, MatchingResult } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TierBadge } from "@/components/tier-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const BREAKDOWN_PARAMS: {
  key: keyof Omit<MatchBreakdown, "overall">;
  label: string;
}[] = [
  { key: "skills_match", label: "Skills Match" },
  { key: "experience_relevance", label: "Experience Relevance" },
  { key: "role_alignment", label: "Role & Responsibility" },
  { key: "education_fit", label: "Education & Qualification" },
];

/** Five labelled AI comments. Numeric ranking values are intentionally hidden. */
export function MatchBreakdownView({
  breakdown,
  rationale,
  linkId,
}: {
  breakdown?: MatchBreakdown | null;
  /** Fallback overall comment (match_rationale) when breakdown is absent. */
  rationale?: string | null;
  /** Logged server-side after the comments are rendered. */
  linkId?: string;
}) {
  const telemetrySentFor = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (!linkId || telemetrySentFor.current === linkId) return;
    telemetrySentFor.current = linkId;
    void apiPost(`/telemetry/rating-comments-view/${linkId}`).catch(() => {
      // Telemetry failures must never interrupt a candidate review.
    });
  }, [linkId]);

  if (!breakdown) {
    return (
      <p className="text-sm text-muted-foreground">
        {rationale ?? "No ranking breakdown available yet."}
      </p>
    );
  }
  return (
    <section className="space-y-3" aria-label="AI match comments">
      <div className="grid gap-2 sm:grid-cols-2">
        {BREAKDOWN_PARAMS.map(({ key, label }) => {
          const p = breakdown[key];
          return (
            <section key={key} className="rounded-md border p-3" aria-labelledby={`match-comment-${key}`}>
              <h3 id={`match-comment-${key}`} className="mb-1 text-sm font-medium">
                {label}
              </h3>
              <p className="text-sm leading-6 text-muted-foreground">
                {p?.comment || "—"}
              </p>
            </section>
          );
        })}
      </div>
      <section className="rounded-md border bg-muted/50 p-3" aria-labelledby="match-comment-overall">
        <h3 id="match-comment-overall" className="mb-1 text-sm font-semibold">
          Overall Recommendation
        </h3>
        <p className="text-sm leading-6 text-muted-foreground">
          {breakdown.overall?.comment || rationale || "—"}
        </p>
      </section>
    </section>
  );
}

export function MatchingResults({
  jobId,
  selectable = false,
  selected,
  onToggleSelect,
  canTrigger = true,
}: {
  jobId: string;
  selectable?: boolean;
  /** candidate ids currently selected (outreach send) */
  selected?: Set<string>;
  onToggleSelect?: (candidateId: string, source: string) => void;
  /** Show the trigger-matching action (capability `trigger_matching`). */
  canTrigger?: boolean;
}) {
  const { toast } = useToast();
  const [results, setResults] = React.useState<MatchingResult[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [triggering, setTriggering] = React.useState(false);
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<
        MatchingResult[] | { results: MatchingResult[] }
      >(`/matching/jobs/${jobId}/results`);
      setResults(Array.isArray(res) ? res : res.results ?? []);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const trigger = async () => {
    setTriggering(true);
    try {
      await apiPost(`/matching/jobs/${jobId}/run`);
      toast({
        title: "Matching queued",
        description:
          "The AI contextual rating runs asynchronously — refresh results shortly.",
      });
    } catch (e) {
      toast({
        title: "Could not queue matching",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setTriggering(false);
    }
  };

  const toggleExpanded = (linkId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(linkId)) next.delete(linkId);
      else next.add(linkId);
      return next;
    });
  };

  const colSpan = selectable ? 5 : 4;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {canTrigger ? (
          <Button
            onClick={() => void trigger()}
            disabled={triggering}
            className="gap-2"
          >
            <Sparkles className="h-4 w-4" />
            {triggering ? "Queuing…" : "Trigger matching"}
          </Button>
        ) : null}
        <Button
          variant="outline"
          onClick={() => void load()}
          disabled={loading}
          className="gap-2"
        >
          <RefreshCw className="h-4 w-4" /> Refresh results
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            {selectable ? <TableHead className="w-10" /> : null}
            <TableHead>Candidate</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Tier</TableHead>
            <TableHead className="w-32">AI comments</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell
                colSpan={colSpan}
                className="text-center text-muted-foreground"
              >
                Loading…
              </TableCell>
            </TableRow>
          ) : results.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={colSpan}
                className="text-center text-muted-foreground"
              >
                No matching results yet — trigger matching to rank linked
                profiles.
              </TableCell>
            </TableRow>
          ) : (
            results.map((r) => (
              <React.Fragment key={r.link_id}>
                <TableRow>
                  {selectable ? (
                    <TableCell>
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-foreground"
                        disabled={r.source !== "fresh"}
                        title={
                          r.source !== "fresh"
                            ? "Databank candidates skip outreach — Profile reused as-is"
                            : undefined
                        }
                        checked={selected?.has(r.candidate.id) ?? false}
                        onChange={() =>
                          onToggleSelect?.(r.candidate.id, r.source)
                        }
                      />
                    </TableCell>
                  ) : null}
                  <TableCell className="font-medium">
                    {r.candidate.full_name || r.candidate.email}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="capitalize">
                      {r.source}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <TierBadge tier={r.tier} />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="gap-1"
                      onClick={() => toggleExpanded(r.link_id)}
                    >
                      {expanded.has(r.link_id) ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                      AI comments
                    </Button>
                  </TableCell>
                </TableRow>
                {expanded.has(r.link_id) ? (
                  <TableRow>
                    <TableCell colSpan={colSpan} className="bg-muted/30">
                      <MatchBreakdownView
                        breakdown={r.breakdown}
                        rationale={r.rationale}
                        linkId={r.link_id}
                      />
                    </TableCell>
                  </TableRow>
                ) : null}
              </React.Fragment>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
