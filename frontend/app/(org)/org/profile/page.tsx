"use client";

// Company Profile (spec §3.2), the page formerly called Settings.
//
// These three sections are the company's own words about itself, and every NEW
// job snapshots them into its JD, so editing here reaches future jobs only.
// That behaviour used to be spelled out in a paragraph ON the page; the client
// named it as copy that explains the implementation rather than the task, so it
// now lives here in the source and the save toast states the consequence once.

import * as React from "react";
import { ExternalLink, Loader2, Pencil, Save, Search } from "lucide-react";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import type { CompanyProfile, CompanyProfileResearch } from "@/lib/types";
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
  const [researching, setResearching] = React.useState(false);
  const [research, setResearch] = React.useState<CompanyProfileResearch | null>(null);
  const [editing, setEditing] = React.useState(false);

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
      setEditing(false);
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

  const researchProfile = async () => {
    setResearching(true);
    setEditing(false);
    try {
      const result = await apiPost<CompanyProfileResearch>(
        "/companies/me/profile/research"
      );
      setResearch(result);
      if (!result.degraded) {
        setDraft({
          about_company: result.about_company,
          work_life: result.work_life,
          benefits: result.benefits,
        });
        toast({
          title: "Research draft ready",
          description: "Review the sources, then choose Edit draft before changing or saving it.",
        });
      } else {
        toast({
          title: "Company research could not produce a draft",
          description: result.message ?? "No usable professional sources were found.",
          variant: "destructive",
        });
      }
    } catch (error) {
      setResearch(null);
      toast({
        title: "Company research failed",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setResearching(false);
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
              {canEdit ? (
                <div className="rounded-xl border bg-secondary/35 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="font-medium">Professional company research</p>
                      <p className="mt-1 text-sm">
                        Builds a draft from the company website and professional sources before editing begins.
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        className="gap-1.5"
                        disabled={researching || saving}
                        onClick={() => void researchProfile()}
                      >
                        {researching ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Search className="h-4 w-4" />
                        )}
                        {researching ? "Researching" : "Research company"}
                      </Button>
                      {research ? (
                        <Button
                          className="gap-1.5"
                          disabled={researching || editing}
                          onClick={() => setEditing(true)}
                        >
                          <Pencil className="h-4 w-4" /> Edit draft
                        </Button>
                      ) : null}
                    </div>
                  </div>
                  {research?.message ? (
                    <p role="alert" className="mt-3 text-sm text-destructive">
                      {research.message}
                    </p>
                  ) : null}
                  {research?.sources.length ? (
                    <div className="mt-3">
                      <p className="text-xs font-semibold uppercase tracking-wide">Sources used</p>
                      <ul className="mt-2 space-y-1 text-xs">
                        {research.sources.map((source) => (
                          <li key={source}>
                            <a className="inline-flex items-center gap-1 underline" href={source} target="_blank" rel="noreferrer">
                              {source} <ExternalLink className="h-3 w-3" aria-hidden="true" />
                            </a>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              ) : null}
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
                      readOnly={!canEdit || !editing}
                      placeholder={section.placeholder}
                      value={value}
                      onChange={(e) =>
                        setDraft({ ...draft, [section.key]: e.target.value })
                      }
                    />
                  </FormField>
                );
              })}

              {canEdit && editing ? (
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
