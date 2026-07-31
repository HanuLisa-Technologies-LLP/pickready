"use client";

// The Provider's edit modal (spec §4.2).
//
// Three editable fields, and the rest shown read-only rather than omitted:
// seeing "Company Name, Acme Corp" greyed out answers "can I change this?"
// on the spot, where a missing field just looks like the form forgot it.
// Company name, primary contact and team belong to the customer and are
// maintained in their own portal, the API accepts none of them here.

import * as React from "react";

import type { Customer } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Mirrors schemas/admin.INDUSTRY_CHOICES, the API is the source of truth. */
export const INDUSTRIES = [
  "Technology",
  "Finance",
  "Healthcare",
  "Retail",
  "Manufacturing",
  "Education",
  "Other",
] as const;

export interface CustomerEditValues {
  industry: string;
  website_domain: string;
  notes: string;
}

function ReadOnly({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <p className="text-xs font-medium">{label}</p>
      <p className="text-sm">{value || "Not set"}</p>
    </div>
  );
}

export function CustomerEditModal({
  customer,
  saving,
  onCancel,
  onSave,
}: {
  customer: Customer;
  saving: boolean;
  onCancel: () => void;
  onSave: (values: CustomerEditValues) => void;
}) {
  const [values, setValues] = React.useState<CustomerEditValues>({
    industry: customer.industry ?? "",
    website_domain: customer.website_domain ?? "",
    notes: customer.notes ?? "",
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit customer</DialogTitle>
          <DialogDescription>
            Only industry, website and internal notes are yours to change. The
            customer maintains everything else in their own portal.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 rounded-md border p-3 sm:grid-cols-2">
            <ReadOnly label="Company Name" value={customer.name} />
            <ReadOnly
              label="Status"
              value={customer.status === "archived" ? "Archived" : "Active"}
            />
            <ReadOnly
              label="Primary Contact"
              value={
                customer.primary_contact.name || customer.primary_contact.email
                  ? `${customer.primary_contact.name ?? "Owner"}${
                      customer.primary_contact.email
                        ? ` (${customer.primary_contact.email})`
                        : ""
                    }`
                  : null
              }
            />
            <ReadOnly
              label="Created"
              value={new Date(customer.created_at).toLocaleDateString()}
            />
          </div>

          <FormField label="Industry">
            <Select
              value={values.industry}
              disabled={saving}
              onValueChange={(industry) =>
                setValues((current) => ({ ...current, industry }))
              }
            >
              <SelectTrigger>
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
          </FormField>

          <FormField
            label="Website / domain"
            htmlFor="customer-website"
            hint="For example acme.example.com"
          >
            <Input
              id="customer-website"
              value={values.website_domain}
              disabled={saving}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  website_domain: event.target.value,
                }))
              }
            />
          </FormField>

          <FormField
            label="Internal notes"
            htmlFor="customer-notes"
            hint="Visible to PickReady only, never to the customer."
          >
            <Textarea
              id="customer-notes"
              rows={4}
              value={values.notes}
              disabled={saving}
              onChange={(event) =>
                setValues((current) => ({ ...current, notes: event.target.value }))
              }
            />
          </FormField>
        </div>

        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onCancel}>
            Cancel
          </Button>
          <Button disabled={saving} onClick={() => onSave(values)}>
            {saving ? "Saving" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
