"use client";

// Public employer verification form (FR-5.3): fields per the API contract,
// with a thank-you state after submission.

import * as React from "react";
import { useParams } from "next/navigation";
import { CheckCircle2, LinkIcon } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type {
  VerificationFormInfo,
  VerificationFormSubmission,
} from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { FormField, FormSection } from "@/components/ui/form";
import { PublicNotice, PublicShell } from "@/components/public-shell";
import { Section } from "@/components/page-primitives";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";

export default function VerifyEmploymentPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const { toast } = useToast();

  const [info, setInfo] = React.useState<VerificationFormInfo | null>(null);
  const [invalid, setInvalid] = React.useState(false);
  const [submitted, setSubmitted] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  const [form, setForm] = React.useState<VerificationFormSubmission>({
    designation: "",
    doj: "",
    doe: "",
    last_drawn_ctc: "",
    last_drawn_gross: "",
    noc_status: "",
    exit_formalities_complete: false,
    bgv_status: "",
    proofs_details: "",
    prior_experience_details: "",
  });

  React.useEffect(() => {
    apiGet<VerificationFormInfo>(`/verification/form/${token}`)
      .then(setInfo)
      .catch(() => setInvalid(true));
  }, [token]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await apiPost(`/verification/form/${token}`, form);
      setSubmitted(true);
    } catch (err) {
      toast({
        title: "Submission failed",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  if (invalid) {
    return (
      <PublicNotice
        tone="error"
        icon={<LinkIcon className="h-7 w-7" aria-hidden="true" />}
        title="Link not valid"
        description="This verification link has expired or was already completed."
      />
    );
  }

  if (submitted) {
    return (
      <PublicNotice
        tone="success"
        icon={<CheckCircle2 className="h-7 w-7" aria-hidden="true" />}
        title="Thank you"
        description="Your verification response has been recorded. No further action is needed."
      />
    );
  }

  return (
    <PublicShell>
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brand-600">
          Employment verification
        </p>
        <h1 className="mt-2 text-balance text-2xl font-bold tracking-tight">
          {info?.candidate_name
            ? `Confirm details for ${info.candidate_name}`
            : "Confirm employment details"}
        </h1>
        <p className="mt-2 text-pretty text-sm leading-6">
          You have been listed as a previous employer. Confirming takes about
          two minutes.
        </p>
      </div>

      <Section>
          <form className="space-y-8" onSubmit={submit}>
            <FormSection title="Employment record">
              <FormField label="Designation held" htmlFor="v-designation" required>
                <Input
                  id="v-designation"
                  value={form.designation}
                  onChange={(e) =>
                    setForm({ ...form, designation: e.target.value })
                  }
                  required
                />
              </FormField>
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label="Date of joining" htmlFor="v-doj" required>
                  <Input
                    id="v-doj"
                    type="date"
                    value={form.doj}
                    onChange={(e) => setForm({ ...form, doj: e.target.value })}
                    required
                  />
                </FormField>
                <FormField label="Date of exit" htmlFor="v-doe" required>
                  <Input
                    id="v-doe"
                    type="date"
                    value={form.doe}
                    onChange={(e) => setForm({ ...form, doe: e.target.value })}
                    required
                  />
                </FormField>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField label="Last drawn CTC" htmlFor="v-ctc" required>
                  <Input
                    id="v-ctc"
                    value={form.last_drawn_ctc}
                    onChange={(e) =>
                      setForm({ ...form, last_drawn_ctc: e.target.value })
                    }
                    required
                  />
                </FormField>
                <FormField label="Last drawn gross" htmlFor="v-gross" required>
                  <Input
                    id="v-gross"
                    value={form.last_drawn_gross}
                    onChange={(e) =>
                      setForm({ ...form, last_drawn_gross: e.target.value })
                    }
                    required
                  />
                </FormField>
              </div>
            </FormSection>

            <Separator />

            <FormSection title="Exit and compliance">
              <FormField label="NOC status" required>
                <Select
                  value={form.noc_status}
                  onValueChange={(v) => setForm({ ...form, noc_status: v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="issued">Issued</SelectItem>
                    <SelectItem value="not_issued">Not issued</SelectItem>
                    <SelectItem value="not_applicable">
                      Not applicable
                    </SelectItem>
                  </SelectContent>
                </Select>
              </FormField>
              <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-secondary px-4 py-3">
                <label
                  htmlFor="v-exit-formalities"
                  className="text-sm font-medium"
                >
                  Exit formalities completed
                </label>
                <Switch
                  id="v-exit-formalities"
                  checked={form.exit_formalities_complete}
                  onCheckedChange={(v) =>
                    setForm({ ...form, exit_formalities_complete: v })
                  }
                />
              </div>
              <FormField label="BGV status" required>
                <Select
                  value={form.bgv_status}
                  onValueChange={(v) => setForm({ ...form, bgv_status: v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="clear">Clear</SelectItem>
                    <SelectItem value="minor_discrepancy">
                      Minor discrepancy
                    </SelectItem>
                    <SelectItem value="major_discrepancy">
                      Major discrepancy
                    </SelectItem>
                    <SelectItem value="not_conducted">Not conducted</SelectItem>
                  </SelectContent>
                </Select>
              </FormField>
            </FormSection>

            <Separator />

            <FormSection title="Supporting details">
              <FormField
                label="Educational / address / ID proof details"
                htmlFor="v-proofs"
              >
                <Textarea
                  id="v-proofs"
                  rows={3}
                  value={form.proofs_details}
                  onChange={(e) =>
                    setForm({ ...form, proofs_details: e.target.value })
                  }
                />
              </FormField>
              <FormField
                label="Prior experience / compensation details"
                htmlFor="v-prior"
              >
                <Textarea
                  id="v-prior"
                  rows={3}
                  value={form.prior_experience_details}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      prior_experience_details: e.target.value,
                    })
                  }
                />
              </FormField>
            </FormSection>

            <Button type="submit" size="lg" className="w-full" disabled={busy}>
              {busy ? "Submitting" : "Submit verification"}
            </Button>
          </form>
      </Section>
    </PublicShell>
  );
}
