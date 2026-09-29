"use client";

// Provider Portal → Billing (killer-spec §4.1, per-credit only since 2026-09-29).
//
// What each customer's credit balance is. READ-ONLY, like every other Provider
// view of a customer's own data, and read-only by ABSENCE: there is no route in
// api/billing that lets the Provider write a purchase or a credit, so there is
// nothing to gate here with a flag. There is no plan or renewal column: the
// product sells one-time credit packs and nothing recurs.
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

function formatInr(value: string): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(Number(value));
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
        description="Credit balances across your customers."
        actions={
          rows.length ? (
            <ExportXlsxButton
              fileName="readypick-provider-billing"
              rows={rows.map((row) => ({
                customer: row.customer_name,
                credits: row.balance_credits,
                amount_inr: row.balance_inr,
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
          description="Credit balances appear here once a customer is onboarded."
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
                  they buy more credits.
                </p>
              </div>
            </div>
          ) : null}

          <Section
            title="Customers"
            description="Balances are shown in credits, and in rupees at the list price per credit, excluding GST."
          >
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Customer</TableHead>
                    <TableHead className="text-right">Credits / INR value</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.tenant_id}>
                      <TableCell className="font-medium">
                        {row.customer_name}
                      </TableCell>
                      <TableCell className="text-right font-medium">
                        <span className="block">{row.balance_credits} credits</span>
                        <span className="mt-0.5 block text-xs font-normal">
                          {formatInr(row.balance_inr)}
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
                    meta={
                      row.in_deficit ? (
                        <Badge variant="rating5">In deficit</Badge>
                      ) : null
                    }
                  >
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span>Credits</span>
                      <span className="font-medium">
                        {row.balance_credits} credits /{" "}
                        {formatInr(row.balance_inr)}
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
