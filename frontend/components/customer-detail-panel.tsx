"use client";

// The Provider Portal's customer detail view (spec §4.1).
//
// Everything here is READ-ONLY except the two action buttons at the bottom.
// The customer's identity, contact details and team are theirs to maintain in
// their own portal, so this panel deliberately offers no way to edit them, 
// the same asymmetry the API enforces by not exposing the routes.

import * as React from "react";
import { Building2, Mail, Phone, PhoneCall, User as UserIcon } from "lucide-react";

import { API_BASE } from "@/lib/api";
import type { CustomerDetail } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { ComplianceDocumentsSection } from "@/components/compliance-documents-section";

const ROLE_LABELS: Record<string, string> = {
  recruitment_manager: "Recruitment Manager",
  hr_manager: "HR Manager",
  recruiter: "Recruiter",
  hiring_manager: "Hiring Manager",
  client: "Company Admin",
};

function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <dt className="text-xs font-medium">{label}</dt>
      <dd className="text-sm">{value || "Not set"}</dd>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs font-medium">{label}</p>
      <p className="text-xl font-semibold tabular-nums">{value}</p>
    </div>
  );
}

export function CustomerDetailPanel({
  customer,
  loading,
  onClose,
  onEdit,
  onToggleArchive,
  archiving,
}: {
  customer: CustomerDetail | null;
  loading: boolean;
  onClose: () => void;
  onEdit: () => void;
  onToggleArchive: () => void;
  archiving: boolean;
}) {
  const archived = customer?.status === "archived";

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" aria-hidden />
            {customer?.name ?? "Customer"}
          </DialogTitle>
          <DialogDescription>
            Everything PickReady holds for this customer. Contact details, team
            and documents are maintained by the customer and shown here
            read-only.
          </DialogDescription>
        </DialogHeader>

        {loading || !customer ? (
          <p className="py-10 text-center text-sm">
            Loading customer
          </p>
        ) : (
          <div className="space-y-6">
            <section>
              <h3 className="mb-2 text-base font-semibold">Basic Info</h3>
              <dl className="grid gap-3 sm:grid-cols-2">
                <Field label="Company Name" value={customer.name} />
                <Field label="Industry" value={customer.industry} />
                <Field label="Website / Domain" value={customer.website_domain} />
                <div>
                  <dt className="text-xs font-medium">
                    Status
                  </dt>
                  <dd className="text-sm">
                    <Badge variant={archived ? "outline" : "default"}>
                      {archived ? "Archived" : "Active"}
                    </Badge>
                  </dd>
                </div>
                <Field
                  label="Created"
                  value={new Date(customer.created_at).toLocaleDateString()}
                />
                {archived && customer.archived_at ? (
                  <Field
                    label="Archived"
                    value={new Date(customer.archived_at).toLocaleDateString()}
                  />
                ) : null}
              </dl>
              {customer.notes ? (
                <div className="mt-3">
                  <p className="text-xs font-medium">
                    Notes
                  </p>
                  <p className="whitespace-pre-wrap text-sm">{customer.notes}</p>
                </div>
              ) : null}
            </section>

            <Separator />

            <section>
              <h3 className="mb-2 text-base font-semibold">
                Primary Contact (HR Head)
              </h3>
              <dl className="grid gap-3 sm:grid-cols-2">
                <div className="flex items-center gap-2">
                  <UserIcon className="h-4 w-4" aria-hidden />
                  <Field label="Name" value={customer.primary_contact.name} />
                </div>
                <div className="flex items-center gap-2">
                  <Mail className="h-4 w-4" aria-hidden />
                  <Field label="Email" value={customer.primary_contact.email} />
                </div>
                <div className="flex items-center gap-2">
                  <Phone className="h-4 w-4" aria-hidden />
                  <Field label="Phone" value={customer.primary_contact.phone} />
                </div>
                <div className="flex items-center gap-2">
                  <PhoneCall
                    className="h-4 w-4"
                    aria-hidden
                  />
                  <Field
                    label="Landline"
                    value={customer.primary_contact.landline}
                  />
                </div>
              </dl>
            </section>

            <Separator />

            <section>
              <h3 className="mb-2 text-base font-semibold">
                Team ({customer.team_size}{" "}
                {customer.team_size === 1 ? "member" : "members"})
              </h3>
              {customer.team.length === 0 ? (
                <p className="text-sm">
                  No team members yet.
                </p>
              ) : (
                <ul className="divide-y rounded-md border">
                  {customer.team.map((member) => (
                    <li
                      key={member.id}
                      className="flex items-center justify-between gap-3 p-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {member.name || member.email}
                        </p>
                        <p className="truncate text-xs">
                          {member.email}
                        </p>
                      </div>
                      <Badge variant="outline">
                        {ROLE_LABELS[member.role] ?? member.role}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
              {customer.team_size > customer.team.length ? (
                <p className="mt-2 text-xs">
                  Showing {customer.team.length} of {customer.team_size} members.
                </p>
              ) : null}
            </section>

            <Separator />

            <section>
              <h3 className="mb-2 text-base font-semibold">Analytics</h3>
              <div className="grid gap-3 sm:grid-cols-3">
                <Stat
                  label="Jobs Posted (All Time)"
                  value={customer.analytics.jobs_posted}
                />
                <Stat label="Jobs Closed" value={customer.analytics.jobs_closed} />
                <Stat
                  label="Jobs Ongoing"
                  value={customer.analytics.jobs_ongoing}
                />
                <Stat
                  label="Candidates Interacted"
                  value={customer.analytics.total_candidates_interacted}
                />
                <Stat
                  label="Jobs in Last 30 Days"
                  value={customer.analytics.jobs_last_30_days}
                />
              </div>
            </section>

            <Separator />

            <ComplianceDocumentsSection
              slots={customer.compliance_documents}
              documentHref={(slot, inline) =>
                // Absent documents never render a link, so the id is present
                // whenever this runs.
                `${API_BASE}/provider/compliance-documents/${slot.document!.id}/download${
                  inline ? "?inline=true" : ""
                }`
              }
            />
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button variant="outline" disabled={!customer} onClick={onEdit}>
            Edit
          </Button>
          <Button
            variant="outline"
            disabled={!customer || archiving}
            onClick={onToggleArchive}
          >
            {archiving ? "Saving" : archived ? "Unarchive" : "Archive"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
