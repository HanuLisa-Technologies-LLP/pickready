"use client";

// Create or edit one BD lead, for either reach channel.
//
// The channel is fixed by the page that opened this modal and is never a field:
// moving a lead between Personal Reach and Social Reach would either strip a
// real source or invent one, so `POST /bd/leads` takes the channel once and
// `PATCH` refuses it afterwards.
//
// SOURCE IS THE ONE ASYMMETRY. A social lead REQUIRES a source and a personal
// lead FORBIDS one, enforced by a Postgres CHECK constraint. This form enforces
// the same rule up front: the select only exists on the social channel, and it
// blocks the save when it is empty, so the database never has to answer for a
// shape the UI could have caught.

import * as React from "react";

import {
  SOCIAL_SOURCES,
  SOCIAL_SOURCE_LABELS,
  type BDChannel,
  type BDLead,
  type BDLeadFormValues,
  type BDSocialSource,
} from "@/lib/bd-types";
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

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const EMPTY_LEAD_FORM: BDLeadFormValues = {
  company_name: "",
  website: "",
  industry: "",
  location: "",
  contact_name: "",
  contact_email: "",
  contact_phone: "",
  notes: "",
  social_source: "",
};

export function leadToForm(lead: BDLead): BDLeadFormValues {
  return {
    company_name: lead.company_name ?? "",
    website: lead.website ?? "",
    industry: lead.industry ?? "",
    location: lead.location ?? "",
    contact_name: lead.contact_name ?? "",
    contact_email: lead.contact_email ?? "",
    contact_phone: lead.contact_phone ?? "",
    notes: lead.notes ?? "",
    social_source: lead.social_source ?? "",
  };
}

export type LeadFormErrors = Partial<Record<keyof BDLeadFormValues, string>>;

export function validateLead(
  values: BDLeadFormValues,
  channel: BDChannel
): LeadFormErrors {
  const errors: LeadFormErrors = {};
  if (!values.company_name.trim()) {
    errors.company_name = "A lead needs a company name.";
  }
  if (values.contact_email.trim() && !EMAIL_RE.test(values.contact_email.trim())) {
    errors.contact_email = "Enter a valid email address, or leave it blank.";
  }
  if (channel === "social" && !values.social_source) {
    errors.social_source =
      "A social lead needs a source. Choose LinkedIn, Google, Facebook, Instagram or X.";
  }
  return errors;
}

export function LeadFormModal({
  channel,
  lead,
  saving,
  onCancel,
  onSave,
}: {
  channel: BDChannel;
  /** Absent for a create. */
  lead?: BDLead | null;
  saving: boolean;
  onCancel: () => void;
  onSave: (values: BDLeadFormValues) => void;
}) {
  const editing = Boolean(lead);
  const [values, setValues] = React.useState<BDLeadFormValues>(
    lead ? leadToForm(lead) : EMPTY_LEAD_FORM
  );
  const [errors, setErrors] = React.useState<LeadFormErrors>({});

  const set = <K extends keyof BDLeadFormValues>(
    key: K,
    value: BDLeadFormValues[K]
  ) => {
    setValues((current) => {
      const next = { ...current, [key]: value };
      // Re-validate as they type, but only ever clear errors they have fixed.
      setErrors((shown) =>
        Object.keys(shown).length ? validateLead(next, channel) : shown
      );
      return next;
    });
  };

  const submit = () => {
    const found = validateLead(values, channel);
    setErrors(found);
    if (Object.keys(found).length) return;
    onSave(values);
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit lead" : "Add lead"}</DialogTitle>
          <DialogDescription>
            {channel === "social"
              ? "A company found through social reach. The source is required."
              : "A company approached directly by the team."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <FormField
            label="Company name"
            htmlFor="lead-company"
            required
            error={errors.company_name}
          >
            <Input
              id="lead-company"
              value={values.company_name}
              maxLength={255}
              disabled={saving}
              aria-invalid={Boolean(errors.company_name)}
              onChange={(event) => set("company_name", event.target.value)}
            />
          </FormField>

          {channel === "social" ? (
            <FormField label="Source" required error={errors.social_source}>
              <Select
                value={values.social_source || undefined}
                disabled={saving}
                onValueChange={(source) =>
                  set("social_source", source as BDSocialSource)
                }
              >
                <SelectTrigger aria-invalid={Boolean(errors.social_source)}>
                  <SelectValue placeholder="Where the lead came from" />
                </SelectTrigger>
                <SelectContent>
                  {SOCIAL_SOURCES.map((source) => (
                    <SelectItem key={source} value={source}>
                      {SOCIAL_SOURCE_LABELS[source]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormField>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Industry" htmlFor="lead-industry">
              <Input
                id="lead-industry"
                value={values.industry}
                maxLength={100}
                disabled={saving}
                onChange={(event) => set("industry", event.target.value)}
              />
            </FormField>
            <FormField label="Location" htmlFor="lead-location">
              <Input
                id="lead-location"
                value={values.location}
                maxLength={255}
                disabled={saving}
                onChange={(event) => set("location", event.target.value)}
              />
            </FormField>
          </div>

          <FormField
            label="Website"
            htmlFor="lead-website"
            hint="For example acme.example.com"
          >
            <Input
              id="lead-website"
              value={values.website}
              maxLength={255}
              disabled={saving}
              onChange={(event) => set("website", event.target.value)}
            />
          </FormField>

          <div className="grid gap-4 sm:grid-cols-3">
            <FormField label="Contact name" htmlFor="lead-contact-name">
              <Input
                id="lead-contact-name"
                value={values.contact_name}
                maxLength={255}
                disabled={saving}
                onChange={(event) => set("contact_name", event.target.value)}
              />
            </FormField>
            <FormField
              label="Contact email"
              htmlFor="lead-contact-email"
              error={errors.contact_email}
            >
              <Input
                id="lead-contact-email"
                type="email"
                value={values.contact_email}
                maxLength={320}
                disabled={saving}
                aria-invalid={Boolean(errors.contact_email)}
                onChange={(event) => set("contact_email", event.target.value)}
              />
            </FormField>
            <FormField label="Contact phone" htmlFor="lead-contact-phone">
              <Input
                id="lead-contact-phone"
                type="tel"
                value={values.contact_phone}
                maxLength={50}
                disabled={saving}
                onChange={(event) => set("contact_phone", event.target.value)}
              />
            </FormField>
          </div>

          <FormField
            label="Notes"
            htmlFor="lead-notes"
            hint="Visible to the PickReady team only."
          >
            <Textarea
              id="lead-notes"
              rows={4}
              value={values.notes}
              maxLength={5000}
              disabled={saving}
              onChange={(event) => set("notes", event.target.value)}
            />
          </FormField>
        </div>

        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onCancel}>
            Cancel
          </Button>
          <Button disabled={saving} onClick={submit}>
            {saving ? "Saving…" : editing ? "Save changes" : "Add lead"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
