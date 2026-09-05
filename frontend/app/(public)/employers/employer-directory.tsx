"use client";

// The searchable public employer directory (2026-09-05 add-features spec,
// "Employer Page & Content"). Backed by GET /employers, which filters in SQL
// before pagination; this component only renders what the server matched.

import * as React from "react";
import Link from "next/link";
import { Building2, ChevronLeft, ChevronRight, Globe, Search, X } from "lucide-react";

import { apiGet } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingCards } from "@/components/page-primitives";
import { FadeIn, Stagger, StaggerItem } from "@/components/motion";

/** Mirrors `schemas.employer_pages.EmployerCardOut`. */
interface EmployerCard {
  slug: string;
  name: string;
  industry?: string | null;
  website_domain?: string | null;
}

interface EmployerSearchResponse {
  employers: EmployerCard[];
  total: number;
  page: number;
  page_size: number;
}

const PAGE_SIZE = 20;

export function EmployerDirectory() {
  const [search, setSearch] = React.useState("");
  const [activeSearch, setActiveSearch] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [data, setData] = React.useState<EmployerSearchResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [reloadCount, setReloadCount] = React.useState(0);

  // Debounce typing into one request, and reset to page 1 on a new term.
  React.useEffect(() => {
    const handle = window.setTimeout(() => {
      setActiveSearch(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(handle);
  }, [search]);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    if (activeSearch) params.set("search", activeSearch);
    apiGet<EmployerSearchResponse>(`/employers?${params.toString()}`)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(
            e instanceof Error ? e.message : "Could not load employers."
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeSearch, page, reloadCount]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <section className="mx-auto max-w-5xl px-6 py-14 lg:px-10 lg:py-20">
      <FadeIn>
        <p className="text-sm font-semibold uppercase tracking-[.18em] text-teal-700">
          Employer directory
        </p>
        <h1 className="mt-3 text-balance text-3xl font-bold sm:text-4xl">
          Companies hiring on ReadyPick
        </h1>
        <p className="mt-4 max-w-2xl text-pretty leading-7">
          Read about a company, visit its website, and apply to its open roles
          from one place.
        </p>
      </FadeIn>

      <div className="mt-8 max-w-md">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 opacity-70"
            aria-hidden="true"
          />
          <Input
            type="search"
            className="pl-10 pr-10"
            placeholder="Search by company or industry"
            value={search}
            aria-label="Search employers by company or industry"
            onChange={(event) => setSearch(event.target.value)}
          />
          {search ? (
            <button
              type="button"
              className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md hover:bg-navy-600/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Clear search"
              onClick={() => setSearch("")}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-8">
        {loading ? (
          <LoadingCards count={6} label="Loading employers" />
        ) : error ? (
          <ErrorState
            title="Could not load employers"
            description={error}
            action={
              <Button
                variant="outline"
                onClick={() => setReloadCount((count) => count + 1)}
              >
                Try again
              </Button>
            }
          />
        ) : !data || data.employers.length === 0 ? (
          <EmptyState
            icon={Building2}
            title={
              activeSearch
                ? "No employers match that search"
                : "No employer pages yet"
            }
            description={
              activeSearch
                ? "Try a shorter phrase, or clear the search to browse every company."
                : "Companies appear here as soon as they are hiring through ReadyPick."
            }
            action={
              activeSearch ? (
                <Button variant="outline" onClick={() => setSearch("")}>
                  Clear search
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <Stagger className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {data.employers.map((employer) => (
                <StaggerItem key={employer.slug}>
                  <Link
                    href={`/employers/${employer.slug}`}
                    className="block h-full rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Card className="h-full shadow-card transition-shadow duration-150 hover:shadow-card-hover">
                      <CardContent className="flex h-full flex-col gap-3 p-6">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-navy-600/10">
                          <Building2
                            className="h-5 w-5 text-navy-600"
                            aria-hidden="true"
                          />
                        </div>
                        <h2 className="text-balance text-base font-semibold">
                          {employer.name}
                        </h2>
                        {employer.industry ? (
                          <p className="text-sm leading-6">{employer.industry}</p>
                        ) : null}
                        {employer.website_domain ? (
                          <p className="mt-auto flex items-center gap-1.5 text-sm leading-6">
                            <Globe
                              className="h-3.5 w-3.5 shrink-0 opacity-70"
                              aria-hidden="true"
                            />
                            <span className="truncate">
                              {employer.website_domain}
                            </span>
                          </p>
                        ) : null}
                      </CardContent>
                    </Card>
                  </Link>
                </StaggerItem>
              ))}
            </Stagger>

            {totalPages > 1 ? (
              <nav
                className="mt-8 flex items-center justify-between"
                aria-label="Employer directory pages"
              >
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                  Previous
                </Button>
                <span className="text-sm font-medium">
                  Page {page} of {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() =>
                    setPage((current) => Math.min(totalPages, current + 1))
                  }
                >
                  Next
                  <ChevronRight className="h-4 w-4" aria-hidden="true" />
                </Button>
              </nav>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
