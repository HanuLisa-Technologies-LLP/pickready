"use client";

// Provider Portal → Billing (killer-spec §4.1).
//
// Which customer is on which plan, whether their subscription is charging, and
// what their credit balance is. READ-ONLY, like every other Provider view of a
// customer's own data, and read-only by ABSENCE: there is no route in
// api/billing that lets the Provider write a subscription, a plan or a credit,
// so there is nothing to gate here with a flag.
//
// The one number that matters most is the deficit column: a customer whose pool
// has run dry has stopped being able to invite anyone to an assessment, and
// they are unlikely to open a support ticket before they get frustrated.

import * as React from "react";
import { AlertTriangle } from "lucide-react";

import { ApiError, apiGet } from "@/lib/api";
import type { ProviderBillingRow } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  RowCard,
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
import { ExportXlsxButton } from "@/components/export-xlsx-button";

const STATUS_LABELS: Record<string, string> = {
  active: "Active",
  past_due: "Payment pending",
  cancelled: "Cancelled",
  halted: "Halted",
};

function formatDate(value: string | null): string {
  if (!value) return "Not scheduled";
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <Badge variant="outline">No subscription</Badge>;
  return (
    <Badge variant={status === "active" ? "brand" : "outline"}>
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

export default function ProviderBillingPage() {
  const [rows, setRows] = React.useState<ProviderBillingRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setRows(
        await apiGet<ProviderBillingRow[]>("/billing/provider/overview?limit=100")
      );
    } catch (error) {
      setLoadError(
        error instanceof ApiError || error instanceof Error
          ? error.message
          : "Could not load billing."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const inDeficit = rows.filter((row) => row.in_deficit);

  return (
    <>
      <PageHeader
        title="Billing"
        description="Plans, subscription status and credit balances across your customers."
        actions={
          rows.length ? (
            <ExportXlsxButton
              fileName="readypick-provider-billing"
              rows={rows.map((row) => ({
                customer: row.customer_name,
                plan: row.plan_name ?? "No plan",
                status: row.subscription_status ?? "No subscription",
                renews: formatDate(row.current_end),
                credits: row.balance_credits,
                amount_inr: row.balance_inr ?? "No plan rate",
                deficit: row.in_deficit,
              }))}
            />
          ) : null
        }
      />

      {loading ? (
        <LoadingRows rows={6} label="Loading billing" />
      ) : loadError ? (
        <ErrorState
          title="Could not load billing"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          }
        />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No customers yet"
          description="Subscription and credit information appears here once a customer is onboarded."
        />
      ) : (
        <div className="space-y-6">
          {inDeficit.length > 0 ? (
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
                  {inDeficit.length === 1
                    ? "One customer is over their credit limit"
                    : `${inDeficit.length} customers are over their credit limit`}
                </p>
                <p className="mt-1 leading-7">
                  New assessment invitations are paused for{" "}
                  {inDeficit.map((row) => row.customer_name).join(", ")} until
                  their next billing date or an upgrade.
                </p>
              </div>
            </div>
          ) : null}

          <Section
            title="Customers"
            description="Balances are shown in credits. One credit is one completed assessment."
          >
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Customer</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Renews</TableHead>
                    <TableHead className="text-right">Credits / INR value</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.tenant_id}>
                      <TableCell className="font-medium">
                        {row.customer_name}
                      </TableCell>
                      <TableCell>{row.plan_name ?? "No plan"}</TableCell>
                      <TableCell>
                        <StatusBadge status={row.subscription_status} />
                      </TableCell>
                      <TableCell>{formatDate(row.current_end)}</TableCell>
                      <TableCell className="text-right font-medium">
                        <span className="block">{row.balance_credits} credits</span>
                        <span className="mt-0.5 block text-xs font-normal">
                          {row.balance_inr !== null
                            ? new Intl.NumberFormat("en-IN", {
                                style: "currency",
                                currency: "INR",
                                maximumFractionDigits: 2,
                              }).format(Number(row.balance_inr))
                            : "No plan rate"}
                        </span>
                        {row.in_deficit ? (
                          <span className="ml-2 align-middle">
                            <Badge variant="rating5">In deficit</Badge>
                          </span>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* Under md the table becomes one card per customer, so the page
                body never scrolls sideways. */}
            <ul className="space-y-3 md:hidden">
              {rows.map((row) => (
                <li key={row.tenant_id}>
                  <RowCard
                    title={row.customer_name}
                    meta={<StatusBadge status={row.subscription_status} />}
                  >
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span>Plan</span>
                      <span className="font-medium">
                        {row.plan_name ?? "No plan"}
                      </span>
                    </div>
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span>Renews</span>
                      <span className="font-medium">
                        {formatDate(row.current_end)}
                      </span>
                    </div>
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span>Credits</span>
                      <span className="font-medium">
                        {row.balance_credits} credits
                        {row.balance_inr !== null
                          ? ` / ${new Intl.NumberFormat("en-IN", {
                              style: "currency",
                              currency: "INR",
                              maximumFractionDigits: 2,
                            }).format(Number(row.balance_inr))}`
                          : " / no plan rate"}
                        {row.in_deficit ? " (in deficit)" : ""}
                      </span>
                    </div>
                  </RowCard>
                </li>
              ))}
            </ul>
          </Section>
        </div>
      )}
    </>
  );
}
