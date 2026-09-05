"use client";

// Data retention choices on My Profile (Consent & Privacy spec, 2026-09-05).
//
// Two consents, each answering one plain question: may ReadyPick keep this
// record for FUTURE jobs, or for this job only? A choice that was never made
// is treated exactly like "this job only", the safe direction, and the card
// says so rather than letting an off toggle silently stand in for an answer.
//
// Saving is per toggle: flipping one never rewrites the other's timestamp.

import * as React from "react";
import { ShieldCheck } from "lucide-react";

import { apiGet, apiPut } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { useToast } from "@/components/ui/toast";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface RetentionConsents {
  retain_assessment: boolean | null;
  retain_assessment_updated_at: string | null;
  retain_video: boolean | null;
  retain_video_updated_at: string | null;
}

type ConsentKey = "retain_assessment" | "retain_video";

const CHOICES: Array<{
  key: ConsentKey;
  title: string;
  question: string;
  yesMeans: string;
  noMeans: string;
}> = [
  {
    key: "retain_assessment",
    title: "Assessment record",
    question: "Keep my completed assessment for future jobs?",
    yesMeans:
      "Yes: your assessment stays on file and employers you apply to later can download the report.",
    noMeans:
      "No: your assessment is used for this job only. Employers can view the report on screen but cannot download a copy.",
  },
  {
    key: "retain_video",
    title: "Video record",
    question: "Keep my video record for future jobs?",
    yesMeans:
      "Yes: any video recorded with your consent stays on file for jobs you apply to later.",
    noMeans:
      "No: any video recorded with your consent is used for this job only and cannot be downloaded.",
  },
];

export function DataRetentionCard() {
  const { toast } = useToast();
  const [consents, setConsents] = React.useState<RetentionConsents | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState<ConsentKey | null>(null);

  React.useEffect(() => {
    apiGet<RetentionConsents>("/portal/me/retention-consents")
      .then(setConsents)
      .catch((error) => setLoadError(apiErrorMessage(error)))
      .finally(() => setLoading(false));
  }, []);

  const save = async (key: ConsentKey, value: boolean) => {
    setSaving(key);
    try {
      const updated = await apiPut<RetentionConsents>(
        "/portal/me/retention-consents",
        { [key]: value }
      );
      setConsents(updated);
      toast({
        title: "Choice saved",
        description: value
          ? "Kept for future jobs."
          : "This job only.",
      });
    } catch (error) {
      const message = apiErrorMessage(error);
      toast({
        title: "Choice not saved",
        description: message,
        variant: "destructive",
      });
    } finally {
      setSaving(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5" aria-hidden />
          Data retention choices
        </CardTitle>
        <CardDescription>
          You decide whether your records stay available for future jobs or are
          used for this job only. You can change either choice at any time.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {loading ? (
          <p role="status" className="text-sm">
            Checking your choices
          </p>
        ) : loadError ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {loadError}
          </p>
        ) : consents ? (
          CHOICES.map(({ key, title, question, yesMeans, noMeans }) => {
            const value = consents[key];
            return (
              <div key={key} className="rounded-md border p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm font-semibold">{title}</p>
                    <p className="mt-0.5 text-sm">{question}</p>
                  </div>
                  <Switch
                    checked={value === true}
                    disabled={saving !== null}
                    onCheckedChange={(next) => void save(key, next)}
                    aria-label={question}
                  />
                </div>
                <p className="mt-2 text-xs">{value === true ? yesMeans : noMeans}</p>
                {value === null ? (
                  <p className="mt-1 text-xs font-medium">
                    You have not chosen yet. Until you do, the safer choice
                    applies: this job only.
                  </p>
                ) : null}
              </div>
            );
          })
        ) : null}
      </CardContent>
    </Card>
  );
}
