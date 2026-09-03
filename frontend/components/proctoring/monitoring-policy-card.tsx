"use client";

// The recruiter's one monitoring setting (proctoring spec section 6).
//
// ONE QUESTION, SET ONCE PER JOB, applied to every candidate on it. The
// label, the help text and the two option labels below are the
// specification's words verbatim, exported so a test asserts what a recruiter
// reads rather than a paraphrase, and so a change to them is a change to one
// file.
//
// The default is "let them finish". Never terminate by default without an
// explicit choice: repeated warnings are not the same thing as a clear-cut
// case, and a job nobody answered this question for must not be the strict
// one.
//
// It moves no score. Nothing in the report and nothing here feeds the grade,
// the ranking or the matrix; this decides only what happens at the third
// warning during the assessment itself.

import * as React from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { useToast } from "@/components/ui/toast";
import { apiGet, apiPatch } from "@/lib/api";
import type { Job, ProctoringWarningPolicy } from "@/lib/types";

export const MONITORING_CARD_TITLE = "Assessment monitoring";

export const MONITORING_FIELD_LABEL =
  "If a candidate crosses the warning limit during their assessment, what should happen?";

export const MONITORING_HELP_TEXT =
  "Some candidates may trigger repeated warnings during an assessment, for example leaving the " +
  "screen, or a phone or another person appearing on camera, without it being a clear-cut case " +
  "of misconduct. Choose how you would like this handled.";

export const MONITORING_OPTIONS: Array<{
  value: ProctoringWarningPolicy;
  label: string;
  description: string;
}> = [
  {
    value: "terminate",
    label: "Stop the assessment",
    description: "The candidate is removed immediately and this is noted in their report.",
  },
  {
    value: "continue_and_note",
    label: "Let them finish, just note it",
    description:
      "The assessment continues and the report clearly notes that the warning limit was crossed.",
  },
];

export const DEFAULT_MONITORING_POLICY: ProctoringWarningPolicy = "continue_and_note";

export function MonitoringPolicyCard({ jobId }: { jobId: string }) {
  const { toast } = useToast();
  const [policy, setPolicy] = React.useState<ProctoringWarningPolicy | null>(null);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    // The value is read from the job itself rather than held in this
    // component's own state across mounts, so two people with the page open
    // never disagree about what the job is set to.
    apiGet<Job>(`/jobs/${jobId}`)
      .then((job) => {
        if (!cancelled) setPolicy(job.proctoring_warning_policy ?? DEFAULT_MONITORING_POLICY);
      })
      .catch(() => {
        // The job read failed. The control shows the product default, which
        // is also what the server applies to a job nobody answered for, so
        // the screen and the behaviour agree; saving writes it explicitly.
        if (!cancelled) setPolicy(DEFAULT_MONITORING_POLICY);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const save = async (next: ProctoringWarningPolicy) => {
    const previous = policy;
    setPolicy(next);
    setSaving(true);
    try {
      await apiPatch<Job>(`/jobs/${jobId}`, { proctoring_warning_policy: next });
      toast({ title: "Monitoring setting saved" });
    } catch (error) {
      setPolicy(previous);
      toast({
        title: "Couldn't save the monitoring setting",
        description: error instanceof Error ? error.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{MONITORING_CARD_TITLE}</CardTitle>
        <CardDescription>
          Every assessment for this job is monitored: camera, microphone and screen activity, with
          nothing recorded or stored. This setting decides one thing.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <p className="text-sm font-medium">{MONITORING_FIELD_LABEL}</p>
          <p className="mt-1 text-xs leading-5">{MONITORING_HELP_TEXT}</p>
        </div>
        <RadioGroup
          value={policy ?? DEFAULT_MONITORING_POLICY}
          disabled={saving || policy === null}
          onValueChange={(value) => void save(value as ProctoringWarningPolicy)}
          aria-label={MONITORING_FIELD_LABEL}
        >
          {MONITORING_OPTIONS.map((option) => (
            <div key={option.value} className="flex items-start gap-3 border border-border p-3">
              <RadioGroupItem
                value={option.value}
                id={`monitoring-${option.value}`}
                className="mt-1"
              />
              <div className="min-w-0">
                <Label htmlFor={`monitoring-${option.value}`} className="cursor-pointer">
                  {option.label}
                </Label>
                <p className="mt-1 text-xs leading-5">{option.description}</p>
              </div>
            </div>
          ))}
        </RadioGroup>
      </CardContent>
    </Card>
  );
}
