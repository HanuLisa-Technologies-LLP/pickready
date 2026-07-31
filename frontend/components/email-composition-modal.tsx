"use client";

// Email composition (spec §6.2). Two branches:
//
//   AI drafts, personalised per candidate, one draft each, referencing that
//   person's own evidence. The recruiter steps through them and edits any.
//
//   Write it myself, one template, sent to everyone selected, with
//   {{candidate_name}} / {{job_title}} / {{company}} placeholders substituted
//   per recipient at send time.
//
// Nothing sends until the recruiter presses Send. Every message is recorded in
// the email log with whether a human edited it.

import * as React from "react";
import { Loader2, Send, Sparkles, PenLine, AlertTriangle } from "lucide-react";

import { apiPost } from "@/lib/api";
import {
  EMAIL_TYPE_LABELS,
  EMAIL_TYPES,
  type EmailDraft,
  type EmailDraftsResponse,
  type EmailType,
  type RankedCandidate,
} from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Types a recruiter can send by hand. The two automatic ones fire on an
 *  event (application received, assessment unfinished) and the internal
 *  reminder targets a colleague, so none of them belong on this menu. */
const SELECTABLE_TYPES: EmailType[] = ["shortlist", "rejected", "hold"];

type Mode = "ai" | "manual";

interface Editable extends EmailDraft {
  /** The AI's original text, so an edit can be detected rather than assumed. */
  originalSubject: string;
  originalBody: string;
}

function substitute(
  template: string,
  row: { full_name: string },
  jobTitle: string,
  company: string
): string {
  return template
    .replaceAll("{{candidate_name}}", row.full_name)
    .replaceAll("{{job_title}}", jobTitle)
    .replaceAll("{{company}}", company);
}

export function EmailCompositionModal({
  open,
  onOpenChange,
  candidates,
  jobTitle,
  companyName,
  onSent,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  candidates: RankedCandidate[];
  jobTitle: string;
  companyName: string;
  onSent?: () => void;
}) {
  const { toast } = useToast();
  const [mode, setMode] = React.useState<Mode>("ai");
  const [emailType, setEmailType] = React.useState<EmailType>("shortlist");
  const [drafting, setDrafting] = React.useState(false);
  const [sending, setSending] = React.useState(false);
  const [drafts, setDrafts] = React.useState<Editable[]>([]);
  const [skipped, setSkipped] = React.useState<EmailDraftsResponse["skipped"]>([]);
  const [index, setIndex] = React.useState(0);
  const [manual, setManual] = React.useState({
    subject: "",
    body:
      "Hi {{candidate_name}},\n\n" +
      "Thank you for your interest in the {{job_title}} role at {{company}}.\n\n" +
      ", The {{company}} team",
  });

  // Reset whenever the modal opens on a new selection, so a previous batch's
  // drafts can never be sent to this one's recipients.
  React.useEffect(() => {
    if (!open) return;
    setDrafts([]);
    setSkipped([]);
    setIndex(0);
    setMode("ai");
  }, [open, candidates]);

  const generate = async () => {
    setDrafting(true);
    try {
      const res = await apiPost<EmailDraftsResponse>("/emails/draft", {
        email_type: emailType,
        link_ids: candidates.map((c) => c.link_id),
      });
      setDrafts(
        res.drafts.map((d) => ({
          ...d,
          originalSubject: d.subject,
          originalBody: d.body,
        }))
      );
      setSkipped(res.skipped ?? []);
      setIndex(0);
      if (res.drafts.length === 0) {
        toast({
          title: "Nothing to draft",
          description: "None of the selected candidates has an email address on file.",
          variant: "destructive",
        });
      }
    } catch (e) {
      toast({
        title: "Couldn't draft the emails",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setDrafting(false);
    }
  };

  const send = async () => {
    const messages =
      mode === "ai"
        ? drafts.map((d) => ({
            link_id: d.link_id,
            subject: d.subject,
            body: d.body,
            edited_by_human:
              d.subject !== d.originalSubject || d.body !== d.originalBody,
            generated_by_ai: d.generated_by_ai,
          }))
        : candidates.map((c) => ({
            link_id: c.link_id,
            subject: substitute(manual.subject, c, jobTitle, companyName),
            body: substitute(manual.body, c, jobTitle, companyName),
            // Written by a person start to finish.
            edited_by_human: true,
            generated_by_ai: false,
          }));

    if (messages.length === 0) return;
    setSending(true);
    try {
      const res = await apiPost<{ queued: number; skipped: unknown[] }>(
        "/emails/send",
        { email_type: emailType, messages }
      );
      toast({
        title: `${res.queued} email${res.queued === 1 ? "" : "s"} queued`,
        description:
          res.skipped.length > 0
            ? `${res.skipped.length} skipped, see the email log for details.`
            : "Delivery status appears in the email log.",
      });
      onOpenChange(false);
      onSent?.();
    } catch (e) {
      toast({
        title: "Send failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSending(false);
    }
  };

  const current = drafts[index];
  const updateCurrent = (patch: Partial<Editable>) =>
    setDrafts((prev) =>
      prev.map((d, i) => (i === index ? { ...d, ...patch } : d))
    );

  const manualValid = manual.subject.trim().length > 0 && manual.body.trim().length > 0;
  const canSend = mode === "ai" ? drafts.length > 0 : manualValid;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            Email {candidates.length} candidate{candidates.length === 1 ? "" : "s"}
          </DialogTitle>
          <DialogDescription>
            Nothing is sent until you press Send. Every message is recorded in the
            email log.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <FormField label="Email type" htmlFor="email-type">
            <Select
              value={emailType}
              onValueChange={(v) => {
                setEmailType(v as EmailType);
                setDrafts([]);
              }}
            >
              <SelectTrigger id="email-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SELECTABLE_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {EMAIL_TYPE_LABELS[t]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>

          <div className="flex gap-2">
            <Button
              type="button"
              variant={mode === "ai" ? "default" : "outline"}
              size="sm"
              className="gap-1.5"
              onClick={() => setMode("ai")}
            >
              <Sparkles className="h-4 w-4" /> AI drafts, one per candidate
            </Button>
            <Button
              type="button"
              variant={mode === "manual" ? "default" : "outline"}
              size="sm"
              className="gap-1.5"
              onClick={() => setMode("manual")}
            >
              <PenLine className="h-4 w-4" /> I&apos;ll write it myself
            </Button>
          </div>

          {mode === "ai" ? (
            drafts.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-center">
                <p className="mb-3 text-sm">
                  Each candidate gets their own email, written from the evidence in
                  their assessment.
                </p>
                <Button
                  onClick={() => void generate()}
                  disabled={drafting}
                  className="gap-1.5"
                >
                  {drafting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Sparkles className="h-4 w-4" />
                  )}
                  {drafting ? "Drafting" : "Generate drafts"}
                </Button>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    {current?.candidate_name || current?.recipient_email}, draft{" "}
                    {index + 1} of {drafts.length}
                  </p>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={index === 0}
                      onClick={() => setIndex((i) => i - 1)}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={index >= drafts.length - 1}
                      onClick={() => setIndex((i) => i + 1)}
                    >
                      Next
                    </Button>
                  </div>
                </div>

                {current && !current.generated_by_ai ? (
                  <p className="flex items-start gap-1.5 rounded-md border border-amber-600 bg-amber-50 p-2 text-xs text-amber-950 dark:bg-amber-950/30 dark:text-amber-100">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    The AI service was unavailable, so this is a standard template.
                    Read it before sending.
                  </p>
                ) : null}

                <FormField label="Subject" htmlFor="draft-subject" required>
                  <Input
                    id="draft-subject"
                    value={current?.subject ?? ""}
                    onChange={(e) => updateCurrent({ subject: e.target.value })}
                  />
                </FormField>
                <FormField
                  label="Body"
                  htmlFor="draft-body"
                  required
                  hint={`${(current?.body ?? "").trim().split(/\s+/).filter(Boolean).length} words · ${(current?.body ?? "").length} characters`}
                >
                  <Textarea
                    id="draft-body"
                    rows={14}
                    value={current?.body ?? ""}
                    onChange={(e) => updateCurrent({ body: e.target.value })}
                  />
                </FormField>
              </div>
            )
          ) : (
            <div className="space-y-3">
              <p className="text-xs">
                Placeholders <code>{"{{candidate_name}}"}</code>,{" "}
                <code>{"{{job_title}}"}</code> and <code>{"{{company}}"}</code> are
                filled in for each recipient.
              </p>
              <FormField label="Subject" htmlFor="manual-subject" required>
                <Input
                  id="manual-subject"
                  value={manual.subject}
                  onChange={(e) => setManual({ ...manual, subject: e.target.value })}
                />
              </FormField>
              <FormField
                label="Body"
                htmlFor="manual-body"
                required
                hint={`${manual.body.trim().split(/\s+/).filter(Boolean).length} words · ${manual.body.length} characters`}
              >
                <Textarea
                  id="manual-body"
                  rows={14}
                  value={manual.body}
                  onChange={(e) => setManual({ ...manual, body: e.target.value })}
                />
              </FormField>
            </div>
          )}

          {skipped.length > 0 ? (
            <div className="rounded-md border border-dashed p-3 text-xs">
              <p className="mb-1 font-medium">
                {skipped.length} candidate{skipped.length === 1 ? "" : "s"} will be
                skipped:
              </p>
              <ul className="space-y-0.5">
                {skipped.map((s) => (
                  <li key={s.link_id}>
                    <Badge variant="secondary" className="mr-1.5">
                      {s.name ?? "Unknown"}
                    </Badge>
                    {s.reason}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSend || sending} onClick={() => void send()} className="gap-1.5">
            {sending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            {sending
              ? "Sending"
              : `Send ${mode === "ai" ? drafts.length : candidates.length}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
