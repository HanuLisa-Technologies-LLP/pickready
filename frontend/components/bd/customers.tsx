"use client";

// The BD Customers database: every lead whose agreement was signed.
//
// Search and pagination run SERVER-SIDE (`GET /bd/customers`), for the same
// reason every other list in this platform does: a browser-side filter over one
// fetched page makes the match count depend on which page was loaded.
//
// The CSV export deliberately does NOT go through the JSON helpers in
// `lib/api`. That endpoint streams a file with a Content-Disposition filename
// the server chose, and `api()` would parse the body as JSON and throw the
// filename away. It is fetched as a blob instead, with the same cookies and the
// same one-shot refresh-and-retry on a 401.

import * as React from "react";
import { Building2, Download, Search } from "lucide-react";

import { API_BASE, apiGet, tryRefresh } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import {
  SOCIAL_SOURCE_LABELS,
  type BDCustomer,
  type BDCustomerListResponse,
} from "@/lib/bd-types";
import { PageHeader } from "@/components/app-shell";
import { useToast } from "@/components/ui/toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FadeIn } from "@/components/motion";
import { ExportXlsxButton } from "@/components/export-xlsx-button";

const PAGE_SIZE = 25;
const SEARCH_DEBOUNCE_MS = 300;
const FALLBACK_FILENAME = "pickready-customers.csv";

/**
 * Pull the server's filename out of a Content-Disposition header. The export is
 * named with the date it was taken, and keeping that name is the difference
 * between a Downloads folder of dated exports and six files called
 * "export(3).csv".
 */
export function filenameFromDisposition(header: string | null): string {
  if (!header) return FALLBACK_FILENAME;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
      /* fall through to the plain form */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : FALLBACK_FILENAME;
}

export function BDCustomersPage() {
  const { toast } = useToast();

  const [customers, setCustomers] = React.useState<BDCustomer[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [query, setQuery] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [downloading, setDownloading] = React.useState(false);

  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  React.useEffect(() => {
    setPage(1);
  }, [search]);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    if (search) params.set("search", search);
    try {
      const res = await apiGet<BDCustomerListResponse>(
        `/bd/customers?${params.toString()}`
      );
      setCustomers(res.customers);
      setTotal(res.total);
    } catch (error) {
      setLoadError(apiErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const download = async () => {
    setDownloading(true);
    // The export honours the CURRENT search, so what is downloaded is what is
    // on screen rather than the whole database.
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const url = `${API_BASE}/bd/customers/export.csv${suffix}`;
    try {
      let res = await fetch(url, { credentials: "include" });
      if (res.status === 401 && (await tryRefresh())) {
        res = await fetch(url, { credentials: "include" });
      }
      if (!res.ok) {
        throw new Error(
          res.status === 403
            ? "You do not have permission to export customers."
            : "The export could not be generated."
        );
      }
      const blob = await res.blob();
      const filename = filenameFromDisposition(
        res.headers.get("Content-Disposition")
      );
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
      toast({ title: "Export downloaded", description: filename });
    } catch (error) {
      toast({
        title: "Could not download the export",
        description:
          error instanceof Error ? error.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Customers"
        description="Every company whose agreement was signed, with their primary contact."
        actions={
          <div className="flex flex-wrap gap-2">
            <ExportXlsxButton
              fileName="pickready-bd-customers"
              rows={customers.map((customer) => ({
                company: customer.company_name,
                industry: customer.industry ?? "",
                location: customer.location ?? "",
                contact: customer.contact_name ?? "",
                email: customer.contact_email ?? "",
                phone: customer.contact_phone ?? "",
                website: customer.website ?? "",
                source: customer.social_source ?? customer.channel,
                agreement_date: customer.agreement_at ?? "",
              }))}
            />
            <Button
              variant="outline"
              className="gap-2"
              disabled={downloading || total === 0}
              onClick={() => void download()}
            >
              <Download className="h-4 w-4" />
              {downloading ? "Preparing…" : "Download CSV"}
            </Button>
          </div>
        }
      />

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-sm">
          <Search
            className="pointer-events-none absolute left-3 top-3 h-4 w-4"
            aria-hidden="true"
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="pl-9"
            placeholder="Search company, industry, location or contact"
            aria-label="Search customers"
          />
        </div>
        <p className="text-sm tabular-nums">
          {total} customer{total === 1 ? "" : "s"}
        </p>
      </div>

      {loadError ? (
        <div className="rounded-md border p-4" role="alert">
          <p className="text-sm font-medium">Customers could not be loaded.</p>
          <p className="mt-1 text-sm">{loadError}</p>
          <Button className="mt-3" size="sm" onClick={() => void load()}>
            Try again
          </Button>
        </div>
      ) : loading ? (
        <div className="space-y-2 rounded-md border p-4" aria-busy="true">
          <span className="sr-only">Loading customers</span>
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-12 w-full" />
          ))}
        </div>
      ) : customers.length === 0 ? (
        <div className="rounded-md border p-10 text-center">
          <p className="text-sm font-medium">
            {search
              ? "No customers match this search."
              : "No customers yet. A lead becomes a customer when its agreement is marked as signed."}
          </p>
        </div>
      ) : (
        <>
          <div className="hidden overflow-x-auto rounded-md border md:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="min-w-[200px]">Company</TableHead>
                  <TableHead>Location</TableHead>
                  <TableHead>Industry</TableHead>
                  <TableHead className="min-w-[220px]">Primary contact</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {customers.map((customer) => (
                  <TableRow key={customer.lead_id}>
                    <TableCell>
                      <span className="flex items-center gap-2">
                        <Building2 className="h-4 w-4 shrink-0" aria-hidden="true" />
                        <span>
                          <span className="block font-medium">
                            {customer.company_name}
                          </span>
                          {customer.website ? (
                            <span className="block text-xs">
                              {customer.website}
                            </span>
                          ) : null}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>{customer.location || "Not set"}</TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {customer.industry || "Not set"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="block text-sm">
                        {customer.contact_name || "No contact name"}
                      </span>
                      <span className="block text-xs">
                        {customer.contact_email || "No email"}
                      </span>
                      <span className="block text-xs">
                        {customer.contact_phone || "No phone"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="brand">
                        {customer.social_source
                          ? SOCIAL_SOURCE_LABELS[customer.social_source]
                          : "Approached directly"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <FadeIn className="space-y-3 md:hidden">
            {customers.map((customer) => (
              <div key={customer.lead_id} className="rounded-md border p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-medium">{customer.company_name}</p>
                    <p className="text-xs">
                      {[customer.industry, customer.location]
                        .filter(Boolean)
                        .join(", ") || customer.website || "No details yet"}
                    </p>
                  </div>
                  <Badge variant="brand">
                    {customer.social_source
                      ? SOCIAL_SOURCE_LABELS[customer.social_source]
                      : "Personal"}
                  </Badge>
                </div>
                <div className="mt-3 space-y-0.5 text-sm">
                  <p>{customer.contact_name || "No contact name"}</p>
                  <p className="text-xs">{customer.contact_email || "No email"}</p>
                  <p className="text-xs">{customer.contact_phone || "No phone"}</p>
                </div>
              </div>
            ))}
          </FadeIn>
        </>
      )}

      {pageCount > 1 ? (
        <nav
          className="mt-4 flex items-center justify-end gap-2"
          aria-label="Customer pages"
        >
          <Button
            variant="outline"
            size="sm"
            disabled={page === 1}
            onClick={() => setPage((current) => current - 1)}
          >
            Previous
          </Button>
          <span className="text-sm tabular-nums">
            Page {page} of {pageCount}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page === pageCount}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
          </Button>
        </nav>
      ) : null}
    </div>
  );
}
