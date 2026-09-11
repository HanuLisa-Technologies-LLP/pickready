"use client";

// The Provider's edit modal (spec §4.2).
//
// Three editable fields, and the rest shown read-only rather than omitted:
// seeing "Company Name, Acme Corp" greyed out answers "can I change this?"
// on the spot, where a missing field just looks like the form forgot it.
// Company name and team belong to the customer and are maintained in their own
// portal; the API accepts neither here.
//
// THE PRIMARY CONTACT IS THE ONE EXCEPTION, and it is deliberate (owner
// decision, 2026-09-11). Read-only-by-absence protects the customer's own
// data. The primary contact is not that: it is the DOOR into the tenant, and
// before this section a typo at onboarding, an expired invite, or a tenant
// seeded without an address left a customer permanently unreachable, with no
// route anywhere in the product able to repair it.
//
// It saves on its OWN button, against its own endpoint, and never as part of
// "Save". Changing it can send an invitation and can move which Firebase
// identity owns the account, and an action with those consequences must not be
// something somebody performs by editing the notes field and pressing the
// button they always press.

import * as React from "react";

import { apiPut } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import type { Customer, PrimaryContact } from "@/lib/types";
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

interface PrimaryContactSetOut {
  contact: PrimaryContact;
  invite_sent: boolean;
  rebound: boolean;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * The primary contact, editable, with its own save.
 *
 * The copy under the button is the whole point of the section: the operator has
 * to be able to tell, BEFORE pressing it, which of the three things is about to
 * happen. Adding a contact who does not exist, re-sending an invitation to
 * somebody who never signed in, and moving a signed-in account onto a new
 * address are different acts with different consequences, and the server
 * decides which one it was and says so in the response. So the warning is
 * derived from the account's current state, never from a guess, and the result
 * is reported back in the same words the server used.
 */
function PrimaryContactSection({
  customer,
  onSaved,
}: {
  customer: Customer;
  onSaved: (contact: PrimaryContact, message: string) => void;
}) {
  const existing = customer.primary_contact;
  const [email, setEmail] = React.useState(existing.email ?? "");
  const [fullName, setFullName] = React.useState(existing.name ?? "");
  const [phone, setPhone] = React.useState(existing.phone ?? "");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const trimmed = email.trim();
  const emailChanged =
    trimmed.toLowerCase() !== (existing.email ?? "").trim().toLowerCase();
  // `status` is the account's own lifecycle. "active" is the only value that
  // means a Firebase identity is bound to it, which is what makes an email
  // change a hand-over rather than a correction.
  const isBound = existing.status === "active";
  const unchanged =
    !emailChanged &&
    fullName.trim() === (existing.name ?? "").trim() &&
    phone.trim() === (existing.phone ?? "").trim();

  let consequence: string;
  if (!existing.email) {
    consequence =
      "No primary contact is set. Saving creates the customer's Super Admin account and emails them a join link.";
  } else if (emailChanged && isBound) {
    consequence = `${existing.email} is signed in today. Saving moves sign-in to the new address: the account returns to invited, the old address loses access, and a join link goes to the new one.`;
  } else if (emailChanged) {
    consequence =
      "This account has never signed in. Saving corrects the address, cancels any outstanding join link, and sends a fresh one.";
  } else if (isBound) {
    consequence =
      "Saving updates the name and phone only. No email is sent and sign-in is untouched.";
  } else {
    consequence =
      "This account has never signed in. Saving sends them a new join link and cancels the previous one.";
  }

  async function save() {
    if (!EMAIL_RE.test(trimmed)) {
      setError("Enter a valid email address.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await apiPut<PrimaryContactSetOut>(
        `/provider/customers/${customer.id}/primary-contact`,
        {
          email: trimmed,
          full_name: fullName.trim() || null,
          phone: phone.trim() || null,
        },
      );
      onSaved(
        result.contact,
        result.rebound
          ? `Sign-in moved to ${trimmed}. A join link is on its way and the previous address no longer has access.`
          : result.invite_sent
            ? `Join link sent to ${trimmed}.`
            : "Primary contact updated. No email was sent.",
      );
    } catch (caught) {
      setError(apiErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-md border p-3">
      <div>
        <p className="text-sm font-medium">Primary contact</p>
        <p className="text-xs">
          The customer&apos;s Super Admin. This is the address ReadyPick invites
          and the one they sign in with.
        </p>
      </div>

      <FormField label="Email" htmlFor="contact-email" required>
        <Input
          id="contact-email"
          type="email"
          value={email}
          disabled={busy}
          aria-invalid={Boolean(error)}
          onChange={(event) => {
            setEmail(event.target.value);
            setError(null);
          }}
        />
      </FormField>

      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="Full name" htmlFor="contact-name">
          <Input
            id="contact-name"
            value={fullName}
            disabled={busy}
            onChange={(event) => setFullName(event.target.value)}
          />
        </FormField>
        <FormField label="Phone" htmlFor="contact-phone">
          <Input
            id="contact-phone"
            value={phone}
            disabled={busy}
            onChange={(event) => setPhone(event.target.value)}
          />
        </FormField>
      </div>

      {error ? <p className="text-sm font-medium">{error}</p> : null}
      <p className="text-xs">{consequence}</p>

      <Button
        variant="outline"
        size="sm"
        disabled={busy || unchanged}
        onClick={() => void save()}
      >
        {busy ? "Saving" : "Save primary contact"}
      </Button>
    </div>
  );
}

export function CustomerEditModal({
  customer,
  saving,
  onCancel,
  onSave,
  onContactSaved,
}: {
  customer: Customer;
  saving: boolean;
  onCancel: () => void;
  onSave: (values: CustomerEditValues) => void;
  onContactSaved?: (contact: PrimaryContact, message: string) => void;
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
              label="Created"
              value={new Date(customer.created_at).toLocaleDateString()}
            />
          </div>

          <PrimaryContactSection
            customer={customer}
            onSaved={(contact, message) => onContactSaved?.(contact, message)}
          />

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
            hint="Visible to ReadyPick only, never to the customer."
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
