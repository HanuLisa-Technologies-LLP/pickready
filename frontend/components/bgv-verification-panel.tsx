"use client";

// The recruiter's background-verification panel: one row per employer, and the
// two buttons that decide whether an offer can go out.
//
// WHY THE BLOCKED REASON IS RENDERED FROM THE SERVER
// `offer_blocked_reason` is the exact sentence `apply_transition` would refuse
// an offer with. Rendering the server's own words means the screen can never
// promise something the pipeline will then refuse, which is the specific way a
// gate becomes infuriating: a button that looks available and is not.
//
// WHY THE DRAFT IS EDITABLE AND THE SEND USES WHAT IS ON SCREEN
// The BGV Agent writes the first version; the recruitment team owns the one
// that goes out. `generated_by_ai` is shown, because template output presented
// as generation is a lie about how the text was produced, and a recruiter
// editing a fallback template deserves to know that is what they have.
//
// WHY VERIFIED AND NOT VERIFIED ARE BOTH DELIBERATE ACTIONS
// Nothing infers a result from the reply. A person reads the employer's answer
// and says which it was, which is what the brief asks for and what keeps a
// hiring decision out of a language model.

import * as React from "react";
import {
  CheckCircle2,
  Mail,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import { ConversationPanel } from "@/components/conversation-panel";
import { apiGet, apiPost } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import { useToast } from "@/components/ui/toast";

interface Employment {
  id: string;
  employer_name: string;
  designation: string;
  started_on: string;
  ended_on: string;
  hr_name: string;
  hr_email: string | null;
}

interface Verification {
  id: string;
  employment: Employment;
  status: "not_started" | "pending" | "verified" | "not_verified";
  conversation_id: string | null;
  first_sent_at: string | null;
  responded_at: string | null;
  decided_at: string | null;
  decided_by_name: string | null;
  decision_note: string | null;
}

interface CandidateBGV {
  candidate_id: string;
  candidate_name: string | null;
  background: string | null;
  required: boolean;
  status: string;
  employer_count: number;
  verified_count: number;
  verifications: Verification[];
  offer_blocked_reason: string | null;
}

interface Draft {
  verification_id: string;
  subject: string;
  body: string;
  generated_by_ai: boolean;
}

/** Words, never a count dressed as a score. */
const STATUS_WORD: Record<string, string> = {
  not_required: "Not required",
  not_started: "Not started",
  pending: "In progress",
  verified: "Verified",
  not_verified: "Not verified",
};

export function BgvVerificationPanel({ candidateId }: { candidateId: string }) {
  const { toast } = useToast();
  const [data, setData] = React.useState<CandidateBGV | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [notes, setNotes] = React.useState<Record<string, string>>({});

  const load = React.useCallback(async () => {
    try {
      setData(await apiGet<CandidateBGV>(`/bgv/candidates/${candidateId}`));
      setLoadError(null);
    } catch (error) {
      setData(null);
      setLoadError(apiErrorMessage(error));
    }
  }, [candidateId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
      await load();
    } catch (error) {
      toast({ title: apiErrorMessage(error), variant: "destructive" });
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Background verification</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-sm">{loadError}</p>
          <Button size="sm" variant="outline" onClick={() => void load()}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Background verification</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm">Loading.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {data.status === "verified" ? (
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            ) : (
              <ShieldAlert className="h-4 w-4" aria-hidden="true" />
            )}
            <CardTitle className="text-base">Background verification</CardTitle>
          </div>
          <span className="text-sm font-medium">
            {STATUS_WORD[data.status] ?? data.status}
          </span>
        </div>
        <CardDescription>
          {data.required
            ? `${data.verified_count} of ${data.employer_count} previous employers verified.`
            : "This candidate declared no previous employment, so verification is not required."}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* The server's own refusal sentence. The screen never invents one. */}
        {data.offer_blocked_reason ? (
          <p className="rounded-md border p-3 text-sm">{data.offer_blocked_reason}</p>
        ) : data.required ? (
          <p className="flex items-center gap-2 rounded-md border p-3 text-sm">
            <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
            Every submitted employer is verified. An offer can be extended.
          </p>
        ) : null}

        {data.required && data.verifications.length === 0 ? (
          <p className="text-sm">
            This candidate has not submitted their employment details yet.
            Verification opens once they do.
          </p>
        ) : null}

        {data.required &&
        data.verifications.length > 0 &&
        data.verifications.every((item) => item.status === "not_started") ? (
          <Button
            size="sm"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await apiPost(`/bgv/candidates/${candidateId}/initiate`, {});
              })
            }
          >
            Start verification for all employers
          </Button>
        ) : null}

        <ul className="space-y-3">
          {data.verifications.map((item) => (
            <li key={item.id} className="space-y-3 rounded-md border p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-medium">
                    {item.employment.designation} at {item.employment.employer_name}
                  </p>
                  <p className="text-xs">
                    {item.employment.started_on} to {item.employment.ended_on}
                  </p>
                  <p className="text-xs">
                    HR contact: {item.employment.hr_name}
                    {item.employment.hr_email ? ` (${item.employment.hr_email})` : ""}
                  </p>
                </div>
                <span className="text-xs font-medium">
                  {STATUS_WORD[item.status] ?? item.status}
                </span>
              </div>

              {item.decided_at ? (
                <p className="text-xs">
                  Marked {STATUS_WORD[item.status]?.toLowerCase()} by{" "}
                  {item.decided_by_name ?? "a team member"} on{" "}
                  {new Date(item.decided_at).toLocaleDateString()}.
                  {item.decision_note ? ` ${item.decision_note}` : ""}
                </p>
              ) : null}

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || item.status === "not_started"}
                  onClick={() =>
                    void run(async () => {
                      setDraft(
                        await apiPost<Draft>(
                          `/bgv/verifications/${item.id}/draft`,
                          {},
                        ),
                      );
                    })
                  }
                >
                  <Mail className="h-3.5 w-3.5" aria-hidden="true" />
                  <span className="ml-1">
                    {item.first_sent_at
                      ? "Write another message"
                      : "Write the request"}
                  </span>
                </Button>

                {item.status === "pending" || item.status === "not_verified" ? (
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await apiPost(`/bgv/verifications/${item.id}/decision`, {
                          verified: true,
                          note: notes[item.id] || null,
                        });
                      })
                    }
                  >
                    Mark verified
                  </Button>
                ) : null}

                {item.status === "pending" || item.status === "verified" ? (
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await apiPost(`/bgv/verifications/${item.id}/decision`, {
                          verified: false,
                          note: notes[item.id] || null,
                        });
                      })
                    }
                  >
                    Mark not verified
                  </Button>
                ) : null}
              </div>

              {item.status === "pending" ? (
                <FormField
                  label="Note for the record"
                  htmlFor={`note-${item.id}`}
                  hint="What the employer said, in your words. Optional, and kept with the decision."
                >
                  <Input
                    id={`note-${item.id}`}
                    value={notes[item.id] ?? ""}
                    disabled={busy}
                    onChange={(event) =>
                      setNotes({ ...notes, [item.id]: event.target.value })
                    }
                  />
                </FormField>
              ) : null}

              {/* The employer exchange itself, READ ONLY. A reply that lands
                  on the inbound webhook appears here, which is the point: a
                  recruiter decides verified or not verified from what the
                  employer actually wrote, on the same screen as the buttons.
                  Sending is refused from a chat box by the server, so the
                  compose box is hidden rather than rendered and then refused. */}
              {item.conversation_id ? (
                <ConversationPanel
                  conversationId={item.conversation_id}
                  readOnly
                  emptyCopy="Nothing sent to this employer yet."
                />
              ) : null}

              {draft && draft.verification_id === item.id ? (
                <div className="space-y-3 rounded-md border p-3">
                  <p className="text-xs font-medium">
                    {draft.generated_by_ai
                      ? "Drafted by the BGV agent from this candidate's submitted details. Edit anything before sending."
                      : "Written from the standard template, because the drafting model was unavailable. Edit anything before sending."}
                  </p>
                  <FormField label="Subject" htmlFor={`subject-${item.id}`} required>
                    <Input
                      id={`subject-${item.id}`}
                      value={draft.subject}
                      disabled={busy}
                      onChange={(event) =>
                        setDraft({ ...draft, subject: event.target.value })
                      }
                    />
                  </FormField>
                  <FormField label="Message" htmlFor={`body-${item.id}`} required>
                    <Textarea
                      id={`body-${item.id}`}
                      rows={12}
                      value={draft.body}
                      disabled={busy}
                      onChange={(event) =>
                        setDraft({ ...draft, body: event.target.value })
                      }
                    />
                  </FormField>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      disabled={busy || !draft.subject.trim() || !draft.body.trim()}
                      onClick={() =>
                        void run(async () => {
                          await apiPost(`/bgv/verifications/${item.id}/send`, {
                            subject: draft.subject,
                            body: draft.body,
                          });
                          setDraft(null);
                          toast({
                            title: `Verification request sent to ${item.employment.hr_name}`,
                          });
                        })
                      }
                    >
                      Send to {item.employment.hr_name}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          setDraft(
                            await apiPost<Draft>(
                              `/bgv/verifications/${item.id}/draft`,
                              {},
                            ),
                          );
                        })
                      }
                    >
                      <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                      <span className="ml-1">Rewrite</span>
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => setDraft(null)}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
