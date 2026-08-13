"use client";

// BD Reach.
//
// ONE component, ONE page since 2026-08-09. Personal Reach and Social Reach
// were always the same funnel over the same `bd_leads` table: same company
// fields, same primary contact, same six progress checkboxes, same agreement
// decision. The only difference was that a social lead carries the platform it
// came from, which is a COLUMN, not a category, so it is now a column on one
// table and a filter above it.
//
// `bd_leads.channel` is untouched and still discriminates the rows; what is
// gone is the idea that a rep chooses which of two screens a company belongs
// on. The list request simply omits `channel=`, which the API has always
// treated as "both".
//
// THREE THINGS WORTH KNOWING BEFORE EDITING:
//
// 1. SEARCH, THE AGREEMENT FILTER, THE ARCHIVED FILTER AND PAGINATION ALL RUN
//    SERVER-SIDE. Filtering a fetched page in the browser would make "4 of 108
//    leads match" depend on which page happened to be loaded. The Provider
//    Portal's customer list works this way for the same reason.
//
// 2. TICKING A BOX IS OPTIMISTIC AND ROLLS BACK. The row flips immediately, the
//    sparse `PATCH /bd/leads/{id}/progress` follows, and a failure restores the
//    previous row and says so. A checkbox that silently does nothing is worse
//    than one that errors.
//
// 3. AGREEMENT IS NOT JUST A FLAG. Setting it to yes CREATES a customer (a
//    `tenants` row in the `prospect` status); taking the yes away ARCHIVES that
//    customer rather than deleting it. Both consequences are spelled out in a
//    confirmation before the request is sent.

import * as React from "react";
import { Archive, Pencil, Plus, Search } from "lucide-react";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import {
  PROGRESS_FLAGS,
  PROGRESS_SHORT_LABELS,
  SOCIAL_SOURCES,
  SOCIAL_SOURCE_LABELS,
  type BDLead,
  type BDSocialSource,
  type BDLeadFormValues,
  type BDLeadListResponse,
} from "@/lib/bd-types";
import { PageHeader } from "@/components/app-shell";
import { ExportXlsxButton } from "@/components/export-xlsx-button";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import { FadeIn } from "@/components/motion";
import {
  AgreementCell,
  ProgressCheckbox,
  SourceBadge,
  type AgreementChoice,
} from "@/components/bd/lead-cells";
import {
  LeadFormModal,
  channelForSource,
} from "@/components/bd/lead-form-modal";

const PAGE_SIZE = 25;
const SEARCH_DEBOUNCE_MS = 300;

type AgreementFilter = "all" | "signed" | "declined" | "undecided";

const AGREEMENT_FILTER_LABELS: Record<AgreementFilter, string> = {
  all: "All agreements",
  signed: "Signed",
  declined: "Declined",
  undecided: "Not decided",
};

/** What a pending agreement change will do, in the rep's own terms. */
function agreementConsequence(
  lead: BDLead,
  choice: AgreementChoice
): { title: string; body: string; action: string } | null {
  if (choice === true) {
    return {
      title: `Mark the agreement with ${lead.company_name} as signed?`,
      body:
        `This creates a customer record for ${lead.company_name} and adds it to ` +
        "the Customers page. If this company was signed before, its original " +
        "customer record is reused rather than a second one being created.",
      action: "Yes, it is signed",
    };
  }
  if (lead.agreement === true) {
    return {
      title:
        choice === false
          ? `Mark the agreement with ${lead.company_name} as declined?`
          : `Set ${lead.company_name} back to not decided?`,
      body:
        `${lead.company_name} is currently a customer. This archives that ` +
        "customer record, it is not deleted, and nothing they have on the " +
        "platform is removed. Signing again later reuses the same company.",
      action: choice === false ? "Yes, they declined" : "Set to not decided",
    };
  }
  // Declining a lead nobody had signed yet has no side effect worth a dialog.
  return null;
}

/** The source filter, above the table. `all` sends no channel at all. */
type SourceFilter = "all" | "direct" | BDSocialSource;

const SOURCE_FILTER_LABELS: Record<string, string> = {
  all: "All sources",
  direct: "Approached directly",
  ...SOCIAL_SOURCE_LABELS,
};

export function ReachPage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  const { toast } = useToast();

  const [leads, setLeads] = React.useState<BDLead[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const [query, setQuery] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [agreementFilter, setAgreementFilter] =
    React.useState<AgreementFilter>("all");
  const [showArchived, setShowArchived] = React.useState(false);
  const [sourceFilter, setSourceFilter] = React.useState<SourceFilter>("all");
  const [page, setPage] = React.useState(1);

  const [creating, setCreating] = React.useState(false);
  const [editing, setEditing] = React.useState<BDLead | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [pendingAgreement, setPendingAgreement] = React.useState<{
    lead: BDLead;
    choice: AgreementChoice;
  } | null>(null);
  const [pendingArchive, setPendingArchive] = React.useState<BDLead | null>(null);

  // Debounce typing so a search does not fire one request per keystroke.
  React.useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  // Any change to what is being asked for returns to page 1. Staying on page 4
  // of a narrower result set just shows an empty table.
  React.useEffect(() => {
    setPage(1);
  }, [search, agreementFilter, showArchived, sourceFilter]);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    // Omitting `channel` is how the API has always meant "both". "Approached
    // directly" is the personal channel; a named platform is the social one,
    // narrowed further below.
    if (sourceFilter === "direct") params.set("channel", "personal");
    else if (sourceFilter !== "all") {
      params.set("channel", "social");
      params.set("social_source", sourceFilter);
    }
    if (search) params.set("search", search);
    if (showArchived) params.set("include_archived", "true");
    // `agreement=true|false` filters on a decision. An EMPTY value is the
    // "nobody has decided yet" filter, and an absent key means no filter at
    // all: the server tells them apart by reading the raw query string.
    if (agreementFilter === "signed") params.set("agreement", "true");
    if (agreementFilter === "declined") params.set("agreement", "false");
    if (agreementFilter === "undecided") params.set("agreement", "");
    try {
      const res = await apiGet<BDLeadListResponse>(
        `/bd/leads?${params.toString()}`
      );
      setLeads(res.leads);
      setTotal(res.total);
    } catch (error) {
      setLoadError(apiErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [sourceFilter, page, search, showArchived, agreementFilter]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const replaceLead = React.useCallback((updated: BDLead) => {
    setLeads((current) =>
      current.map((lead) => (lead.id === updated.id ? updated : lead))
    );
  }, []);

  // ---- Progress: optimistic, with a rollback ----
  const toggleProgress = async (
    lead: BDLead,
    flag: string,
    next: boolean
  ) => {
    const previous = lead;
    setLeads((current) =>
      current.map((row) =>
        row.id !== lead.id
          ? row
          : {
              ...row,
              progress: row.progress.map((step) =>
                step.key === flag
                  ? // The server stamps `at` the first time; show today's date
                    // straight away so the tooltip is not blank until reload.
                    { ...step, done: next, at: step.at ?? new Date().toISOString() }
                  : step
              ),
            }
      )
    );
    try {
      const updated = await apiPatch<BDLead>(
        `/bd/leads/${lead.id}/progress`,
        { progress: { [flag]: next } }
      );
      replaceLead(updated);
    } catch (error) {
      replaceLead(previous);
      toast({
        title: "Could not save that step",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  // ---- Agreement ----
  const chooseAgreement = (lead: BDLead, choice: AgreementChoice) => {
    if (lead.agreement === choice) return;
    if (agreementConsequence(lead, choice)) {
      setPendingAgreement({ lead, choice });
      return;
    }
    void commitAgreement(lead, choice);
  };

  const commitAgreement = async (lead: BDLead, choice: AgreementChoice) => {
    setBusyId(lead.id);
    try {
      const updated = await apiPatch<BDLead>(`/bd/leads/${lead.id}/agreement`, {
        agreement: choice,
      });
      replaceLead(updated);
      toast({
        title:
          choice === true
            ? "Customer created"
            : choice === false
              ? "Marked as declined"
              : "Set back to not decided",
        description:
          choice === true
            ? `${updated.company_name} now appears on the Customers page.`
            : lead.agreement === true
              ? `The customer record for ${updated.company_name} was archived, not deleted.`
              : undefined,
      });
      // The row may no longer match the agreement filter, so reload rather than
      // leaving it visible in a list it has dropped out of.
      if (agreementFilter !== "all") await load();
    } catch (error) {
      toast({
        title: "Could not update the agreement",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
      setPendingAgreement(null);
    }
  };

  // ---- Create, edit, archive ----
  const buildBody = (values: BDLeadFormValues) => {
    const body: Record<string, unknown> = {
      company_name: values.company_name.trim(),
      website: values.website.trim(),
      industry: values.industry.trim(),
      location: values.location.trim(),
      contact_name: values.contact_name.trim(),
      contact_email: values.contact_email.trim(),
      contact_phone: values.contact_phone.trim(),
      notes: values.notes.trim(),
    };
    return body;
  };

  /** A social lead REQUIRES a source and a personal one FORBIDS it (a Postgres
   *  CHECK enforces both). Since the merge the rep picks the source and the
   *  channel follows from it, so the two are derived together here rather than
   *  being set in two places that could disagree. */
  const channelFields = (values: BDLeadFormValues) => {
    const channel = channelForSource(values.social_source);
    return {
      channel,
      social_source: channel === "social" ? values.social_source : null,
    };
  };

  const createLead = async (values: BDLeadFormValues) => {
    setSaving(true);
    try {
      await apiPost("/bd/leads", {
        ...channelFields(values),
        ...buildBody(values),
      });
      setCreating(false);
      toast({ title: "Lead added", description: values.company_name.trim() });
      // Refetch rather than splice: the list is a server-filtered, sorted page
      // and a locally inserted row would sit in the wrong place.
      await load();
    } catch (error) {
      toast({
        title: "Could not add the lead",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const saveLead = async (values: BDLeadFormValues) => {
    if (!editing) return;
    setSaving(true);
    try {
      const updated = await apiPatch<BDLead>(
        `/bd/leads/${editing.id}`,
        buildBody(values)
      );
      replaceLead(updated);
      setEditing(null);
      toast({ title: "Lead updated", description: updated.company_name });
    } catch (error) {
      toast({
        title: "Could not update the lead",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const archiveLead = async (lead: BDLead) => {
    setBusyId(lead.id);
    try {
      await apiDelete<BDLead>(`/bd/leads/${lead.id}`);
      toast({
        title: "Lead archived",
        description: `${lead.company_name} is hidden from this list. Nothing was deleted.`,
      });
      await load();
    } catch (error) {
      toast({
        title: "Could not archive the lead",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyId(null);
      setPendingArchive(null);
    }
  };

  const emptyMessage = showArchived
    ? "No archived leads."
    : search || agreementFilter !== "all" || sourceFilter !== "all"
      ? "No leads match this search."
      : "No leads yet. Add the first company the team is working.";

  return (
    <TooltipProvider delayDuration={200}>
      <div>
        <PageHeader
          title={title}
          description={description}
          actions={
            <div className="flex flex-wrap gap-2">
              <ExportXlsxButton
                fileName="pickready-bd-reach"
                rows={leads.map((lead) => ({
                  company: lead.company_name,
                  // The export used to be one file per screen, so the source
                  // was implicit in the filename. With one screen it has to be
                  // a column, or the sheet loses the distinction entirely.
                  source: lead.social_source
                    ? SOCIAL_SOURCE_LABELS[lead.social_source]
                    : "Approached directly",
                  industry: lead.industry ?? "",
                  location: lead.location ?? "",
                  contact: lead.contact_name ?? "",
                  contact_email: lead.contact_email ?? "",
                  contact_phone: lead.contact_phone ?? "",
                  agreement:
                    lead.agreement === true
                      ? "Signed"
                      : lead.agreement === false
                        ? "Declined"
                        : "Undecided",
                  progress_completed: lead.progress.filter((item) => item.done).length,
                  updated_at: lead.updated_at ?? lead.created_at,
                }))}
              />
              <Button className="gap-2" onClick={() => setCreating(true)}>
                <Plus className="h-4 w-4" /> Add lead
              </Button>
            </div>
          }
        />

        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="relative w-full lg:max-w-sm">
            <Search
              className="pointer-events-none absolute left-3 top-3 h-4 w-4"
              aria-hidden="true"
            />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="pl-9"
              placeholder="Search company, industry, location or contact"
              aria-label="Search leads"
            />
          </div>
          <div className="flex flex-wrap items-center gap-4">
            {/* What used to be the choice between two screens. It narrows in
                SQL, like every other filter here: narrowing a fetched page in
                the browser would make the result count depend on which page
                happened to be loaded. */}
            <Select
              value={sourceFilter}
              onValueChange={(value) => setSourceFilter(value as SourceFilter)}
            >
              <SelectTrigger className="w-[190px]" aria-label="Filter by source">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(["all", "direct", ...SOCIAL_SOURCES] as SourceFilter[]).map(
                  (value) => (
                    <SelectItem key={value} value={value}>
                      {SOURCE_FILTER_LABELS[value]}
                    </SelectItem>
                  )
                )}
              </SelectContent>
            </Select>
            <Select
              value={agreementFilter}
              onValueChange={(value) =>
                setAgreementFilter(value as AgreementFilter)
              }
            >
              <SelectTrigger className="w-[170px]" aria-label="Filter by agreement">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(
                  ["all", "signed", "declined", "undecided"] as AgreementFilter[]
                ).map((value) => (
                  <SelectItem key={value} value={value}>
                    {AGREEMENT_FILTER_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="h-4 w-4 accent-current"
                checked={showArchived}
                onChange={(event) => setShowArchived(event.target.checked)}
              />
              Show archived
            </label>
            <p className="text-sm tabular-nums">
              {total} lead{total === 1 ? "" : "s"}
            </p>
          </div>
        </div>

        {loadError ? (
          <div className="rounded-md border p-4" role="alert">
            <p className="text-sm font-medium">Leads could not be loaded.</p>
            <p className="mt-1 text-sm">{loadError}</p>
            <Button className="mt-3" size="sm" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        ) : loading ? (
          <LeadsSkeleton />
        ) : leads.length === 0 ? (
          <div className="rounded-md border p-10 text-center">
            <p className="text-sm font-medium">{emptyMessage}</p>
          </div>
        ) : (
          <>
            {/* Wide layout. The six progress columns push this past a narrow
                viewport on purpose, and the scroll is confined to this wrapper
                so the page body itself never scrolls sideways. */}
            <div className="hidden overflow-x-auto rounded-md border md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="min-w-[190px]">Company</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="min-w-[210px]">
                      Primary contact
                    </TableHead>
                    {PROGRESS_FLAGS.map((flag) => (
                      <TableHead key={flag} className="text-center">
                        {PROGRESS_SHORT_LABELS[flag]}
                      </TableHead>
                    ))}
                    <TableHead className="min-w-[180px]">Agreement</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {leads.map((lead) => (
                    <TableRow key={lead.id}>
                      <TableCell>
                        <span className="block font-medium">
                          {lead.company_name}
                        </span>
                        <span className="block text-xs">
                          {[lead.industry, lead.location]
                            .filter(Boolean)
                            .join(", ") ||
                            lead.website ||
                            "No details yet"}
                        </span>
                      </TableCell>
                      <TableCell>
                        <SourceBadge source={lead.social_source} />
                      </TableCell>
                      <TableCell>
                        <span className="block text-sm">
                          {lead.contact_name || "No contact name"}
                        </span>
                        <span className="block text-xs">
                          {lead.contact_email || "No email"}
                        </span>
                        <span className="block text-xs">
                          {lead.contact_phone || "No phone"}
                        </span>
                      </TableCell>
                      {lead.progress.map((step) => (
                        <TableCell key={step.key} className="text-center">
                          <ProgressCheckbox
                            step={step}
                            leadName={lead.company_name}
                            disabled={busyId === lead.id}
                            onToggle={(next) =>
                              void toggleProgress(lead, step.key, next)
                            }
                          />
                        </TableCell>
                      ))}
                      <TableCell>
                        <AgreementCell
                          agreement={lead.agreement}
                          agreementAt={lead.agreement_at}
                          disabled={busyId === lead.id}
                          leadName={lead.company_name}
                          onChoose={(choice) => chooseAgreement(lead, choice)}
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1"
                            onClick={() => setEditing(lead)}
                          >
                            <Pencil className="h-3.5 w-3.5" /> Edit
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="gap-1"
                            disabled={busyId === lead.id || Boolean(lead.archived_at)}
                            onClick={() => setPendingArchive(lead)}
                          >
                            <Archive className="h-3.5 w-3.5" /> Archive
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* Narrow layout: one card per lead, from 360px up. */}
            <FadeIn className="space-y-3 md:hidden">
              {leads.map((lead) => (
                <div key={lead.id} className="rounded-md border p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium">{lead.company_name}</p>
                      <p className="text-xs">
                        {[lead.industry, lead.location]
                          .filter(Boolean)
                          .join(", ") || lead.website || "No details yet"}
                      </p>
                    </div>
                    <SourceBadge source={lead.social_source} />
                  </div>

                  <div className="mt-3 space-y-0.5 text-sm">
                    <p>{lead.contact_name || "No contact name"}</p>
                    <p className="text-xs">{lead.contact_email || "No email"}</p>
                    <p className="text-xs">{lead.contact_phone || "No phone"}</p>
                  </div>

                  <div className="mt-4 space-y-2">
                    {lead.progress.map((step) => (
                      <ProgressCheckbox
                        key={step.key}
                        step={step}
                        leadName={lead.company_name}
                        showCaption
                        disabled={busyId === lead.id}
                        onToggle={(next) =>
                          void toggleProgress(lead, step.key, next)
                        }
                      />
                    ))}
                  </div>

                  <div className="mt-4">
                    <p className="mb-1.5 text-xs font-medium">Agreement</p>
                    <AgreementCell
                      agreement={lead.agreement}
                      agreementAt={lead.agreement_at}
                      disabled={busyId === lead.id}
                      leadName={lead.company_name}
                      onChoose={(choice) => chooseAgreement(lead, choice)}
                    />
                  </div>

                  <div className="mt-4 flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1"
                      onClick={() => setEditing(lead)}
                    >
                      <Pencil className="h-3.5 w-3.5" /> Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1"
                      disabled={busyId === lead.id || Boolean(lead.archived_at)}
                      onClick={() => setPendingArchive(lead)}
                    >
                      <Archive className="h-3.5 w-3.5" /> Archive
                    </Button>
                  </div>
                </div>
              ))}
            </FadeIn>
          </>
        )}

        {pageCount > 1 ? (
          <nav
            className="mt-4 flex items-center justify-end gap-2"
            aria-label="Lead pages"
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

        {creating ? (
          <LeadFormModal
            saving={saving}
            onCancel={() => setCreating(false)}
            onSave={(values) => void createLead(values)}
          />
        ) : null}

        {editing ? (
          <LeadFormModal
            key={editing.id}
            lead={editing}
            saving={saving}
            onCancel={() => setEditing(null)}
            onSave={(values) => void saveLead(values)}
          />
        ) : null}

        <AgreementConfirmDialog
          pending={pendingAgreement}
          onCancel={() => setPendingAgreement(null)}
          onConfirm={(lead, choice) => void commitAgreement(lead, choice)}
        />

        <AlertDialog
          open={Boolean(pendingArchive)}
          onOpenChange={(open) => !open && setPendingArchive(null)}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Archive {pendingArchive?.company_name}?
              </AlertDialogTitle>
              <AlertDialogDescription>
                The lead is hidden from this list, not deleted. Everything
                recorded about the relationship is kept, and &ldquo;Show
                archived&rdquo; brings it back.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => {
                  if (pendingArchive) void archiveLead(pendingArchive);
                }}
              >
                Archive lead
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </TooltipProvider>
  );
}

/**
 * The agreement confirmation. It names the consequence rather than asking "are
 * you sure": a yes MINTS a customer and a withdrawn yes ARCHIVES one, and
 * neither is guessable from a checkbox.
 */
function AgreementConfirmDialog({
  pending,
  onCancel,
  onConfirm,
}: {
  pending: { lead: BDLead; choice: AgreementChoice } | null;
  onCancel: () => void;
  onConfirm: (lead: BDLead, choice: AgreementChoice) => void;
}) {
  const consequence = pending
    ? agreementConsequence(pending.lead, pending.choice)
    : null;
  return (
    <AlertDialog
      open={Boolean(pending && consequence)}
      onOpenChange={(open) => !open && onCancel()}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{consequence?.title}</AlertDialogTitle>
          <AlertDialogDescription>{consequence?.body}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              if (pending) onConfirm(pending.lead, pending.choice);
            }}
          >
            {consequence?.action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function LeadsSkeleton() {
  return (
    <div className="space-y-2 rounded-md border p-4" aria-busy="true">
      <span className="sr-only">Loading leads</span>
      {Array.from({ length: 6 }).map((_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}
