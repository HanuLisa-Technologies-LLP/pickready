"use client";

// AI Reach: find companies that are hiring, in two clearly separated segments.
//
// SEGMENT ORDER IS NOT COSMETIC. "Similar to our customers" is computed from
// ReadyPick's own customer database and makes no network call at all, so it is
// asked for first and it keeps working on a deployment with no web search key.
// "From the internet" is the agentic Tavily segment, time boxed at 30 seconds
// on the server. The two FAIL INDEPENDENTLY: an internet timeout must never
// blank the segment that came from our own data.
//
// EVERY SEGMENT STATUS IS RENDERED PROPERLY. `ok`, `unconfigured`, `timeout`
// and `unavailable` each carry a `message` written for the user, so the empty
// state says what actually happened rather than "no results".
//
// CONFIDENCE IS AN APPROVED MATCHING WORD, never a number, percentage or meter.

import * as React from "react";
import { ExternalLink, Globe, Search, Sparkles, Users } from "lucide-react";

import { apiPost } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import type {
  AIReachResponse,
  BDJobCard,
  BDSegment,
  BDSegmentStatus,
} from "@/lib/bd-types";
import { PageHeader } from "@/components/app-shell";
import { Badge, ratingVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeIn } from "@/components/motion";
import { ExportXlsxButton } from "@/components/export-xlsx-button";

interface SearchForm {
  job_role: string;
  city: string;
  industry: string;
  company: string;
}

const EMPTY_FORM: SearchForm = {
  job_role: "",
  city: "",
  industry: "",
  company: "",
};

type FormErrors = Partial<Record<keyof SearchForm, string>>;

/** Job role, city and industry are required and at least 2 characters. */
export function validateSearch(form: SearchForm): FormErrors {
  const errors: FormErrors = {};
  if (form.job_role.trim().length < 2) errors.job_role = "Enter a job role.";
  if (form.city.trim().length < 2) errors.city = "Enter a city.";
  if (form.industry.trim().length < 2) errors.industry = "Enter an industry.";
  return errors;
}

/**
 * The fallback headline for a segment that returned nothing. The server's own
 * `message` is preferred whenever it sent one: it was written for this user,
 * about this search, and it explains the outcome better than a generic line.
 */
const STATUS_FALLBACK: Record<BDSegmentStatus, string> = {
  ok: "Nothing matched this search.",
  unconfigured: "Web search is not set up on this deployment.",
  timeout: "The web search took too long and was stopped.",
  breaker_open: "Web search is paused briefly after repeated provider failures.",
  quota_exhausted: "The web search provider's quota is exhausted.",
  unavailable: "Web search could not be reached just now.",
};

export function AIReachPage() {
  const [form, setForm] = React.useState<SearchForm>(EMPTY_FORM);
  const [errors, setErrors] = React.useState<FormErrors>({});
  const [result, setResult] = React.useState<AIReachResponse | null>(null);
  const [searching, setSearching] = React.useState(false);
  const [failure, setFailure] = React.useState<string | null>(null);

  const set = <K extends keyof SearchForm>(key: K, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    if (errors[key]) setErrors((current) => ({ ...current, [key]: undefined }));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const found = validateSearch(form);
    setErrors(found);
    if (Object.keys(found).length) return;

    setSearching(true);
    setFailure(null);
    try {
      setResult(
        await apiPost<AIReachResponse>("/bd/ai-reach/search", {
          job_role: form.job_role.trim(),
          city: form.city.trim(),
          industry: form.industry.trim(),
          company: form.company.trim() || null,
        })
      );
    } catch (error) {
      setFailure(apiErrorMessage(error));
    } finally {
      setSearching(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="AI Reach"
        description="Find companies that are hiring, from our own customer database and from the web."
        actions={
          <div className="flex flex-wrap gap-2">
            {result ? <ExportXlsxButton
              fileName="readypick-ai-reach"
              rows={[
                ...result.similar_to_customers.jobs.map((job) => ({
                  segment: "Similar to our customers",
                  job_title: job.job_title,
                  company: job.company,
                  city: job.city ?? "",
                  industry: job.industry ?? "",
                  company_url: job.company_url,
                  job_url: job.job_url ?? "",
                  contact_name: job.contact_name ?? "",
                  contact_role: job.contact_role ?? "",
                  contact_email: job.contact_email ?? "",
                  contact_phone: job.contact_phone ?? "",
                  contact_source: job.contact_source_url ?? "",
                  confidence: job.confidence_label,
                })),
                ...result.from_internet.jobs.map((job) => ({
                  segment: "From the internet",
                  job_title: job.job_title,
                  company: job.company,
                  city: job.city ?? "",
                  industry: job.industry ?? "",
                  company_url: job.company_url,
                  job_url: job.job_url ?? "",
                  contact_name: job.contact_name ?? "",
                  contact_role: job.contact_role ?? "",
                  contact_email: job.contact_email ?? "",
                  contact_phone: job.contact_phone ?? "",
                  contact_source: job.contact_source_url ?? "",
                  confidence: job.confidence_label,
                })),
              ]}
            /> : null}
          </div>
        }
      />

      <form
        className="rounded-md border p-4 sm:p-6"
        onSubmit={submit}
        noValidate
      >
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <FormField
            label="Job role"
            htmlFor="ai-role"
            required
            error={errors.job_role}
          >
            <Input
              id="ai-role"
              value={form.job_role}
              maxLength={160}
              disabled={searching}
              placeholder="Senior Backend Engineer"
              onChange={(event) => set("job_role", event.target.value)}
            />
          </FormField>
          <FormField label="City" htmlFor="ai-city" required error={errors.city}>
            <Input
              id="ai-city"
              value={form.city}
              maxLength={120}
              disabled={searching}
              placeholder="Bengaluru"
              onChange={(event) => set("city", event.target.value)}
            />
          </FormField>
          <FormField
            label="Industry"
            htmlFor="ai-industry"
            required
            error={errors.industry}
          >
            <Input
              id="ai-industry"
              value={form.industry}
              maxLength={120}
              disabled={searching}
              placeholder="Financial services"
              onChange={(event) => set("industry", event.target.value)}
            />
          </FormField>
          <FormField label="Company" htmlFor="ai-company" hint="Optional">
            <Input
              id="ai-company"
              value={form.company}
              maxLength={160}
              disabled={searching}
              onChange={(event) => set("company", event.target.value)}
            />
          </FormField>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button type="submit" className="gap-2" disabled={searching}>
            {searching ? (
              <>
                <Sparkles className="h-4 w-4 animate-pulse" /> Searching…
              </>
            ) : (
              <>
                <Search className="h-4 w-4" /> Search
              </>
            )}
          </Button>
          {searching ? (
            <p className="text-sm">
              The web search can take up to 30 seconds. Results from our own
              customers appear first.
            </p>
          ) : null}
        </div>
      </form>

      {failure ? (
        <div className="mt-6 rounded-md border p-4" role="alert">
          <p className="text-sm font-medium">The search could not be run.</p>
          <p className="mt-1 text-sm">{failure}</p>
        </div>
      ) : null}

      {searching ? (
        <div className="mt-8 space-y-8">
          <SegmentSkeleton title="Similar to our customers" icon={Users} />
          <SegmentSkeleton title="From the internet" icon={Globe} />
        </div>
      ) : result ? (
        <div className="mt-8 space-y-10">
          {/* Computed from ReadyPick's own customers. Rendered first, always. */}
          <Segment
            title="Similar to our customers"
            hint="Matched against ReadyPick's customer database."
            icon={Users}
            segment={result.similar_to_customers}
          />
          {/* The agentic segment. It fails on its own, never taking the one
              above down with it. */}
          <Segment
            title="From the internet"
            hint="Discovered on the web, then checked for truth and relevance."
            icon={Globe}
            segment={result.from_internet}
          />
        </div>
      ) : (
        <div className="mt-8 rounded-md border p-10 text-center">
          <p className="text-sm font-medium">
            Search a job role, city and industry to see who is hiring.
          </p>
        </div>
      )}
    </div>
  );
}

function Segment({
  title,
  hint,
  icon: Icon,
  segment,
}: {
  title: string;
  hint: string;
  icon: typeof Users;
  segment: BDSegment;
}) {
  const empty = segment.jobs.length === 0;
  return (
    <section>
      <div className="mb-1 flex items-center gap-2">
        <Icon className="h-5 w-5" aria-hidden="true" />
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {segment.status !== "ok" ? (
          <Badge variant="muted">{statusWord(segment.status)}</Badge>
        ) : null}
      </div>
      <p className="mb-4 text-sm">{hint}</p>

      {empty ? (
        <div className="rounded-md border p-8 text-center">
          {/* The server's message is written for the user, so it is shown as
              the headline rather than replaced by a generic empty state. */}
          <p className="text-sm font-medium">
            {segment.message ?? STATUS_FALLBACK[segment.status]}
          </p>
        </div>
      ) : (
        <>
          {/* A segment can be degraded AND still carry partial results, so the
              message is shown above the cards rather than instead of them. */}
          {segment.message ? (
            <p className="mb-3 rounded-md border p-3 text-sm">
              {segment.message}
            </p>
          ) : null}
          <FadeIn className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {segment.jobs.map((job, index) => (
              <JobCard key={`${job.company_url}-${job.job_title}-${index}`} job={job} />
            ))}
          </FadeIn>
        </>
      )}
    </section>
  );
}

/** One word for a degraded segment. Never a code, never a number. */
function statusWord(status: BDSegmentStatus): string {
  switch (status) {
    case "unconfigured":
      return "Not set up";
    case "timeout":
      return "Timed out";
    case "breaker_open":
      return "Retrying later";
    case "quota_exhausted":
      return "Quota exhausted";
    case "unavailable":
      return "Unavailable";
    default:
      return "Ready";
  }
}

function JobCard({ job }: { job: BDJobCard }) {
  // The posting link when it is confidently known, the company site otherwise.
  // A guessed job URL is worse than no job URL, so the API sends null instead.
  const href = job.job_url ?? job.company_url;
  const tags = [job.company, job.city, job.industry].filter(Boolean) as string[];

  return (
    <article className="rounded-lg border p-4 transition-colors hover:border-brand-600/40 hover:bg-brand-100/40">
      <div className="flex items-start justify-between gap-3">
        <p className="font-medium leading-snug">{job.job_title}</p>
        {/* A WORD, never a number, a bar or a percentage. */}
        <Badge variant={ratingVariant(job.confidence_label)}>
          {job.confidence_label}
        </Badge>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {tags.map((tag) => (
          <Badge key={tag} variant="outline">
            {tag}
          </Badge>
        ))}
      </div>
      {job.contact_name || job.contact_email || job.contact_phone ? (
        <div className="mt-4 rounded-md bg-brand-100/70 p-3 text-sm">
          <p className="font-semibold">Published company contact</p>
          {job.contact_name ? (
            <p className="mt-1">
              {job.contact_name}
              {job.contact_role ? ` · ${job.contact_role}` : ""}
            </p>
          ) : job.contact_role ? (
            <p className="mt-1">{job.contact_role}</p>
          ) : null}
          {job.contact_email ? (
            <a
              href={`mailto:${job.contact_email}`}
              target="_blank"
              rel="noreferrer"
              className="mt-1 block text-brand-700 underline underline-offset-4"
            >
              {job.contact_email}
            </a>
          ) : null}
          {job.contact_phone ? <p className="mt-1">{job.contact_phone}</p> : null}
          {job.contact_source_url ? (
            <a
              href={job.contact_source_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-flex items-center gap-1 text-xs underline underline-offset-4"
            >
              Verify source
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          ) : null}
        </div>
      ) : (
        <p className="mt-4 text-xs">No verified public company contact found.</p>
      )}
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="mt-4 flex items-center gap-1.5 text-xs font-semibold text-brand-700 underline underline-offset-4"
      >
        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        {job.job_url ? "Open the job posting" : "Open the company website"}
        {job.source_domain ? ` (${job.source_domain})` : ""}
      </a>
    </article>
  );
}

function SegmentSkeleton({
  title,
  icon: Icon,
}: {
  title: string;
  icon: typeof Users;
}) {
  return (
    <section aria-busy="true">
      <div className="mb-4 flex items-center gap-2">
        <Icon className="h-5 w-5" aria-hidden="true" />
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      </div>
      <span className="sr-only">Loading results</span>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-32 w-full" />
        ))}
      </div>
    </section>
  );
}
