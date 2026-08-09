"use client";

// Create or edit one BD lead.
//
// Personal Reach and Social Reach merged into one BD Reach section on
// 2026-08-09, so the rep no longer picks a screen and thereby a channel. They
// pick a SOURCE: "Approached directly" or one of the five platforms. The
// `bd_leads.channel` column is unchanged and is now DERIVED from that answer,
// which is the whole of the merge as far as storage is concerned.
//
// SOURCE IS STILL THE ASYMMETRY. A social lead REQUIRES a source and a personal
// lead FORBIDS one, enforced by a Postgres CHECK constraint. This form enforces
// the same rule up front, so the database never has to answer for a shape the
// UI could have caught.
//
// The channel remains IMMUTABLE after creation (`PATCH` refuses it), so the
// source select is disabled when editing: switching it would either strip a
// real source or invent one.

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

/** The "no platform" choice. A Select cannot hold an empty string as a value,
 *  so the direct option needs a token of its own. */
export const DIRECT_SOURCE = "direct";

/** The channel a chosen source implies. `channel` stops being something the rep
 *  picks and becomes a fact about where the lead came from. */
export function channelForSource(source: string): BDChannel {
  return source && source !== DIRECT_SOURCE ? "social" : "personal";
}

export function validateLead(values: BDLeadFormValues): LeadFormErrors {
  const errors: LeadFormErrors = {};
  if (!values.company_name.trim()) {
    errors.company_name = "A lead needs a company name.";
  }
  if (values.contact_email.trim() && !EMAIL_RE.test(values.contact_email.trim())) {
    errors.contact_email = "Enter a valid email address, or leave it blank.";
  }
  if (!values.social_source) {
    // Unanswered, not "personal". A blank select is a question nobody answered,
    // and defaulting it would silently file every hurried lead as direct.
    errors.social_source =
      "Say where this lead came from: approached directly, or LinkedIn, Google, Facebook, Instagram or X.";
  }
  return errors;
}

export function LeadFormModal({
  lead,
  saving,
  onCancel,
  onSave,
}: {
  /** Absent for a create. */
  lead?: BDLead | null;
  saving: boolean;
  onCancel: () => void;
  onSave: (values: BDLeadFormValues) => void;
}) {
  const editing = Boolean(lead);
  const [values, setValues] = React.useState<BDLeadFormValues>(() => {
    if (!lead) return EMPTY_LEAD_FORM;
    const form = leadToForm(lead);
    // A stored personal lead has no source by construction; show it as the
    // direct choice rather than as an unanswered select.
    return form.social_source
      ? form
      : { ...form, social_source: DIRECT_SOURCE };
  });
  const [errors, setErrors] = React.useState<LeadFormErrors>({});

  const set = <K extends keyof BDLeadFormValues>(
    key: K,
    value: BDLeadFormValues[K]
  ) => {
    setValues((current) => {
      const next = { ...current, [key]: value };
      // Re-validate as they type, but only ever clear errors they have fixed.
      setErrors((shown) =>
        Object.keys(shown).length ? validateLead(next) : shown
      );
      return next;
    });
  };

  const submit = () => {
    const found = validateLead(values);
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
            {editing
              ? "Where a lead came from is fixed once it is added, so the source cannot be changed here."
              : "A company the team is working. Say where it came from and the rest is the same either way."}
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

          <FormField
            label="Source"
            required
            error={errors.social_source}
            hint={
              editing
                ? "Fixed once a lead is added."
                : "Approached directly, or the platform it was found on."
            }
          >
            <Select
              value={values.social_source || undefined}
              disabled={saving || editing}
              onValueChange={(source) =>
                set("social_source", source as BDSocialSource)
              }
            >
              <SelectTrigger aria-invalid={Boolean(errors.social_source)}>
                <SelectValue placeholder="Where the lead came from" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DIRECT_SOURCE}>
                  Approached directly
                </SelectItem>
                {SOCIAL_SOURCES.map((source) => (
                  <SelectItem key={source} value={source}>
                    {SOCIAL_SOURCE_LABELS[source]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>

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
