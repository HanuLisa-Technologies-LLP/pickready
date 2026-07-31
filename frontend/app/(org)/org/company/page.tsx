"use client";

import * as React from "react";
import { Building2, ChevronDown, Save, UserRound } from "lucide-react";

import { apiGet, apiPut } from "@/lib/api";
import type { CompanyPage, TenantProfile } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import {
  DetailItem,
  ErrorState,
  LoadingRows,
  Section,
} from "@/components/page-primitives";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const INDUSTRIES = [
  "Technology",
  "Finance",
  "Healthcare",
  "Retail",
  "Manufacturing",
  "Education",
  "Other",
] as const;
const EMPTY_PAGE: CompanyPage = {
  brief: "",
  culture: "",
  policies: "",
  benefits: "",
};
const wordCount = (value: string) =>
  value.trim() ? value.trim().split(/\s+/).length : 0;

export default function CompanyPageEditor() {
  const { toast } = useToast();
  const [page, setPage] = React.useState<CompanyPage>(EMPTY_PAGE);
  const [company, setCompany] = React.useState<TenantProfile | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [cultureTouched, setCultureTouched] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [companyPage, profile] = await Promise.all([
        apiGet<CompanyPage | { company: CompanyPage }>("/companies/me"),
        apiGet<TenantProfile>("/admin/my-tenant"),
      ]);
      const resolved =
        "company" in (companyPage as object)
          ? (companyPage as { company: CompanyPage }).company
          : (companyPage as CompanyPage);
      setPage({ ...EMPTY_PAGE, ...resolved });
      setCompany(profile);
    } catch (error) {
      setLoadError(
        error instanceof Error
          ? error.message
          : "Could not load company details."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const cultureWords = wordCount(company?.culture ?? "");
  const cultureInvalid =
    Boolean(company?.editable) &&
    (cultureWords < 100 || cultureWords > 500);

  const save = async () => {
    if (!company || cultureInvalid || !company.name.trim() || !company.industry) {
      setCultureTouched(true);
      return;
    }
    setSaving(true);
    try {
      const requests: Promise<unknown>[] = [apiPut("/companies/me", page)];
      if (company.editable) {
        requests.push(
          apiPut("/admin/my-tenant", {
            name: company.name.trim(),
            industry: company.industry,
            culture: company.culture?.trim() || null,
            details: company.details?.trim() || null,
          })
        );
      }
      await Promise.all(requests);
      toast({
        title: "Company details saved",
        description:
          "Everyone in your workspace now sees the updated company profile.",
      });
    } catch (error) {
      toast({
        title: "Could not save company details",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div>
        <PageHeader eyebrow="Customer Portal" title="Company Page" />
        <LoadingRows rows={5} label="Loading company details" />
      </div>
    );
  }

  if (loadError || !company) {
    return (
      <div>
        <PageHeader eyebrow="Customer Portal" title="Company Page" />
        <ErrorState
          title="Company details could not be loaded"
          description={loadError ?? undefined}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Company Page"
        description="What your team and every candidate sees about your company."
        actions={
          <Button
            disabled={saving || cultureInvalid}
            onClick={() => void save()}
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            {saving ? "Saving" : "Save changes"}
          </Button>
        }
      />

      <div className="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
        <div className="space-y-6">
          <Section
            title={
              <span className="flex items-center gap-2">
                <Building2 className="h-4 w-4" aria-hidden="true" /> Company
                profile
              </span>
            }
            contentClassName="space-y-5"
          >
              <FormField label="Company name" htmlFor="company-name" required>
                <Input
                  id="company-name"
                  value={company.name}
                  disabled={!company.editable || saving}
                  onChange={(event) =>
                    setCompany({ ...company, name: event.target.value })
                  }
                />
              </FormField>
              <FormField label="Industry" required>
                <Select
                  value={company.industry ?? ""}
                  disabled={!company.editable || saving}
                  onValueChange={(industry) =>
                    setCompany({ ...company, industry })
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select an industry" />
                  </SelectTrigger>
                  <SelectContent>
                    {INDUSTRIES.map((industry) => (
                      <SelectItem key={industry} value={industry}>
                        {industry}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FormField>
              <FormField
                label="Company culture"
                htmlFor="company-culture"
                required
                hint={`${cultureWords} words, 100 to 500 required`}
              >
                <Textarea
                  id="company-culture"
                  rows={8}
                  value={company.culture ?? ""}
                  disabled={!company.editable || saving}
                  aria-invalid={cultureTouched && cultureInvalid}
                  onBlur={() => setCultureTouched(true)}
                  onChange={(event) =>
                    setCompany({ ...company, culture: event.target.value })
                  }
                />
                {cultureTouched && cultureInvalid ? (
                  <p role="alert" className="mt-1 text-xs font-medium text-destructive">
                    Company culture must be between 100 and 500 words.
                  </p>
                ) : null}
              </FormField>
              <FormField
                label="Company details"
                htmlFor="company-details"
                hint="Founding year, size, headquarters, mission, markets, and additional context."
              >
                <Textarea
                  id="company-details"
                  rows={6}
                  value={company.details ?? ""}
                  disabled={!company.editable || saving}
                  onChange={(event) =>
                    setCompany({ ...company, details: event.target.value })
                  }
                />
              </FormField>
          </Section>

          <Section
            title="Candidate-facing information"
            contentClassName="space-y-5"
          >
              <FormField label="Company overview" htmlFor="company-brief">
                <Textarea
                  id="company-brief"
                  rows={5}
                  value={page.brief ?? ""}
                  disabled={saving}
                  onChange={(event) =>
                    setPage({ ...page, brief: event.target.value })
                  }
                />
              </FormField>
              <FormField label="Benefits" htmlFor="company-benefits">
                <Textarea
                  id="company-benefits"
                  rows={5}
                  value={page.benefits ?? ""}
                  disabled={saving}
                  onChange={(event) =>
                    setPage({ ...page, benefits: event.target.value })
                  }
                />
              </FormField>
              <FormField label="Policies" htmlFor="company-policies">
                <Textarea
                  id="company-policies"
                  rows={5}
                  value={page.policies ?? ""}
                  disabled={saving}
                  onChange={(event) =>
                    setPage({ ...page, policies: event.target.value })
                  }
                />
              </FormField>
          </Section>
        </div>

        <div className="space-y-4">
          <Section
            title={
              <span className="flex items-center gap-2">
                <UserRound className="h-4 w-4" aria-hidden="true" /> Primary
                contact
              </span>
            }
          >
            <dl className="space-y-4">
              <DetailItem label="Name">
                {company.client_name ?? "Not set"}
              </DetailItem>
              <DetailItem label="Email">
                {company.client_email ?? "Not set"}
              </DetailItem>
              <DetailItem label="Company since">
                {new Date(company.created_at).toLocaleDateString()}
              </DetailItem>
            </dl>
          </Section>

          <details className="group overflow-hidden rounded-xl border border-border bg-surface shadow-card">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-4 text-sm font-semibold">
              Full company details
              <ChevronDown
                className="h-4 w-4 transition-transform duration-200 group-open:rotate-180"
                aria-hidden="true"
              />
            </summary>
            <div className="space-y-4 border-t border-border p-4 text-sm">
              <section>
                <h3 className="font-semibold">Culture</h3>
                <p className="mt-1 whitespace-pre-wrap leading-6">
                  {company.culture || "No culture statement yet."}
                </p>
              </section>
              <section>
                <h3 className="font-semibold">Additional details</h3>
                <p className="mt-1 whitespace-pre-wrap leading-6">
                  {company.details || "No additional details yet."}
                </p>
              </section>
            </div>
          </details>

          {!company.editable ? (
            <p className="rounded-xl border border-dashed border-border p-4 text-sm leading-6">
              You have read-only access. A Company Admin can update these
              details.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
