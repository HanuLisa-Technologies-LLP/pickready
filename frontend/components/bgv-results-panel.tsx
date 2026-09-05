"use client";

// Recruiter-facing background verification (add-features spec 2026-09-05,
// Candidate Verification). Everything shown here crossed the candidate's own
// consent boundary: the API returns an inquiry's details ONLY where the
// candidate shared it with this employer, and an unshared inquiry arrives as
// a bare marker, so this panel can honestly say "Not shared by the
// candidate" without knowing who was asked. The domain-match word is
// provenance beside the parsed fields, never a verdict.

import * as React from "react";
import { MailCheck } from "lucide-react";

import { apiGet } from "@/lib/api";
import { Badge } from "@/components/ui/badge";

type SharedInquiry = {
  shared: true;
  employer_name: string;
  departmental_email: string;
  domain_match_result: "matched" | "mismatched" | "indeterminate";
  status: string;
  inquiry_sent_at: string | null;
  response_received_at: string | null;
  parsed_fields: Record<string, string | boolean | null> | null;
};

type UnsharedInquiry = { shared: false; note: string };

type BgvResultItem = SharedInquiry | UnsharedInquiry;

const STATUS_COPY: Record<string, string> = {
  collected: "Inquiry recorded, not sent yet",
  dispatched: "Inquiry sent, awaiting the employer's reply",
  dispatch_failed: "The inquiry could not be delivered",
  response_received: "Reply received, being read",
  parsed: "Reply received and recorded",
  parse_failed: "Reply received, but it could not be read automatically",
};

const DOMAIN_COPY: Record<SharedInquiry["domain_match_result"], string> = {
  matched: "Mailbox domain matches the employer name",
  mismatched: "Mailbox domain does not obviously match the employer name",
  indeterminate: "Mailbox domain could not be compared with the employer name",
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

function fieldText(value: string | boolean | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "Not stated in the reply";
  }
  if (value === true) return "Completed";
  if (value === false) return "Not completed";
  return String(value);
}

export function BgvResultsPanel({ candidateId }: { candidateId: string }) {
  const [items, setItems] = React.useState<BgvResultItem[] | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setItems(null);
    apiGet<{ inquiries: BgvResultItem[] }>(`/candidates/${candidateId}/bgv`)
      .then((res) => {
        if (!cancelled) setItems(res.inquiries);
      })
      .catch(() => {
        if (!cancelled) setItems(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [candidateId]);

  if (loading) {
    return (
      <p role="status" className="text-sm">
        Checking background verification
      </p>
    );
  }
  if (items === null) {
    return (
      <p className="text-sm">
        Background verification could not be loaded right now.
      </p>
    );
  }
  if (items.length === 0) {
    return (
      <p className="text-sm">
        The candidate has not started any employer verification.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {items.map((item, index) =>
        item.shared ? (
          <div key={index} className="rounded-md border p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="flex items-center gap-2 text-sm font-semibold">
                  <MailCheck className="h-4 w-4" aria-hidden />
                  {item.employer_name}
                </p>
                <p className="text-sm">{item.departmental_email}</p>
              </div>
              <p className="text-sm font-medium">
                {STATUS_COPY[item.status] ?? item.status}
              </p>
            </div>
            {/* Teal carries what is corroborated; a mismatch stays neutral
                prose because provenance is context, never a verdict. */}
            {item.domain_match_result === "matched" ? (
              <Badge className="mt-2 border-teal-600 bg-transparent text-teal-700">
                {DOMAIN_COPY.matched}
              </Badge>
            ) : (
              <p className="mt-2 text-xs">
                {DOMAIN_COPY[item.domain_match_result]}
              </p>
            )}
            {item.parsed_fields ? (
              <dl className="mt-3 grid gap-1 text-sm sm:grid-cols-2">
                {FIELD_LABELS.map(({ key, label }) => (
                  <div key={key}>
                    <dt className="text-xs font-medium">{label}</dt>
                    <dd>{fieldText(item.parsed_fields?.[key])}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
          </div>
        ) : (
          <div key={index} className="rounded-md border p-4">
            <p className="text-sm">{item.note}</p>
          </div>
        )
      )}
    </div>
  );
}
