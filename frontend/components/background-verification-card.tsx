"use client";

// Background verification on My Profile (add-features spec 2026-09-05,
// Candidate Verification).
//
// The candidate names their previous two employers' departmental mailboxes
// (hr@, careers@, resumes@ on the company's own domain), sends one inquiry
// each, and controls which employer they share the result with. Following up
// with an unresponsive employer is the candidate's own responsibility, and
// the card says so plainly. Every state is a word, never a number.

import * as React from "react";
import { MailCheck } from "lucide-react";

import { apiGet, apiPost, apiPut } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import type { BgvInquiry, BgvInquiryStatus, BgvList } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const STATUS_COPY: Record<BgvInquiryStatus, string> = {
  collected: "Recorded, not sent yet",
  dispatched: "Inquiry sent, awaiting the employer's reply",
  dispatch_failed: "The inquiry could not be delivered",
  response_received: "Reply received, being read",
  parsed: "Reply received and recorded",
  parse_failed: "Reply received, but it could not be read automatically",
};

const DOMAIN_COPY: Record<string, string> = {
  matched: "The mailbox domain matches this employer's name.",
  mismatched:
    "The mailbox domain does not obviously match this employer's name. You can still send the inquiry.",
  indeterminate:
    "The mailbox domain could not be compared with this employer's name.",
};

const FIELD_LABELS: Array<{ key: string; label: string }> = [
  { key: "duration", label: "Employment duration" },
  { key: "designation", label: "Designation" },
  { key: "reporting_manager", label: "Reporting manager" },
  { key: "compensation", label: "Compensation" },
  { key: "exit_formalities", label: "Exit formalities" },
  { key: "noc", label: "NOC status" },
  { key: "relieving_method", label: "Relieving method" },
];

function parsedFieldText(value: string | boolean | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "Not stated in the reply";
  }
  if (value === true) return "Completed";
  if (value === false) return "Not completed";
  return String(value);
}

export function BackgroundVerificationCard() {
  const { toast } = useToast();
  const [data, setData] = React.useState<BgvList | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [employerName, setEmployerName] = React.useState("");
  const [email, setEmail] = React.useState("");

  React.useEffect(() => {
    apiGet<BgvList>("/portal/me/bgv")
      .then(setData)
      .catch((error) => setLoadError(apiErrorMessage(error)))
      .finally(() => setLoading(false));
  }, []);

  const addInquiry = async () => {
    setBusy(true);
    try {
      const created = await apiPost<BgvList>("/portal/me/bgv", {
        employer_name: employerName,
        departmental_email: email,
      });
      const added = created.inquiries.find(
        (inquiry) =>
          inquiry.departmental_email.toLowerCase() === email.trim().toLowerCase()
      );
      let latest = created;
      if (added) {
        // One click records and sends: the dispatch is still the candidate's
        // own explicit action, this button is it.
        latest = await apiPost<BgvList>(`/portal/me/bgv/${added.id}/dispatch`);
        toast({
          title: "Inquiry sent",
          description:
            "We emailed the employer. Following up with them is up to you.",
        });
      }
      setData(latest);
      setEmployerName("");
      setEmail("");
    } catch (error) {
      toast({
        title: "Inquiry not sent",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
      // The row may have been recorded even when sending failed; reload so
      // the card shows the real state instead of guessing.
      try {
        setData(await apiGet<BgvList>("/portal/me/bgv"));
      } catch {
        /* the earlier toast already reports the problem */
      }
    } finally {
      setBusy(false);
    }
  };

  const resend = async (inquiry: BgvInquiry) => {
    setBusy(true);
    try {
      setData(await apiPost<BgvList>(`/portal/me/bgv/${inquiry.id}/dispatch`));
      toast({ title: "Inquiry sent" });
    } catch (error) {
      toast({
        title: "Inquiry not sent",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const setConsent = async (
    inquiry: BgvInquiry,
    tenantId: string,
    granted: boolean
  ) => {
    setBusy(true);
    try {
      setData(
        await apiPut<BgvList>(`/portal/me/bgv/${inquiry.id}/consents`, {
          tenant_id: tenantId,
          granted,
        })
      );
      toast({
        title: granted ? "Shared" : "Sharing stopped",
        description: granted
          ? "That employer can now see this verification result."
          : "That employer can no longer see this verification result.",
      });
    } catch (error) {
      toast({
        title: "Sharing not updated",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MailCheck className="h-5 w-5" aria-hidden />
          Background verification
        </CardTitle>
        <CardDescription>
          Add your previous two employers&apos; departmental mailboxes (such as
          hr@ or careers@ on the company&apos;s own domain). We send one
          verification inquiry each; ensuring the employer replies is your
          responsibility. Results stay on your profile and an employer sees
          them only when you share with that employer.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {loading ? (
          <p role="status" className="text-sm">
            Checking your verifications
          </p>
        ) : loadError ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {loadError}
          </p>
        ) : data ? (
          <>
            {data.inquiries.map((inquiry) => (
              <div key={inquiry.id} className="rounded-md border p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold">{inquiry.employer_name}</p>
                    <p className="text-sm">{inquiry.departmental_email}</p>
                  </div>
                  <p className="text-sm font-medium">
                    {STATUS_COPY[inquiry.status]}
                  </p>
                </div>
                <p className="mt-1 text-xs">
                  {DOMAIN_COPY[inquiry.domain_match_result]}
                </p>
                {inquiry.status === "collected" ||
                inquiry.status === "dispatch_failed" ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    disabled={busy}
                    onClick={() => void resend(inquiry)}
                  >
                    {inquiry.status === "dispatch_failed"
                      ? "Try sending again"
                      : "Send inquiry"}
                  </Button>
                ) : null}
                {inquiry.status === "parse_failed" ? (
                  <p className="mt-2 text-xs font-medium">
                    The employer&apos;s reply is saved. You may ask them to
                    resend it as a plain written answer to the listed
                    questions.
                  </p>
                ) : null}
                {inquiry.status === "parsed" && inquiry.parsed_fields ? (
                  <dl className="mt-3 grid gap-1 text-sm sm:grid-cols-2">
                    {FIELD_LABELS.map(({ key, label }) => (
                      <div key={key}>
                        <dt className="text-xs font-medium">{label}</dt>
                        <dd>{parsedFieldText(inquiry.parsed_fields?.[key])}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
                {data.shareable_tenants.length > 0 ? (
                  <div className="mt-3 border-t pt-3">
                    <p className="text-xs font-semibold">
                      Share this result with an employer you applied to
                    </p>
                    <div className="mt-2 space-y-2">
                      {data.shareable_tenants.map((tenant) => {
                        const shared = inquiry.consents.some(
                          (consent) => consent.tenant_id === tenant.tenant_id
                        );
                        return (
                          <div
                            key={tenant.tenant_id}
                            className="flex items-center justify-between gap-4"
                          >
                            <span className="text-sm">
                              {tenant.tenant_name ?? "Employer"}
                            </span>
                            <Switch
                              checked={shared}
                              disabled={busy}
                              onCheckedChange={(next) =>
                                void setConsent(inquiry, tenant.tenant_id, next)
                              }
                              aria-label={`Share with ${tenant.tenant_name ?? "this employer"}`}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>
            ))}
            {data.inquiries.length === 0 ? (
              <p className="text-sm">
                No verification inquiries yet. Add a previous employer below to
                start one.
              </p>
            ) : null}
            {data.can_add ? (
              <form
                className="rounded-md border p-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void addInquiry();
                }}
              >
                <p className="text-sm font-semibold">Add a previous employer</p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="bgv-employer-name">Employer name</Label>
                    <Input
                      id="bgv-employer-name"
                      value={employerName}
                      onChange={(event) => setEmployerName(event.target.value)}
                      placeholder="Acme Software Pvt Ltd"
                      required
                      minLength={2}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="bgv-email">Departmental email</Label>
                    <Input
                      id="bgv-email"
                      type="email"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      placeholder="hr@acmesoftware.com"
                      required
                    />
                  </div>
                </div>
                <Button
                  type="submit"
                  size="sm"
                  className="mt-3"
                  disabled={busy || !employerName.trim() || !email.trim()}
                >
                  Record and send inquiry
                </Button>
              </form>
            ) : (
              <p className="text-xs">
                Both previous employers are recorded, which is all this check
                covers.
              </p>
            )}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
