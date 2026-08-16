"use client";

// Provider Portal, the customer list (spec §2).
//
// This is the ReadyPick owner's home screen: every customer, what they are
// doing on the platform, and the two management actions.
//
// TWO DELIBERATE CHOICES WORTH KNOWING BEFORE EDITING:
//
// 1. Search, the archived filter and pagination all run SERVER-SIDE. Filtering
//    a single fetched page in the browser would make "3 of 108 customers
//    match" depend on which page happened to be loaded, the count would be a
//    lie the moment the customer list outgrows one page.
//
// 2. There is no Delete button. Archive is a reversible hide that touches no
//    job, application or report; the irreversible delete still exists on the
//    API and still demands the company name be retyped, but it does not belong
//    one click away from Edit (spec §8).

import * as React from "react";
import {
  Archive,
  ArchiveRestore,
  Building2,
  Pencil,
  Plus,
  Search,
} from "lucide-react";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import type {
  Customer,
  CustomerDetail,
  CustomerListResponse,
} from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  RowCard,
} from "@/components/page-primitives";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  CustomerEditModal,
  INDUSTRIES,
  type CustomerEditValues,
} from "@/components/customer-edit-modal";
import { CustomerDetailPanel } from "@/components/customer-detail-panel";
import { ExportXlsxButton } from "@/components/export-xlsx-button";

const PAGE_SIZE = 25;
const SEARCH_DEBOUNCE_MS = 300;

type OnboardForm = {
  name: string;
  client_email: string;
  industry: string;
  culture: string;
  details: string;
};

const EMPTY_FORM: OnboardForm = {
  name: "",
  client_email: "",
  industry: "",
  culture: "",
  details: "",
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const wordCount = (value: string) =>
  value.trim() ? value.trim().split(/\s+/).length : 0;

function validateOnboard(
  form: OnboardForm
): Partial<Record<keyof OnboardForm, string>> {
  const errors: Partial<Record<keyof OnboardForm, string>> = {};
  if (!form.name.trim()) errors.name = "Company name is required.";
  if (!EMAIL_RE.test(form.client_email.trim())) {
    errors.client_email = "Enter a valid owner email.";
  }
  if (!form.industry) errors.industry = "Choose an industry.";
  const words = wordCount(form.culture);
  if (words < 100 || words > 500) {
    errors.culture = "Company culture must be between 100 and 500 words.";
  }
  return errors;
}

function FieldError({ message }: { message?: string }) {
  return message ? (
    <p className="mt-1 text-xs font-medium text-destructive" role="alert">
      {message}
    </p>
  ) : null;
}

export default function CustomersPage() {
  const { toast } = useToast();

  const [customers, setCustomers] = React.useState<Customer[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const [query, setQuery] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [showArchived, setShowArchived] = React.useState(false);
  const [page, setPage] = React.useState(1);

  const [createOpen, setCreateOpen] = React.useState(false);
  const [createForm, setCreateForm] = React.useState<OnboardForm>(EMPTY_FORM);
  const [createErrors, setCreateErrors] = React.useState<
    Partial<Record<keyof OnboardForm, string>>
  >({});
  const [creating, setCreating] = React.useState(false);

  const [detailId, setDetailId] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<CustomerDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [editing, setEditing] = React.useState<Customer | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [archivingId, setArchivingId] = React.useState<string | null>(null);

  // Debounce the typed query so a search does not fire a request per keystroke.
  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  // Any change to what is being asked for returns to page 1, staying on
  // page 4 of a narrower result set shows an empty table.
  React.useEffect(() => {
    setPage(1);
  }, [search, showArchived]);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const params = new URLSearchParams({
      status: showArchived ? "archived" : "active",
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    if (search) params.set("search", search);
    try {
      const res = await apiGet<CustomerListResponse>(
        `/provider/customers?${params.toString()}`
      );
      setCustomers(res.customers);
      setTotal(res.total);
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "Could not load customers."
      );
    } finally {
      setLoading(false);
    }
  }, [page, search, showArchived]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const openDetail = React.useCallback(async (customerId: string) => {
    setDetailId(customerId);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(
        await apiGet<CustomerDetail>(`/provider/customers/${customerId}`)
      );
    } catch (error) {
      toast({
        title: "Could not load customer",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
      setDetailId(null);
    } finally {
      setDetailLoading(false);
    }
  }, [toast]);

  const onboard = async () => {
    const errors = validateOnboard(createForm);
    setCreateErrors(errors);
    if (Object.keys(errors).length) return;
    setCreating(true);
    try {
      await apiPost("/admin/tenants", {
        name: createForm.name.trim(),
        client_email: createForm.client_email.trim(),
        industry: createForm.industry,
        culture: createForm.culture.trim(),
        details: createForm.details.trim() || null,
      });
      setCreateOpen(false);
      setCreateForm(EMPTY_FORM);
      setCreateErrors({});
      toast({
        title: "Customer onboarded",
        description:
          "The HR Head can create their account with the invited email using email/password or Google.",
      });
      // Refetch rather than splice the new row in: the list is a server-side
      // filtered, sorted page, and a locally inserted row would sit in the
      // wrong place, or in a filter it does not match.
      await load();
    } catch (error) {
      toast({
        title: "Could not onboard customer",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setCreating(false);
    }
  };

  const applyUpdated = (updated: CustomerDetail) => {
    setCustomers((current) =>
      current.map((customer) =>
        customer.id === updated.id ? { ...customer, ...updated } : customer
      )
    );
    if (detailId === updated.id) setDetail(updated);
  };

  const saveEdit = async (values: CustomerEditValues) => {
    if (!editing) return;
    setSaving(true);
    try {
      const updated = await apiPatch<CustomerDetail>(
        `/provider/customers/${editing.id}`,
        {
          industry: values.industry || null,
          website_domain: values.website_domain.trim(),
          notes: values.notes.trim(),
        }
      );
      applyUpdated(updated);
      setEditing(null);
      toast({ title: "Customer updated", description: updated.name });
    } catch (error) {
      toast({
        title: "Could not update customer",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const toggleArchive = async (customer: Customer) => {
    const archiving = customer.status !== "archived";
    setArchivingId(customer.id);
    try {
      const updated = await apiPatch<CustomerDetail>(
        `/provider/customers/${customer.id}/archive?archived=${archiving}`
      );
      toast({
        title: archiving ? "Customer archived" : "Customer restored",
        description: archiving
          ? `${updated.name} is hidden from the active list. Nothing was deleted.`
          : `${updated.name} is active again.`,
      });
      if (detailId === customer.id) setDetail(updated);
      // The row no longer matches the current filter, so reload rather than
      // leaving it visible in a list it has dropped out of.
      await load();
    } catch (error) {
      toast({
        title: archiving
          ? "Could not archive customer"
          : "Could not restore customer",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setArchivingId(null);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Provider Portal"
        title="Customers"
        description="Every onboarded company, what they are running on the platform, and their compliance records."
        actions={
          <div className="flex flex-wrap gap-2">
            <ExportXlsxButton
              fileName="readypick-provider-customers"
              rows={customers.map((customer) => ({
                customer: customer.name,
                industry: customer.industry ?? "",
                status: customer.status,
                jobs_posted: customer.analytics.jobs_posted,
                jobs_closed: customer.analytics.jobs_closed,
                jobs_ongoing: customer.analytics.jobs_ongoing,
                candidates_interacted: customer.analytics.total_candidates_interacted,
              }))}
            />
            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus className="h-4 w-4" aria-hidden="true" /> Onboard customer
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Onboard a new customer</DialogTitle>
                <DialogDescription>
                  Create the company profile and reserve its HR Head account.
                  Credentials are handled by Firebase through email/password or
                  Google sign-in.
                </DialogDescription>
              </DialogHeader>
              <OnboardFields
                form={createForm}
                errors={createErrors}
                disabled={creating}
                onChange={(next) => {
                  setCreateForm(next);
                  setCreateErrors(validateOnboard(next));
                }}
              />
              <DialogFooter>
                <Button
                  variant="outline"
                  disabled={creating}
                  onClick={() => setCreateOpen(false)}
                >
                  Cancel
                </Button>
                <Button disabled={creating} onClick={() => void onboard()}>
                  {creating ? "Creating" : "Create customer"}
                </Button>
              </DialogFooter>
            </DialogContent>
            </Dialog>
          </div>
        }
      />

      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-sm">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 opacity-70"
            aria-hidden="true"
          />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="pl-10"
            placeholder="Search company, industry, or owner"
            aria-label="Search customers"
          />
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <label
            htmlFor="show-archived"
            className="flex cursor-pointer items-center gap-2 text-sm font-medium"
          >
            <Checkbox
              id="show-archived"
              checked={showArchived}
              onCheckedChange={(checked) => setShowArchived(checked === true)}
            />
            Show archived
          </label>
          <p className="text-sm font-medium [font-variant-numeric:tabular-nums]">
            {total} customer{total === 1 ? "" : "s"}
          </p>
        </div>
      </div>

      {loadError ? (
        <ErrorState
          title="Customers could not be loaded"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          }
        />
      ) : loading ? (
        <LoadingRows rows={6} label="Loading customers" />
      ) : customers.length === 0 ? (
        <EmptyState
          icon={Building2}
          title={showArchived ? "No archived customers" : "No customers found"}
          description={
            showArchived
              ? "Archived customers appear here. Nothing is deleted when a customer is archived."
              : "Onboard a company to see it here with its jobs and compliance records."
          }
        />
      ) : (
        <>
        {/* The analytics columns push the table past a narrow viewport on
            purpose; the scroll is confined to the table's own wrapper so the
            page body itself never scrolls sideways, and below md the rows are
            rendered as stacked cards instead. */}
        <div className="hidden md:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[180px]">Company Name</TableHead>
                <TableHead>Industry</TableHead>
                <TableHead className="min-w-[200px]">Primary Contact</TableHead>
                <TableHead>Team</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Jobs Posted</TableHead>
                <TableHead className="text-right">Jobs Closed</TableHead>
                <TableHead className="text-right">Jobs Ongoing</TableHead>
                <TableHead className="text-right">Candidates</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {customers.map((customer) => (
                  <TableRow key={customer.id}>
                    <TableCell>
                      <button
                        type="button"
                        className="flex items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        onClick={() => void openDetail(customer.id)}
                      >
                        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-100 text-accent-foreground">
                          <Building2 className="h-4 w-4" aria-hidden="true" />
                        </span>
                        <span className="min-w-0">
                          <span className="block font-semibold underline-offset-4 hover:underline">
                            {customer.name}
                          </span>
                          <span className="block truncate text-xs">
                            {customer.website_domain || customer.domain}
                          </span>
                        </span>
                      </button>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {customer.industry ?? "Not set"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <p className="text-sm">
                        {customer.primary_contact.name ?? "HR Head"}
                      </p>
                      <p className="text-xs">
                        {customer.primary_contact.email ?? "No email"}
                      </p>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {customer.team_size}{" "}
                      {customer.team_size === 1 ? "member" : "members"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {new Date(customer.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {customer.analytics.jobs_posted}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {customer.analytics.jobs_closed}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {customer.analytics.jobs_ongoing}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {customer.analytics.total_candidates_interacted}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setEditing(customer)}
                        >
                          <Pencil className="h-3.5 w-3.5" aria-hidden="true" />{" "}
                          Edit
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={archivingId === customer.id}
                          onClick={() => void toggleArchive(customer)}
                        >
                          {customer.status === "archived" ? (
                            <>
                              <ArchiveRestore
                                className="h-3.5 w-3.5"
                                aria-hidden="true"
                              />{" "}
                              Unarchive
                            </>
                          ) : (
                            <>
                              <Archive
                                className="h-3.5 w-3.5"
                                aria-hidden="true"
                              />{" "}
                              Archive
                            </>
                          )}
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        {/* Below md the same rows, stacked. */}
        <ul className="space-y-3 md:hidden">
          {customers.map((customer) => (
            <li key={customer.id}>
              <RowCard
                title={
                  <button
                    type="button"
                    className="underline-offset-4 hover:underline"
                    onClick={() => void openDetail(customer.id)}
                  >
                    {customer.name}
                  </button>
                }
                meta={
                  <>
                    {customer.industry ?? "Industry not set"},{" "}
                    {customer.team_size}{" "}
                    {customer.team_size === 1 ? "member" : "members"}
                  </>
                }
                actions={
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEditing(customer)}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
                  </Button>
                }
              >
                <p className="text-xs">
                  {customer.primary_contact.name ?? "HR Head"},{" "}
                  {customer.primary_contact.email ?? "no email"}
                </p>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <MobileStat
                    label="Jobs posted"
                    value={customer.analytics.jobs_posted}
                  />
                  <MobileStat
                    label="Jobs closed"
                    value={customer.analytics.jobs_closed}
                  />
                  <MobileStat
                    label="Jobs ongoing"
                    value={customer.analytics.jobs_ongoing}
                  />
                  <MobileStat
                    label="Candidates"
                    value={customer.analytics.total_candidates_interacted}
                  />
                </dl>
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  disabled={archivingId === customer.id}
                  onClick={() => void toggleArchive(customer)}
                >
                  {customer.status === "archived" ? "Unarchive" : "Archive"}
                </Button>
              </RowCard>
            </li>
          ))}
        </ul>
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
          <span className="text-sm font-medium [font-variant-numeric:tabular-nums]">
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

      {detailId ? (
        <CustomerDetailPanel
          customer={detail}
          loading={detailLoading}
          archiving={archivingId === detailId}
          onClose={() => {
            setDetailId(null);
            setDetail(null);
          }}
          onEdit={() => {
            if (detail) setEditing(detail);
          }}
          onToggleArchive={() => {
            if (detail) void toggleArchive(detail);
          }}
        />
      ) : null}

      {editing ? (
        <CustomerEditModal
          customer={editing}
          saving={saving}
          onCancel={() => setEditing(null)}
          onSave={(values) => void saveEdit(values)}
        />
      ) : null}
    </div>
  );
}

/**
 * One analytics figure in the stacked mobile card. `jobs_closed` and
 * `jobs_ongoing` OVERLAP by design (a job in its grace tail is both), so they
 * are listed as four independent figures and never as parts of a whole.
 */
function MobileStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="opacity-80">{label}</dt>
      <dd className="font-semibold [font-variant-numeric:tabular-nums]">
        {value}
      </dd>
    </div>
  );
}

function OnboardFields({
  form,
  errors,
  disabled,
  onChange,
}: {
  form: OnboardForm;
  errors: Partial<Record<keyof OnboardForm, string>>;
  disabled?: boolean;
  onChange: (form: OnboardForm) => void;
}) {
  return (
    <div className="space-y-4">
      <FormField label="Company name" htmlFor="customer-name" required>
        <Input
          id="customer-name"
          value={form.name}
          disabled={disabled}
          aria-invalid={Boolean(errors.name)}
          onChange={(event) => onChange({ ...form, name: event.target.value })}
        />
        <FieldError message={errors.name} />
      </FormField>
      <FormField
        label="HR Head email"
        htmlFor="customer-owner-email"
        required
        hint="They can create their account with email/password or Google using this address."
      >
        <Input
          id="customer-owner-email"
          type="email"
          value={form.client_email}
          disabled={disabled}
          aria-invalid={Boolean(errors.client_email)}
          onChange={(event) =>
            onChange({ ...form, client_email: event.target.value })
          }
        />
        <FieldError message={errors.client_email} />
      </FormField>
      <FormField label="Industry" required>
        <Select
          value={form.industry}
          disabled={disabled}
          onValueChange={(industry) => onChange({ ...form, industry })}
        >
          <SelectTrigger aria-invalid={Boolean(errors.industry)}>
            <SelectValue placeholder="Select an industry" />
          </SelectTrigger>
          <SelectContent>
            {INDUSTRIES.map((industry) => (
              <SelectItem key={industry} value={industry}>
                {industry}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <FieldError message={errors.industry} />
      </FormField>
      <FormField
        label="Company culture"
        htmlFor="customer-culture"
        required
        hint={`${wordCount(form.culture)} words, 100 to 500 required`}
      >
        <Textarea
          id="customer-culture"
          rows={7}
          value={form.culture}
          disabled={disabled}
          aria-invalid={Boolean(errors.culture)}
          placeholder="Describe how the team works, communicates, makes decisions, and supports people."
          onChange={(event) => onChange({ ...form, culture: event.target.value })}
        />
        <FieldError message={errors.culture} />
      </FormField>
      <FormField
        label="Company details"
        htmlFor="customer-details"
        hint="Size, headquarters, founding year, mission, markets, and anything else candidates should know."
      >
        <Textarea
          id="customer-details"
          rows={5}
          value={form.details}
          disabled={disabled}
          onChange={(event) => onChange({ ...form, details: event.target.value })}
        />
      </FormField>
    </div>
  );
}
