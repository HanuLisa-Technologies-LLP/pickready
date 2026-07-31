"use client";

// Company Profile (spec §3.2), the page formerly called Settings.
//
// These three sections are the company's own words about itself, and every NEW
// job snapshots them into its JD, so editing here reaches future jobs only.
// That behaviour used to be spelled out in a paragraph ON the page; the client
// named it as copy that explains the implementation rather than the task, so it
// now lives here in the source and the save toast states the consequence once.

import * as React from "react";
import { Loader2, Save } from "lucide-react";

import { apiGet, apiPatch } from "@/lib/api";
import type { CompanyProfile } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import { LoadingRows, Section } from "@/components/page-primitives";

type Draft = { about_company: string; work_life: string; benefits: string };

const EMPTY: Draft = { about_company: "", work_life: "", benefits: "" };

const SECTIONS: {
  key: keyof Draft;
  label: string;
  hint: string;
  placeholder: string;
}[] = [
  {
    key: "about_company",
    label: "About company",
    hint: "What the company does, who it serves, and what stage it is at.",
    placeholder: "We build",
  },
  {
    key: "work_life",
    label: "Work life",
    hint: "How people actually work here, hours, location, rhythm, autonomy.",
    placeholder: "Remote-first, async-friendly…",
  },
  {
    key: "benefits",
    label: "Benefits",
    hint: "What the company offers beyond salary.",
    placeholder: "Health cover, learning budget…",
  },
];

export default function CompanyProfilePage() {
  const { toast } = useToast();
  const { hasCapability } = useAuth();
  const canEdit = hasCapability("edit_company_profile");

  const [profile, setProfile] = React.useState<CompanyProfile | null>(null);
  const [draft, setDraft] = React.useState<Draft>(EMPTY);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<CompanyProfile>("/companies/me/profile");
      setProfile(res);
      setDraft({
        about_company: res.about_company ?? "",
        work_life: res.work_life ?? "",
        benefits: res.benefits ?? "",
      });
    } catch (e) {
      toast({
        title: "Couldn't load the company profile",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      // Empty string -> null so a cleared section is genuinely cleared rather
      // than stored as a blank paragraph that renders an empty heading.
      const res = await apiPatch<CompanyProfile>("/companies/me/profile", {
        about_company: draft.about_company.trim() || null,
        work_life: draft.work_life.trim() || null,
        benefits: draft.benefits.trim() || null,
      });
      setProfile(res);
      toast({
        title: "Company profile saved",
        description: "New jobs will use this text. Existing jobs are unchanged.",
      });
    } catch (e) {
      toast({
        title: "Save failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const min = profile?.recommended_min_chars ?? 500;
  const max = profile?.recommended_max_chars ?? 1000;

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Company Profile"
        description={
          profile
            ? [profile.company_name, profile.industry].filter(Boolean).join(" · ")
            : undefined
        }
      />

      <Section
        title="Job description sections"
        className="max-w-3xl"
        contentClassName="space-y-6"
      >
          {loading ? (
            <LoadingRows rows={3} label="Loading company profile" />
          ) : (
            <>
              {SECTIONS.map((section) => {
                const value = draft[section.key];
                const length = value.trim().length;
                // Advisory only: a short or long paragraph still saves. The
                // API does not reject either, so the UI must not block it.
                const outsideGuide = length > 0 && (length < min || length > max);
                return (
                  <FormField
                    key={section.key}
                    label={section.label}
                    htmlFor={`profile-${section.key}`}
                    hint={`${section.hint} ${length} characters${
                      outsideGuide ? ` (${min} to ${max} reads best)` : ""
                    }`}
                  >
                    <Textarea
                      id={`profile-${section.key}`}
                      rows={6}
                      readOnly={!canEdit}
                      placeholder={section.placeholder}
                      value={value}
                      onChange={(e) =>
                        setDraft({ ...draft, [section.key]: e.target.value })
                      }
                    />
                  </FormField>
                );
              })}

              {canEdit ? (
                <Button
                  className="gap-1.5"
                  disabled={saving}
                  onClick={() => void save()}
                >
                  {saving ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="h-4 w-4" />
                  )}
                  {saving ? "Saving" : "Save profile"}
                </Button>
              ) : (
                <p className="text-sm">
                  You have read-only access to the company profile. Ask an
                  administrator if you need to change it.
                </p>
              )}
            </>
          )}
      </Section>
    </div>
  );
}
