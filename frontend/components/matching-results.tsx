"use client";

// Matching results table (FR-4.5): score + tier badges, ordered by score.
// Shared by HR and Recruiter job pages. Optional row selection for outreach.

import * as React from "react";
import { RefreshCw, Sparkles } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { MatchingResult } from "@/lib/types";
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

export function MatchingResults({
  jobId,
  selectable = false,
  selected,
  onToggleSelect,
}: {
  jobId: string;
  selectable?: boolean;
  /** candidate ids currently selected (outreach send) */
  selected?: Set<string>;
  onToggleSelect?: (candidateId: string, source: string) => void;
}) {
  const { toast } = useToast();
  const [results, setResults] = React.useState<MatchingResult[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [triggering, setTriggering] = React.useState(false);

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button
          onClick={() => void trigger()}
          disabled={triggering}
          className="gap-2"
        >
          <Sparkles className="h-4 w-4" />
          {triggering ? "Queuing…" : "Trigger matching"}
        </Button>
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
            <TableHead className="text-right">Score</TableHead>
            <TableHead>Tier</TableHead>
            <TableHead>Rationale</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell
                colSpan={selectable ? 6 : 5}
                className="text-center text-muted-foreground"
              >
                Loading…
              </TableCell>
            </TableRow>
          ) : results.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={selectable ? 6 : 5}
                className="text-center text-muted-foreground"
              >
                No matching results yet — trigger matching to rank linked
                profiles.
              </TableCell>
            </TableRow>
          ) : (
            results.map((r) => (
              <TableRow key={r.link_id}>
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
                  {Math.round(r.match_score)}%
                </TableCell>
                <TableCell>
                  <TierBadge tier={r.tier} />
                </TableCell>
                <TableCell className="max-w-md truncate text-xs text-muted-foreground">
                  {r.rationale ?? "—"}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
