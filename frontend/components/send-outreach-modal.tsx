"use client";

// Send-outreach modal: two composition paths, one send path.
//
//  Step 1 "How should we reach out?"
//    · AI      , the backend generates a 150–200 word personalized email per
//                 candidate (POST /outreach/preview, mode="ai"); every
//                 generated subject/body is shown and is editable before send.
//    · Manual  , one subject + body with {{candidate_name}} / {{company}} /
//                 {{job_title}} placeholders, previewed live against the first
//                 recipient and substituted per recipient on the server.
//
//  Step 2 "Preview" , paginated, per-candidate, editable.
//  Send             , POST /outreach/send; the backend enqueues one Celery
//                      send_email task per recipient. The result is reported
//                      explicitly: "Emails sent to N candidates", the skipped
//                      list, and a loud notice when SMTP is not configured.

import * as React from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Eye, Send } from "lucide-react";

import { apiPost } from "@/lib/api";
import { buildOutreachComposePayload } from "@/lib/outreach-payload";
import { apiErrorMessage } from "@/lib/validation-errors";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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

export interface OutreachRecipientInput {
  link_id: string;
  name: string;
  email: string;
}

export interface SendOutreachModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: string;
  recipients: OutreachRecipientInput[];
  /** Called after at least one email was queued. */
  onSent?: (queued: number) => void;
}

type Mode = "ai" | "manual";

interface ResolvedEmail {
  link_id: string;
  candidate_id: string;
  name: string;
  email: string;
  subject: string;
  body: string;
  ai_fallback?: boolean;
}

interface SkippedRecipient {
  link_id: string;
  name: string;
  reason: string;
}

interface PreviewResponse {
  job_title: string;
  company: string;
  mode: Mode;
  recipients: ResolvedEmail[];
  skipped?: SkippedRecipient[];
  placeholders?: string[];
  smtp_configured?: boolean;
  delivery_warning?: string | null;
}

interface SendResponse {
  queued: number;
  recipients?: string[];
  task_ids: string[];
  skipped?: SkippedRecipient[];
  smtp_configured?: boolean;
  delivery_warning?: string | null;
}

interface DeliveryStatus {
  total: number;
  pending: number;
  sent: number;
  failed: number;
  done: boolean;
}

const DEFAULT_MANUAL_SUBJECT =
  "{{job_title}} at {{company}}, we would like to talk";
const DEFAULT_MANUAL_BODY =
  "Hi {{candidate_name}},\n\nWe have been reviewing your profile for the " +
  "{{job_title}} role at {{company}} and would like to take the conversation " +
  "further.\n\nIf you are interested, reply to this email and we will share " +
  "the next steps.\n\nWarm regards,\n{{company}} Talent Team";

/** Client-side mirror of the server substitution, preview only. */
function substitute(
  template: string,
  ctx: { candidate_name: string; company: string; job_title: string }
): string {
  return template
    .replace(/\{\{\s*candidate_name\s*\}\}/g, ctx.candidate_name)
    .replace(/\{\{\s*company\s*\}\}/g, ctx.company)
    .replace(/\{\{\s*job_title\s*\}\}/g, ctx.job_title)
    .replace(
      /\{\{\s*candidate_strengths\s*\}\}/g,
      "the experience outlined in your profile"
    );
}

function friendlyError(err: unknown): string {
  const message = apiErrorMessage(err);
  if (/Failed to fetch|Network/i.test(message)) {
    return "We could not reach the server. Check your connection and try again.";
  }
  if (/403|permission|capability/i.test(message)) {
    return "Your account does not have permission to send candidate outreach.";
  }
  if (/404/i.test(message)) {
    return "Some of the selected candidates are no longer linked to this job. Reload the page and try again.";
  }
  return message || "Something went wrong. Nothing was sent, please try again.";
}

export function SendOutreachModal({
  open,
  onOpenChange,
  jobId,
  recipients,
  onSent,
}: SendOutreachModalProps) {
  const [mode, setMode] = React.useState<Mode>("ai");
  const [manualSubject, setManualSubject] = React.useState(DEFAULT_MANUAL_SUBJECT);
  const [manualBody, setManualBody] = React.useState(DEFAULT_MANUAL_BODY);

  const [previews, setPreviews] = React.useState<ResolvedEmail[] | null>(null);
  const [index, setIndex] = React.useState(0);
  const [meta, setMeta] = React.useState<{
    company: string;
    jobTitle: string;
    skipped: SkippedRecipient[];
    smtpConfigured: boolean;
    deliveryWarning: string | null;
  } | null>(null);

  const [previewing, setPreviewing] = React.useState(false);
  const [sending, setSending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<SendResponse | null>(null);

  // Reset whenever the modal is reopened for a (possibly new) selection.
  React.useEffect(() => {
    if (!open) return;
    setPreviews(null);
    setIndex(0);
    setError(null);
    setResult(null);
    setSending(false);
    setPreviewing(false);
  }, [open, recipients]);

  const toLine = recipients.map((r) => r.name || r.email).join(", ");

  const manualReady =
    manualSubject.trim().length > 0 && manualBody.trim().length > 0;

  const requestBody = React.useCallback(
    () =>
      buildOutreachComposePayload(jobId, recipients, mode, {
        subject: manualSubject,
        body: manualBody,
      }),
    [jobId, recipients, mode, manualSubject, manualBody]
  );

  const runPreview = async () => {
    setPreviewing(true);
    setError(null);
    try {
      const res = await apiPost<PreviewResponse>("/outreach/preview", requestBody());
      setPreviews(res.recipients);
      setIndex(0);
      setMeta({
        company: res.company,
        jobTitle: res.job_title,
        skipped: res.skipped ?? [],
        smtpConfigured: res.smtp_configured !== false,
        deliveryWarning: res.delivery_warning ?? null,
      });
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setPreviewing(false);
    }
  };

  const send = async () => {
    setSending(true);
    setError(null);
    try {
      const res = await apiPost<SendResponse>("/outreach/send", {
        ...requestBody(),
        overrides: (previews ?? []).map((p) => ({
          link_id: p.link_id,
          subject: p.subject,
          body: p.body,
        })),
      });
      let delivery: DeliveryStatus | null = null;
      for (let attempt = 0; attempt < 80; attempt += 1) {
        delivery = await apiPost<DeliveryStatus>("/outreach/status", {
          task_ids: res.task_ids,
        });
        if (delivery.done) break;
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
      if (!delivery?.done) {
        throw new Error(
          "The emails are still queued. Delivery is taking longer than expected; check the audit log shortly."
        );
      }
      if (delivery.failed > 0) {
        throw new Error(
          `${delivery.failed} email${delivery.failed === 1 ? "" : "s"} failed to send. Check the delivery audit for the provider response.`
        );
      }
      setResult(res);
      onSent?.(delivery.sent);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setSending(false);
    }
  };

  const editPreview = (patch: Partial<ResolvedEmail>) => {
    setPreviews((prev) => {
      if (!prev) return prev;
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  };

  const current = previews?.[index] ?? null;
  const livePreview = React.useMemo(() => {
    const first = recipients[0];
    if (!first || !meta) return null;
    const ctx = {
      candidate_name: first.name || first.email,
      company: meta.company,
      job_title: meta.jobTitle,
    };
    return {
      subject: substitute(manualSubject, ctx),
      body: substitute(manualBody, ctx),
      who: ctx.candidate_name,
    };
  }, [recipients, manualSubject, manualBody, meta]);

  const busy = previewing || sending;

  return (
    <Dialog open={open} onOpenChange={(next) => (busy ? null : onOpenChange(next))}>
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Send email to selected candidates</DialogTitle>
          <DialogDescription>
            {recipients.length} recipient{recipients.length === 1 ? "" : "s"} ·
            To: {toLine || "-"}
          </DialogDescription>
        </DialogHeader>

        {/* ── Result state ───────────────────────────────────────────── */}
        {result ? (
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-md border border-foreground/20 bg-muted p-4">
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" />
              <div className="space-y-1">
                <p className="font-medium">
                  ✓ Sent to {result.queued} candidate
                  {result.queued === 1 ? "" : "s"}
                </p>
                <p className="text-sm">
                  Gmail confirmed delivery for every selected recipient.
                </p>
              </div>
            </div>
            {result.smtp_configured === false ? (
              <div className="flex items-start gap-3 rounded-md border border-dashed p-4">
                <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                <p className="text-sm">
                  {result.delivery_warning ??
                    "Email delivery is not configured on the server (SMTP credentials missing), so these emails will not actually reach candidates yet."}
                </p>
              </div>
            ) : null}
            {result.skipped && result.skipped.length > 0 ? (
              <div className="rounded-md border p-4">
                <p className="mb-2 text-sm font-medium">
                  {result.skipped.length} candidate
                  {result.skipped.length === 1 ? " was" : "s were"} skipped
                </p>
                <ul className="space-y-1 text-sm">
                  {result.skipped.map((s) => (
                    <li key={s.link_id}>
                      {s.name}, {s.reason}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-5">
            {error ? (
              <div
                role="alert"
                className="flex items-start gap-3 rounded-md border border-destructive/50 p-4 text-sm"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
            ) : null}

            {meta && !meta.smtpConfigured ? (
              <div className="flex items-start gap-3 rounded-md border border-dashed p-3 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{meta.deliveryWarning}</span>
              </div>
            ) : null}

            {/* ── Step 1: how should we reach out? ─────────────────────── */}
            <fieldset className="space-y-3" disabled={busy}>
              <legend className="mb-2 text-sm font-medium">
                How should we reach out?
              </legend>
              <label className="flex cursor-pointer items-start gap-3 rounded-md border p-3">
                <input
                  type="radio"
                  name="outreach-mode"
                  className="mt-1 h-4 w-4 accent-foreground"
                  checked={mode === "ai"}
                  onChange={() => {
                    setMode("ai");
                    setPreviews(null);
                  }}
                />
                <span>
                  <span className="block text-sm font-medium">
                    AI will personalize the email
                  </span>
                  <span className="block text-sm">
                    A 150–200 word email per candidate, using their name, the
                    job title and the company. You can edit every one before
                    sending.
                  </span>
                </span>
              </label>
              <label className="flex cursor-pointer items-start gap-3 rounded-md border p-3">
                <input
                  type="radio"
                  name="outreach-mode"
                  className="mt-1 h-4 w-4 accent-foreground"
                  checked={mode === "manual"}
                  onChange={() => {
                    setMode("manual");
                    setPreviews(null);
                  }}
                />
                <span>
                  <span className="block text-sm font-medium">
                    I&apos;ll write it myself
                  </span>
                  <span className="block text-sm">
                    One message, sent to everyone, with placeholders filled in
                    per candidate.
                  </span>
                </span>
              </label>
            </fieldset>

            {/* ── Manual composer ──────────────────────────────────────── */}
            {mode === "manual" ? (
              <div className="space-y-4">
                <FormField label="Subject" htmlFor="outreach-subject" required>
                  <Input
                    id="outreach-subject"
                    value={manualSubject}
                    disabled={busy}
                    onChange={(e) => {
                      setManualSubject(e.target.value);
                      setPreviews(null);
                    }}
                  />
                </FormField>
                <FormField label="Message" htmlFor="outreach-body" required>
                  <Textarea
                    id="outreach-body"
                    rows={10}
                    value={manualBody}
                    disabled={busy}
                    onChange={(e) => {
                      setManualBody(e.target.value);
                      setPreviews(null);
                    }}
                  />
                </FormField>
                <p className="text-xs">
                  Placeholders, replaced for each recipient:{" "}
                  <code>{"{{candidate_name}}"}</code>,{" "}
                  <code>{"{{company}}"}</code>, <code>{"{{job_title}}"}</code>
                  , <code>{"{{candidate_strengths}}"}</code>
                </p>
                {livePreview ? (
                  <div className="rounded-md border bg-muted/40 p-3">
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide">
                      Live preview, as {livePreview.who} will see it
                    </p>
                    <p className="text-sm font-medium">{livePreview.subject}</p>
                    <p className="mt-2 whitespace-pre-wrap text-sm">
                      {livePreview.body}
                    </p>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* ── Step 2: per-candidate preview ────────────────────────── */}
            {previews && previews.length > 0 && current ? (
              <div className="space-y-3 rounded-md border p-4">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {current.name}{" "}
                      <span>
                        ({current.email})
                      </span>
                    </p>
                    <p className="text-xs">
                      Recipient {index + 1} of {previews.length} · edits apply
                      to this candidate only
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {current.ai_fallback ? (
                      <Badge variant="outline" title="The AI service was unavailable; a standard template was used.">
                        template
                      </Badge>
                    ) : null}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={index === 0}
                      onClick={() => setIndex((i) => Math.max(0, i - 1))}
                      aria-label="Previous recipient"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={index >= previews.length - 1}
                      onClick={() =>
                        setIndex((i) => Math.min(previews.length - 1, i + 1))
                      }
                      aria-label="Next recipient"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                <FormField label="Subject" htmlFor="preview-subject">
                  <Input
                    id="preview-subject"
                    value={current.subject}
                    disabled={sending}
                    onChange={(e) => editPreview({ subject: e.target.value })}
                  />
                </FormField>
                <FormField label="Body" htmlFor="preview-body">
                  <Textarea
                    id="preview-body"
                    rows={12}
                    value={current.body}
                    disabled={sending}
                    onChange={(e) => editPreview({ body: e.target.value })}
                  />
                </FormField>
              </div>
            ) : null}

            {meta && meta.skipped.length > 0 && !result ? (
              <p className="text-sm">
                {meta.skipped.length} selected candidate
                {meta.skipped.length === 1 ? "" : "s"} will be skipped:{" "}
                {meta.skipped.map((s) => `${s.name} (${s.reason})`).join("; ")}
              </p>
            ) : null}

            <DialogFooter className="gap-2">
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="outline"
                className="gap-2"
                disabled={busy || (mode === "manual" && !manualReady)}
                onClick={() => void runPreview()}
              >
                <Eye className="h-4 w-4" />
                {previewing
                  ? "Generating"
                  : previews
                    ? "Regenerate preview"
                    : "Preview"}
              </Button>
              <span
                className="inline-flex"
                title={
                  !previews
                    ? "Generate the preview first so you can see exactly what each candidate receives"
                    : `Send to ${previews.length} candidate${
                        previews.length === 1 ? "" : "s"
                      }`
                }
              >
                <Button
                  type="button"
                  className="gap-2"
                  disabled={busy || !previews || previews.length === 0}
                  onClick={() => void send()}
                >
                  <Send className="h-4 w-4" />
                  {sending ? "Sending" : "Send"}
                </Button>
              </span>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
