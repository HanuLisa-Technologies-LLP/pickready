"use client";

// Drishti: one strategic profile per FUNCTION, maintained by its functional
// head, updatable any time (vivekium feature 1 under C3).
//
// TWO DOORS, ONE ARTIFACT. The guided conversation walks the five sections
// one question at a time, which is what the brief asks for; the form is the
// same five boxes for a head who would rather type. Both hand the same five
// strings to the same PUT, which is the only thing in the product that
// compiles a profile. Keeping the form is not a courtesy: a conversation
// that were the only door would make a twenty-minute interview the price of
// correcting one sentence.
//
// Nothing here compiles anything or invents a prompt. The section catalogue,
// every probe, the refusal sentence and the functional-head sentence are all
// server-authored, so the screen cannot say something the server would not.

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { apiGet, apiPost, apiPut } from "@/lib/api";

interface SectionDef {
  key: string;
  title: string;
  prompt: string;
}

interface ProfileOut {
  id: string;
  function_name: string;
  sections: Record<string, string>;
  critiques: Record<string, string[]>;
  functional_head_name: string | null;
  functional_head_title: string | null;
  /** Resource-scoped: whether THIS viewer is the head of record. */
  is_functional_head: boolean;
  functional_head_bound_at: string | null;
  updated_at: string | null;
}

interface TurnOut {
  section_key: string;
  agent_message: string;
  sections: Record<string, string>;
  turns_in_section: number;
  /** Set by the server. A deterministic probe is never dressed as a model's. */
  generated_by_ai: boolean;
  refused: string | null;
  finished: boolean;
}

interface Exchange {
  who: "agent" | "you";
  text: string;
  generated?: boolean;
}

const EMPTY_SECTIONS: Record<string, string> = {};

export default function DrishtiPage() {
  const { toast } = useToast();
  const [sections, setSections] = React.useState<SectionDef[]>([]);
  const [profiles, setProfiles] = React.useState<ProfileOut[]>([]);
  const [functionName, setFunctionName] = React.useState("");
  const [headTitle, setHeadTitle] = React.useState("");
  const [draft, setDraft] = React.useState<Record<string, string>>(EMPTY_SECTIONS);
  const [critiques, setCritiques] = React.useState<Record<string, string[]>>({});
  const [busy, setBusy] = React.useState(false);

  // The guided conversation. `turn` is null until it is opened, which is what
  // keeps the form the default door.
  const [turn, setTurn] = React.useState<TurnOut | null>(null);
  const [exchanges, setExchanges] = React.useState<Exchange[]>([]);
  const [answer, setAnswer] = React.useState("");
  const [thinking, setThinking] = React.useState(false);

  // The server's own sentence when this function belongs to somebody else,
  // rendered verbatim beside the control that gets past it. Held rather than
  // toasted because it is a decision the head has to make, not a notification.
  const [headChange, setHeadChange] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    const [meta, list] = await Promise.all([
      apiGet<{ sections: SectionDef[] }>("/drishti/sections"),
      apiGet<{ profiles: ProfileOut[] }>("/drishti/functions"),
    ]);
    setSections(meta.sections);
    setProfiles(list.profiles);
  }, []);

  React.useEffect(() => {
    void load().catch(() => undefined);
  }, [load]);

  const edit = (profile: ProfileOut) => {
    setFunctionName(profile.function_name);
    setHeadTitle(profile.functional_head_title ?? "");
    setDraft(profile.sections);
    setCritiques(profile.critiques);
    setHeadChange(null);
    setTurn(null);
    setExchanges([]);
  };

  const save = async (confirmHeadChange: boolean) => {
    setBusy(true);
    try {
      const saved = await apiPut<ProfileOut>("/drishti/functions", {
        function_name: functionName,
        functional_head_title: headTitle,
        functional_head_change_confirmed: confirmHeadChange,
        ...draft,
      });
      setCritiques(saved.critiques);
      setHeadChange(null);
      toast({ title: `Drishti saved for ${saved.function_name}` });
      await load();
    } catch (err) {
      // A 409 here is the functional-head binding, and the server wrote the
      // sentence. Rendering it verbatim is what stops the screen promising
      // something the server then refuses.
      const message = err instanceof Error ? err.message : "";
      if (message.includes("functional head")) {
        setHeadChange(message);
      } else {
        toast({
          title: "Could not save",
          description: message || undefined,
          variant: "destructive",
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const postTurn = async (payload: {
    section_key: string;
    sections: Record<string, string>;
    answer: string;
    turns_in_section: number;
  }) => {
    setThinking(true);
    try {
      const next = await apiPost<TurnOut>("/drishti/conversation/turn", {
        function_name: functionName,
        ...payload,
      });
      setTurn(next);
      // The captured sections ARE the form's draft. One state, so switching
      // door mid-profile never loses an answer and never produces two
      // versions of the same section.
      setDraft(next.sections);
      setExchanges((prior) => [
        ...prior,
        ...(next.refused
          ? []
          : payload.answer.trim()
            ? [{ who: "you" as const, text: payload.answer.trim() }]
            : []),
        {
          who: "agent" as const,
          text: next.agent_message,
          generated: next.generated_by_ai,
        },
      ]);
      setAnswer("");
    } catch (err) {
      toast({
        title: "Could not continue",
        description: err instanceof Error ? err.message : undefined,
        variant: "destructive",
      });
    } finally {
      setThinking(false);
    }
  };

  const startConversation = () =>
    postTurn({
      section_key: "",
      sections: draft,
      answer: "",
      turns_in_section: 0,
    });

  const reply = () => {
    if (!turn) return;
    void postTurn({
      section_key: turn.section_key,
      sections: turn.sections,
      answer,
      turns_in_section: turn.turns_in_section,
    });
  };

  const canName = functionName.trim().length >= 2;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Drishti, the function&apos;s strategic profile</CardTitle>
          <CardDescription>
            Once per function, by its functional head, in twenty to thirty
            minutes. It quietly informs every assessment for that function
            until you update it; without one, assessments run exactly as
            before. A leadership change does not update this by itself:
            record it here when it happens.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label htmlFor="fn" className="text-sm font-medium">
                Function
              </label>
              <Input
                id="fn"
                placeholder="Engineering"
                value={functionName}
                onChange={(e) => setFunctionName(e.target.value)}
              />
              <p className="text-xs">
                Matched against each job&apos;s department, so name it the way
                your jobs do.
              </p>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="head-title" className="text-sm font-medium">
                Your title
              </label>
              <Input
                id="head-title"
                placeholder="CTO"
                value={headTitle}
                onChange={(e) => setHeadTitle(e.target.value)}
              />
              <p className="text-xs">
                Recorded with the profile, so the function&apos;s strategic
                direction stays attributable to the person who set it.
              </p>
            </div>
          </div>

          {turn === null ? (
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="outline"
                disabled={!canName || thinking}
                onClick={() => void startConversation()}
              >
                Answer as a conversation
              </Button>
              <p className="text-xs">
                One question at a time, on any device. You can switch to the
                boxes below at any point and keep everything captured.
              </p>
            </div>
          ) : (
            <div className="space-y-3 rounded-md border p-4">
              <div className="space-y-2">
                {exchanges.map((exchange, i) => (
                  <p key={i} className="text-sm">
                    <span className="font-medium">
                      {exchange.who === "agent" ? "Drishti: " : "You: "}
                    </span>
                    {exchange.text}
                  </p>
                ))}
              </div>
              {turn.refused ? (
                <p className="text-sm font-medium">
                  Nothing was recorded from that reply.
                </p>
              ) : null}
              {turn.finished ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setTurn(null);
                    setExchanges([]);
                  }}
                >
                  Review the answers below
                </Button>
              ) : (
                <div className="space-y-2">
                  <Textarea
                    id="drishti-answer"
                    rows={4}
                    value={answer}
                    onChange={(e) => setAnswer(e.target.value)}
                    placeholder="Answer in your own words"
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button
                      disabled={thinking || answer.trim().length === 0}
                      onClick={reply}
                    >
                      {thinking ? "Thinking" : "Send"}
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => {
                        setTurn(null);
                        setExchanges([]);
                      }}
                    >
                      Switch to the boxes
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {sections.map((section) => (
            <div key={section.key} className="space-y-1.5">
              <label htmlFor={section.key} className="text-sm font-medium">
                {section.title}
              </label>
              <p className="text-xs">{section.prompt}</p>
              <Textarea
                id={section.key}
                rows={4}
                value={draft[section.key] ?? ""}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, [section.key]: e.target.value }))
                }
              />
              {(critiques[section.key] ?? []).map((probe, i) => (
                <p key={i} className="text-xs font-medium">
                  {probe}
                </p>
              ))}
            </div>
          ))}

          {headChange ? (
            <div className="space-y-2 rounded-md border p-4">
              <p className="text-sm font-medium">{headChange}</p>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => void save(true)}
              >
                Record the change of functional head and save
              </Button>
            </div>
          ) : null}

          <Button disabled={busy || !canName} onClick={() => void save(false)}>
            {busy ? "Saving" : "Save Drishti profile"}
          </Button>
        </CardContent>
      </Card>

      {profiles.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Functions with a profile</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {profiles.map((profile) => (
              <div
                key={profile.id}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div>
                  <p className="text-sm font-medium">{profile.function_name}</p>
                  <p className="text-xs">
                    {profile.functional_head_name
                      ? `${profile.functional_head_name}${
                          profile.functional_head_title
                            ? `, ${profile.functional_head_title}`
                            : ""
                        }`
                      : "No functional head recorded"}
                  </p>
                  <p className="text-xs">
                    {profile.updated_at
                      ? `Updated ${new Date(profile.updated_at).toLocaleDateString()}`
                      : "Saved"}
                  </p>
                </div>
                <Button size="sm" variant="outline" onClick={() => edit(profile)}>
                  {profile.is_functional_head ? "Edit" : "Open"}
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
