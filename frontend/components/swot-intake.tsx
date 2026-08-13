"use client";

// The Reporting Authority SWOT Intake (spec 5.1).
//
// A short conversation with the person the role reports to, run at every job
// setup and at every grade, BEFORE the PPI matrix is generated. Four areas:
// Strengths, Weaknesses, Opportunities, Threats, all about the ROLE rather
// than about any candidate.
//
// WHY THE CAPTURED POINTS ARE SHOWN BESIDE THE QUESTION
// -----------------------------------------------------
// The hiring manager is a busy person doing an unpaid step in their own hiring
// process, and a chat box with no visible end is the shape of thing they
// abandon. Showing the four areas filling up, with a "1 of 4" counter, is what
// makes a four-question conversation feel finite. An abandoned intake strands
// the job, so this is not decoration.
//
// The panel does NOT block the rest of the setup screen. The intake is an INPUT
// to the matrix, not a gate of its own: an intake nobody completed already
// shows up as a matrix nobody approved.

import * as React from "react";
import { Loader2, MessageSquare } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const BASE = "/api/v2/assessments/jobs";

const AREA_ORDER = ["strengths", "weaknesses", "opportunities", "threats"] as const;

const AREA_LABEL: Record<string, string> = {
  strengths: "Strengths",
  weaknesses: "Weaknesses",
  opportunities: "Opportunities",
  threats: "Threats",
};

export interface SwotIntake {
  job_id: string;
  status: string;
  complete: boolean;
  current_area: string | null;
  current_area_label: string | null;
  prompt: string | null;
  captured: Record<string, string[]>;
  areas_total: number;
  areas_done: number;
}

function errorMessage(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail ? detail : fallback;
}

export function SwotIntakePanel({ jobId }: { jobId: string }) {
  const { toast } = useToast();
  const [intake, setIntake] = React.useState<SwotIntake | null>(null);
  const [answer, setAnswer] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      setIntake(await apiGet<SwotIntake>(`${BASE}/${jobId}/swot`));
    } catch {
      // Degrades to absent rather than taking the setup screen down with it.
      // The matrix half of this screen is what actually gates candidates.
      setIntake(null);
    }
    setLoading(false);
  }, [jobId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const submit = React.useCallback(async () => {
    const text = answer.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      const next = await apiPost<SwotIntake>(`${BASE}/${jobId}/swot/respond`, {
        answer: text,
      });
      setIntake(next);
      setAnswer("");
      if (next.complete) {
        toast({
          title: "SWOT intake complete",
          description:
            "The evaluation matrix is being rewritten with what you described.",
        });
      }
    } catch (error) {
      toast({
        title: "Couldn't record that answer",
        description: errorMessage(error, "Please try again."),
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  }, [answer, busy, jobId, toast]);

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Loading the role intake
        </CardContent>
      </Card>
    );
  }

  if (!intake) return null;

  const captured = AREA_ORDER.map((area) => ({
    area,
    label: AREA_LABEL[area],
    points: intake.captured?.[area] ?? [],
  }));

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4" aria-hidden />
            Role intake
          </CardTitle>
          {intake.complete ? (
            <Badge variant="secondary">Complete</Badge>
          ) : (
            <Badge variant="outline">
              {Math.min(intake.areas_done + 1, intake.areas_total)} of{" "}
              {intake.areas_total}
            </Badge>
          )}
        </div>
        <CardDescription>
          A short conversation with the person this role reports to, about what
          the ROLE demands. It shapes the evaluation matrix below, so answer it
          before saving that. Nothing here is about any candidate.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {intake.complete ? (
          <p className="rounded-md border bg-muted/30 p-3 text-sm">
            Thank you. What you described has been fed into the evaluation
            matrix below, which is generated from the job description and this
            intake together. Review it and save it when it looks right.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="rounded-md border bg-muted/30 p-4">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
                {intake.current_area_label}
              </p>
              <p className="leading-7">{intake.prompt}</p>
            </div>
            <Textarea
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              rows={4}
              placeholder="Describe what someone would actually be seen doing, deciding, or failing to do."
              aria-label="Your answer"
            />
            <div className="flex items-center gap-3">
              <Button disabled={busy || !answer.trim()} onClick={() => void submit()}>
                {busy ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : null}
                Send
              </Button>
              <p className="text-xs">
                Concrete beats general. &quot;Wasn&apos;t sharp enough&quot;
                describes an impression; what they did or failed to do is what
                shapes a fair assessment.
              </p>
            </div>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {captured.map((group) => (
            <div key={group.area} className="rounded-md border p-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide">
                {group.label}
              </p>
              {group.points.length === 0 ? (
                <p className="text-xs">Nothing captured yet.</p>
              ) : (
                <ul className="list-disc space-y-1 pl-4 text-sm">
                  {group.points.map((point, index) => (
                    <li key={`${group.area}-${index}`}>{point}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
