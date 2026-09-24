"use client";

// The credit statement: every movement on the account's credit pool, newest
// first, one page at a time, from GET /billing/ledger.
//
// WHY IT EXISTS. The billing promise since 2026-07-28 is that a customer
// disputing usage gets a STATEMENT, not a number: the balance is the sum of
// the ledger, so the ledger is the explanation. The route was built and
// paginated from day one and no screen ever called it, so the page showed
// the latest 25 rows the overview happens to carry and nothing older. A
// customer asking "where did last quarter's credits go" had no answer in the
// product.
//
// WHAT IT SHOWS AND WHAT IT DOES NOT. When, what happened, and the credit
// movement, which are operational billing figures the customer paid for. It
// never renders the application a consumption row refers to: the ledger row
// carries a link id for reconciliation, and a statement line naming a
// candidate would put candidate data on a billing page read by every staff
// role that holds `view_billing`.

import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { apiGet } from "@/lib/api";
import type { CreditEventType, CreditLedgerEntry } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Human labels for ledger event types. A raw enum never reaches the page.
 *  The one list: the billing page's usage cards read it too. */
export const CREDIT_EVENT_LABELS: Record<CreditEventType, string> = {
  grant: "Credits added",
  completed_assessment: "Assessment completed",
  incomplete_assessment: "Assessment started, not finished",
  no_show: "Invitation never opened",
  old_profile_review: "Earlier applicant reviewed",
  adjustment: "Adjustment",
  expiry: "Credits expired",
};

/** Rows per page. The route caps a page at 100; 25 matches every other list. */
export const STATEMENT_PAGE_SIZE = 25;

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function signed(entry: CreditLedgerEntry): string {
  return `${entry.subunits_delta > 0 ? "+" : ""}${entry.credits_delta}`;
}

function label(entry: CreditLedgerEntry): string {
  return CREDIT_EVENT_LABELS[entry.event_type] ?? "Other credit movement";
}

type Page = { rows: CreditLedgerEntry[]; hasOlder: boolean };

/**
 * `refreshToken` changes whenever the page's overview says the ledger moved
 * (a purchase, a verified checkout), which sends the statement back to its
 * newest page rather than leaving a stale one on screen.
 */
export function CreditStatement({ refreshToken }: { refreshToken: string }) {
  // The page index belongs to ONE refresh token: a new token reads as page one
  // in the same render, so a refresh never fetches the old page first.
  const [cursor, setCursor] = React.useState({ token: refreshToken, index: 0 });
  const pageIndex = cursor.token === refreshToken ? cursor.index : 0;
  const [page, setPage] = React.useState<Page | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  // Only the newest request may write: a slow page answering after a quicker
  // one must not replace what the reader asked for last.
  const latest = React.useRef(0);

  const load = React.useCallback(async (index: number) => {
    const request = ++latest.current;
    setLoading(true);
    setError(null);
    try {
      // One row more than a page: its presence is how the statement knows an
      // older page exists, without a second count query on the ledger.
      const rows = await apiGet<CreditLedgerEntry[]>(
        `/billing/ledger?skip=${index * STATEMENT_PAGE_SIZE}&limit=${
          STATEMENT_PAGE_SIZE + 1
        }`
      );
      if (request !== latest.current) return;
      setPage({
        rows: rows.slice(0, STATEMENT_PAGE_SIZE),
        hasOlder: rows.length > STATEMENT_PAGE_SIZE,
      });
    } catch (caught) {
      if (request !== latest.current) return;
      setPage(null);
      setError(
        caught instanceof Error ? caught.message : "Could not load the statement."
      );
    } finally {
      if (request === latest.current) setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load(pageIndex);
  }, [load, pageIndex, refreshToken]);

  const goTo = (index: number) =>
    setCursor({ token: refreshToken, index: Math.max(0, index) });

  if (loading && page === null) {
    return <LoadingRows rows={4} label="Loading the credit statement" />;
  }
  if (error) {
    return (
      <ErrorState
        title="Could not load the credit statement"
        description={error}
        action={
          <Button variant="outline" onClick={() => void load(pageIndex)}>
            Retry
          </Button>
        }
      />
    );
  }
  if (!page || (page.rows.length === 0 && pageIndex === 0)) {
    return (
      <p className="leading-7">
        Nothing yet. Credit movements appear here as soon as credits are added
        or your first assessment invitation goes out.
      </p>
    );
  }

  return (
    <div aria-busy={loading}>
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>What happened</TableHead>
              <TableHead className="text-right">Credits</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.rows.map((entry) => (
              <TableRow key={entry.id}>
                <TableCell>{formatDate(entry.created_at)}</TableCell>
                <TableCell>{label(entry)}</TableCell>
                <TableCell className="text-right font-medium tabular-nums">
                  {signed(entry)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="space-y-3 md:hidden">
        {page.rows.map((entry) => (
          <li
            key={entry.id}
            className="flex items-start justify-between gap-3 rounded-xl border border-border bg-surface p-4"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium">{label(entry)}</p>
              <p className="mt-1 text-xs">{formatDate(entry.created_at)}</p>
            </div>
            <span className="shrink-0 text-sm font-semibold tabular-nums">
              {signed(entry)}
            </span>
          </li>
        ))}
      </ul>
      {pageIndex > 0 || page.hasOlder ? (
        <nav
          aria-label="Credit statement pages"
          className="mt-4 flex items-center justify-between gap-3"
        >
          <Button
            variant="outline"
            size="sm"
            disabled={pageIndex === 0 || loading}
            onClick={() => goTo(pageIndex - 1)}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            Newer
          </Button>
          <span className="text-sm">Page {pageIndex + 1}</span>
          <Button
            variant="outline"
            size="sm"
            disabled={!page.hasOlder || loading}
            onClick={() => goTo(pageIndex + 1)}
          >
            Older
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </nav>
      ) : null}
    </div>
  );
}
