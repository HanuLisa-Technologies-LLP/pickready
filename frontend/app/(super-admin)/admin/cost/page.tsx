"use client";

// Provider Portal -> Cost (change 28D).
//
// WHY THIS IS IN THE OWNER'S CONSOLE AND NOWHERE ELSE
// ---------------------------------------------------
// Every figure here is operational: dollars, tokens, call counts, and a
// per-CLIENT breakdown of what each customer costs the platform to serve. An
// employer surface showing this would be handing one customer a view of every
// other customer's spend, so the boundary is the audience: the backing route
// sits behind `get_superadmin_db`, the same dependency `/admin/llm/stats`
// uses, and there is no tenant-side route that reads the table at all.
//
// WHAT THE NUMBERS ARE, AND THE PAGE SAYS SO IN PLACE
// ----------------------------------------------------
// List-price estimates, not an invoice. The per-token rates are unverified for
// the two model ids in use, prompt-cache hits are counted but not discounted
// because no cached-input rate is on file, and the rupee figures ride on a
// fixed FX rate from settings. Each of those is rendered as a caption next to
// the figure it qualifies rather than buried in a tooltip: an operator who
// cannot see the caveat will quote the number as a fact.
//
// AN UNAVAILABLE READING CARRIES NO NUMBER
// -----------------------------------------
// The server sends `{status: "unavailable", reason}` with no numeric key at
// all for anything it could not compute, and this page renders the reason. A
// month with no assessments did not cost nothing, and the threshold flag then
// reads "not measured" rather than showing a reassuring green tick.

import * as React from "react";
import { AlertTriangle } from "lucide-react";

import { ApiError, apiGet } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  Section,
} from "@/components/page-primitives";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type Money =
  | { status: "available"; usd: number; inr: number }
  | { status: "unavailable"; reason: string };

type Breakdown = {
  assessments: number;
  total_spend: Money;
  average_per_assessment: Money;
};

type TierRow = Breakdown & { pricing_tier: string | null };
type ClientRow = Breakdown & { tenant_id: string; tenant_name: string };

type CostSummary = {
  month: string;
  assessments: number;
  total_spend: Money;
  synthesis_spend: Money;
  average_per_assessment: Money;
  alert: {
    threshold_inr: number;
    exceeded: boolean | null;
    reason?: string;
    average_inr?: number;
  };
  by_pricing_tier: TierRow[];
  by_client: ClientRow[];
  coverage: {
    calls: number;
    calls_with_usage: number;
    calls_reporting_cache: number;
    input_tokens: number;
    output_tokens: number;
    cached_input_tokens: number;
  };
  prompt_cache: {
    cached_input_tokens: number;
    calls_reporting_cache: number;
    saving: { status: string; reason?: string };
  };
  fx: { usd_to_inr: number; basis: string };
  cost_basis: string;
};

const RUPEES = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});
const COUNT = new Intl.NumberFormat("en-IN");

function rupees(value: Money): string {
  return value.status === "available" ? RUPEES.format(value.inr) : "Not measured";
}

function dollars(value: Money): string {
  return value.status === "available"
    ? `about $${value.usd.toFixed(2)}`
    : value.reason;
}

/** One headline figure with the caveat that qualifies it directly underneath. */
function Figure({
  label,
  value,
  caption,
}: {
  label: string;
  value: Money;
  caption: string;
}) {
  return (
    <div className="rounded-xl border border-border p-5">
      <p className="text-sm font-medium">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{rupees(value)}</p>
      <p className="mt-1 text-sm">
        {value.status === "available" ? `${dollars(value)}. ${caption}` : dollars(value)}
      </p>
    </div>
  );
}

export default function ProviderCostPage() {
  const [summary, setSummary] = React.useState<CostSummary | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setSummary(await apiGet<CostSummary>("/admin/cost/assessments"));
    } catch (error) {
      setLoadError(
        error instanceof ApiError || error instanceof Error
          ? error.message
          : "Could not load assessment cost."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageHeader
        title="Assessment cost"
        description="What running assessments costs the platform this month. Internal only."
      />

      {loading ? (
        <LoadingRows rows={5} label="Loading assessment cost" />
      ) : loadError ? (
        <ErrorState
          title="Could not load assessment cost"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          }
        />
      ) : !summary ? (
        <EmptyState
          title="Nothing to show"
          description="Cost figures appear here once assessments have run."
        />
      ) : (
        <div className="space-y-6">
          {summary.alert.exceeded === true ? (
            <div
              role="status"
              className="flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-5"
            >
              <AlertTriangle
                className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
                aria-hidden="true"
              />
              <div>
                <p className="font-semibold">
                  The average assessment cost this month is above{" "}
                  {RUPEES.format(summary.alert.threshold_inr)}
                </p>
                <p className="text-sm">
                  {summary.month} is averaging{" "}
                  {RUPEES.format(summary.alert.average_inr ?? 0)} per assessment.
                  This is a flag, not a limit: nothing in the product refuses
                  work because of it.
                </p>
              </div>
            </div>
          ) : summary.alert.exceeded === null ? (
            <div className="rounded-xl border border-border p-5">
              <p className="font-semibold">Not measured this month</p>
              <p className="text-sm">
                {summary.alert.reason ?? "The average could not be computed."}
              </p>
            </div>
          ) : null}

          <Section title={`This month (${summary.month})`}>
            <div className="grid gap-4 sm:grid-cols-3">
              <Figure
                label="Average per assessment"
                value={summary.average_per_assessment}
                caption={`Across ${COUNT.format(summary.assessments)} assessments.`}
              />
              <Figure
                label="Total model spend"
                value={summary.total_spend}
                caption={summary.cost_basis}
              />
              <Figure
                label="Report synthesis alone"
                value={summary.synthesis_spend}
                caption="The largest single call in an assessment."
              />
            </div>
            <p className="mt-3 text-sm">
              Rupee figures use a fixed rate of {summary.fx.usd_to_inr} to the
              dollar, so they are approximate. {summary.cost_basis}.{" "}
              {COUNT.format(summary.coverage.calls_with_usage)} of{" "}
              {COUNT.format(summary.coverage.calls)} model calls reported usage
              to measure from.
            </p>
          </Section>

          <Section title="Prompt cache">
            <p className="text-sm">
              {COUNT.format(summary.prompt_cache.calls_reporting_cache)} calls
              reported a cache figure, covering{" "}
              {COUNT.format(summary.prompt_cache.cached_input_tokens)} input
              tokens. {summary.prompt_cache.saving.reason ?? ""}
            </p>
          </Section>

          <Section title="By pricing tier">
            {summary.by_pricing_tier.length === 0 ? (
              <EmptyState
                title="No assessments this month"
                description="Tier averages appear once assessments have run."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tier</TableHead>
                    <TableHead className="text-right">Assessments</TableHead>
                    <TableHead className="text-right">Average</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {summary.by_pricing_tier.map((row) => (
                    <TableRow key={row.pricing_tier ?? "no-plan"}>
                      <TableCell>
                        {row.pricing_tier ?? (
                          <Badge variant="outline">No plan</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {COUNT.format(row.assessments)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {rupees(row.average_per_assessment)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {rupees(row.total_spend)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          <Section title="By client">
            {summary.by_client.length === 0 ? (
              <EmptyState
                title="No assessments this month"
                description="Per-client cost appears once assessments have run."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Customer</TableHead>
                    <TableHead className="text-right">Assessments</TableHead>
                    <TableHead className="text-right">Average</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {summary.by_client.map((row) => (
                    <TableRow key={row.tenant_id}>
                      <TableCell>{row.tenant_name}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {COUNT.format(row.assessments)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {rupees(row.average_per_assessment)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {rupees(row.total_spend)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
        </div>
      )}
    </>
  );
}
