"use client";

// Company page editor (FR-2.1): brief, culture, policies, benefits.

import * as React from "react";

import { apiGet, apiPut } from "@/lib/api";
import type { CompanyPage } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import { Card, CardContent } from "@/components/ui/card";

const EMPTY: CompanyPage = { brief: "", culture: "", policies: "", benefits: "" };

export default function CompanyPageEditor() {
  const { toast } = useToast();
  const [page, setPage] = React.useState<CompanyPage>(EMPTY);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    apiGet<CompanyPage | { company: CompanyPage }>("/companies/me")
      .then((res) => {
        const p = "company" in (res as object) ? (res as { company: CompanyPage }).company : (res as CompanyPage);
        setPage({ ...EMPTY, ...p });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await apiPut("/companies/me", page);
      toast({ title: "Company page saved" });
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

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Company Page"
        description="Your brief, culture, policies and benefits — visible to candidates and Hanulisa staff."
        actions={
          <Button onClick={() => void save()} disabled={saving || loading}>
            {saving ? "Saving…" : "Save"}
          </Button>
        }
      />
      <Card>
        <CardContent className="space-y-6 pt-6">
          <FormField label="Company brief" htmlFor="brief" required>
            <Textarea
              id="brief"
              rows={4}
              placeholder="What the company does, mission, scale…"
              value={page.brief}
              onChange={(e) => setPage({ ...page, brief: e.target.value })}
              disabled={loading}
            />
          </FormField>
          <FormField label="Culture" htmlFor="culture">
            <Textarea
              id="culture"
              rows={4}
              value={page.culture}
              onChange={(e) => setPage({ ...page, culture: e.target.value })}
              disabled={loading}
            />
          </FormField>
          <FormField label="Policies" htmlFor="policies">
            <Textarea
              id="policies"
              rows={4}
              value={page.policies}
              onChange={(e) => setPage({ ...page, policies: e.target.value })}
              disabled={loading}
            />
          </FormField>
          <FormField label="Benefits" htmlFor="benefits">
            <Textarea
              id="benefits"
              rows={4}
              value={page.benefits}
              onChange={(e) => setPage({ ...page, benefits: e.target.value })}
              disabled={loading}
            />
          </FormField>
        </CardContent>
      </Card>
    </div>
  );
}
