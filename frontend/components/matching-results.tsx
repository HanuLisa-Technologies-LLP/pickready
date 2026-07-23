"use client";

// Matching results table (FR-4.5, contract rev 2): overall score (X.X/10) +
// tier badge, with an expandable per-row breakdown showing all 4 ranking
// parameters (1–10 + comment) PLUS the holistic overall comment.
// Ordered by score. Optional row selection for outreach.

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
  weight: string;
}[] = [
  { key: "skills_match", label: "Skills match", weight: "35%" },
  { key: "experience_relevance", label: "Experience relevance", weight: "30%" },
  { key: "role_alignment", label: "Role alignment", weight: "20%" },
  { key: "education_fit", label: "Education fit", weight: "15%" },
];

/** All 5 scores + all 5 comments of the 4-parameter breakdown (rev 2). */
export function MatchBreakdownView({
  breakdown,
  rationale,
}: {
  breakdown?: MatchBreakdown | null;
  /** Fallback overall comment (match_rationale) when breakdown is absent. */
  rationale?: string | null;
}) {
  if (!breakdown) {
    return (
      <p className="text-sm text-muted-foreground">
        {rationale ?? "No ranking breakdown available yet."}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      <div className="grid gap-2 sm:grid-cols-2">
        {BREAKDOWN_PARAMS.map(({ key, label, weight }) => {
          const p = breakdown[key];
          return (
            <div key={key} className="rounded-md border p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-medium">{label}</span>
                <span className="font-mono text-sm">
                  {p ? `${p.score}/10` : "—"}
                  <span className="ml-1 text-xs text-muted-foreground">
                    ({weight})
                  </span>
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                {p?.comment || "—"}
              </p>
            </div>
          );
        })}
      </div>
      <div className="rounded-md border bg-muted/50 p-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm font-semibold">Overall</span>
          <span className="font-mono text-sm font-semibold">
            {breakdown.overall ? breakdown.overall.score.toFixed(1) : "—"}/10
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          {breakdown.overall?.comment || rationale || "—"}
        </p>
      </div>
    </div>
  );
}

/** Overall score for display: prefer breakdown.overall, else match_score/10. */
function overallScore(r: { match_score?: number | null; breakdown?: MatchBreakdown | null }): string {
  if (r.breakdown?.overall) return r.breakdown.overall.score.toFixed(1);
  if (typeof r.match_score === "number") return (r.match_score / 10).toFixed(1);
  return "—";
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

  const colSpan = selectable ? 6 : 5;

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
            <TableHead className="text-right">Overall</TableHead>
            <TableHead>Tier</TableHead>
            <TableHead className="w-32">Breakdown</TableHead>
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
                  <TableCell className="text-right font-mono">
                    {overallScore(r)}/10
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
                      Scores
                    </Button>
                  </TableCell>
                </TableRow>
                {expanded.has(r.link_id) ? (
                  <TableRow>
                    <TableCell colSpan={colSpan} className="bg-muted/30">
                      <MatchBreakdownView
                        breakdown={r.breakdown}
                        rationale={r.rationale}
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
