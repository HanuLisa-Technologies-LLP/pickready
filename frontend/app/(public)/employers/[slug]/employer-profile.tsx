"use client";

// One employer's public page (2026-09-05 add-features spec, "Employer Page &
// Content"): identity, narrative sections, and a careers list of currently
// live jobs, each linking to the existing public application page. Backed by
// GET /employers/{slug}; a hidden or unknown slug answers 404 and renders the
// same not-found state, so this page cannot be used to probe which companies
// exist on the platform.

import * as React from "react";
import Link from "next/link";
import {
  Briefcase,
  Building2,
  Globe,
  HeartHandshake,
  Sparkles,
  Users,
} from "lucide-react";

import { ApiError, apiGet } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { EmptyState, ErrorState, LoadingCards } from "@/components/page-primitives";
import { FadeIn, Stagger, StaggerItem } from "@/components/motion";

/** Mirrors `schemas.employer_pages.EmployerOpenRoleOut`. */
interface OpenRole {
  id: string;
  title: string;
  department?: string | null;
  level?: string | null;
  experience_min_years?: number | null;
  experience_max_years?: number | null;
  apply_path: string;
  apply_url: string;
}

/** Mirrors `schemas.employer_pages.EmployerPageOut`. */
interface EmployerPageData {
  slug: string;
  name: string;
  industry?: string | null;
  website_domain?: string | null;
  about_company?: string | null;
  work_life?: string | null;
  benefits?: string | null;
  open_roles: OpenRole[];
}

function experienceBand(role: OpenRole): string | null {
  const { experience_min_years: min, experience_max_years: max } = role;
  if (min == null && max == null) return null;
  if (min != null && max != null) return `${min} to ${max} years experience`;
  if (min != null) return `${min}+ years experience`;
  return `Up to ${max} years experience`;
}

const SECTIONS = [
  {
    key: "about_company",
    icon: Building2,
    title: "About the company",
  },
  {
    key: "work_life",
    icon: Users,
    title: "Work life",
  },
  {
    key: "benefits",
    icon: HeartHandshake,
    title: "Benefits",
  },
] as const;

export function EmployerProfile({ slug }: { slug: string }) {
  const [data, setData] = React.useState<EmployerPageData | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [notFound, setNotFound] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadCount, setReloadCount] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotFound(false);
    apiGet<EmployerPageData>(`/employers/${encodeURIComponent(slug)}`)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true);
        } else {
          setError(
            e instanceof Error ? e.message : "Could not load this page."
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, reloadCount]);

  if (loading) {
    return (
      <section className="mx-auto max-w-5xl px-6 py-14 lg:px-10 lg:py-20">
        <LoadingCards count={4} label="Loading employer page" />
      </section>
    );
  }

  if (notFound) {
    return (
      <section className="mx-auto max-w-3xl px-6 py-20 lg:px-10">
        <EmptyState
          icon={Building2}
          title="Employer page not found"
          description="This page does not exist or is not public. Browse the directory for companies hiring through ReadyPick."
          action={
            <Button asChild variant="outline">
              <Link href="/employers">Browse employers</Link>
            </Button>
          }
        />
      </section>
    );
  }

  if (error || !data) {
    return (
      <section className="mx-auto max-w-3xl px-6 py-20 lg:px-10">
        <ErrorState
          title="Could not load this employer page"
          description={error ?? undefined}
          action={
            <Button
              variant="outline"
              onClick={() => setReloadCount((count) => count + 1)}
            >
              Try again
            </Button>
          }
        />
      </section>
    );
  }

  const sections = SECTIONS.map((section) => ({
    ...section,
    body: data[section.key],
  })).filter((section) => Boolean(section.body));

  return (
    <>
      <section className="relative overflow-hidden border-b border-border py-16 lg:py-20">
        <div
          aria-hidden="true"
          className="absolute -top-40 left-1/2 h-[28rem] w-[44rem] -translate-x-1/2 rounded-full bg-navy-600/15 blur-[120px]"
        />
        <FadeIn className="relative mx-auto max-w-5xl px-6 lg:px-10">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div className="min-w-0">
              <p className="text-sm font-semibold uppercase tracking-[.18em] text-teal-700">
                Employer page
              </p>
              <h1 className="mt-3 text-balance text-3xl font-bold sm:text-4xl">
                {data.name}
              </h1>
              {data.industry ? (
                <p className="mt-3 text-base leading-7">{data.industry}</p>
              ) : null}
            </div>
            {data.website_domain ? (
              <Button asChild variant="outline">
                <a
                  href={`https://${data.website_domain}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Globe className="h-4 w-4" aria-hidden="true" />
                  {data.website_domain}
                </a>
              </Button>
            ) : null}
          </div>
        </FadeIn>
      </section>

      {sections.length > 0 ? (
        <section className="mx-auto max-w-5xl px-6 py-12 lg:px-10">
          <Stagger className="grid gap-4 lg:grid-cols-3">
            {sections.map((section) => (
              <StaggerItem key={section.key}>
                <Card className="h-full shadow-card">
                  <CardContent className="p-6">
                    <section.icon
                      className="h-6 w-6 text-navy-600"
                      aria-hidden="true"
                    />
                    <h2 className="mt-4 text-base font-semibold">
                      {section.title}
                    </h2>
                    <p className="mt-2 whitespace-pre-line text-pretty text-sm leading-7">
                      {section.body}
                    </p>
                  </CardContent>
                </Card>
              </StaggerItem>
            ))}
          </Stagger>
        </section>
      ) : null}

      <section className="mx-auto max-w-5xl px-6 pb-20 lg:px-10">
        {sections.length > 0 ? <Separator className="mb-10" /> : null}
        <FadeIn>
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-teal-700" aria-hidden="true" />
            <h2 className="text-2xl font-bold">Careers at {data.name}</h2>
          </div>
          <p className="mt-2 leading-7">
            Every application goes straight to the hiring team through
            ReadyPick.
          </p>
        </FadeIn>

        {data.open_roles.length === 0 ? (
          <div className="mt-6">
            <EmptyState
              icon={Briefcase}
              title="No open roles right now."
              description="Check back soon; new roles appear here the moment they go live."
            />
          </div>
        ) : (
          <Stagger className="mt-6 grid gap-4 sm:grid-cols-2">
            {data.open_roles.map((role) => {
              const band = experienceBand(role);
              return (
                <StaggerItem key={role.id}>
                  <Card className="h-full shadow-card transition-shadow duration-150 hover:shadow-card-hover">
                    <CardContent className="flex h-full flex-col gap-3 p-6">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <h3 className="text-balance text-base font-semibold">
                          {role.title}
                        </h3>
                        {role.department ? (
                          <Badge variant="outline">{role.department}</Badge>
                        ) : null}
                      </div>
                      {band || role.level ? (
                        <p className="text-sm leading-6">
                          {[role.level, band].filter(Boolean).join(" · ")}
                        </p>
                      ) : null}
                      <div className="mt-auto pt-2">
                        <Button asChild className="w-full">
                          <Link href={role.apply_path}>Apply</Link>
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </StaggerItem>
              );
            })}
          </Stagger>
        )}
      </section>
    </>
  );
}
