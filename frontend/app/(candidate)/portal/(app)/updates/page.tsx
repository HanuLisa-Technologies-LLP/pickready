"use client";

// Updates (workflow sections 14 and 15).
//
// WHY THIS PAGE EXISTS. Everything the product tells a candidate goes out by
// email and nowhere else. A spam filter, a full inbox, or a typo in an address
// a recruiter uploaded, and somebody misses an assessment invitation with
// neither side ever finding out. This is the same information, durable, in a
// place signing in always reaches.
//
// It is a READ surface. Nothing here changes an application, and the only
// write is marking rows read, which happens automatically on arrival because
// the page has been opened and that IS the reading.

import * as React from "react";
import Link from "next/link";
import { BellRing, Mail } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, LoadingCards } from "@/components/page-primitives";
import { Stagger, StaggerItem } from "@/components/motion";

interface CandidateUpdate {
  id: string;
  kind: string;
  title: string;
  body: string;
  link_path: string | null;
  job_title: string | null;
  company_name: string | null;
  emailed: boolean;
  read_at: string | null;
  unread: boolean;
  created_at: string;
}

interface UpdatesResponse {
  updates: CandidateUpdate[];
  unread_count: number;
  total: number;
  page: number;
  page_size: number;
  has_next: boolean;
}

function when(value: string): string {
  const at = new Date(value);
  const days = Math.floor((Date.now() - at.getTime()) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return at.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function UpdatesPage() {
  const [data, setData] = React.useState<UpdatesResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [page, setPage] = React.useState(1);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet<UpdatesResponse>(`/portal/updates?page=${page}`));
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Your updates could not be loaded.",
      );
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [page]);

  React.useEffect(() => {
    void load();
  }, [load]);

  // Opening the page IS reading it, so the rows are marked read on arrival
  // rather than behind a button nobody presses. The UNREAD STYLING SURVIVES
  // for this render: a candidate who came here to see what is new should still
  // be able to see which rows were new, and clearing the emphasis under their
  // cursor is how you make somebody feel they missed something.
  const marked = React.useRef(false);
  React.useEffect(() => {
    if (marked.current || !data || data.unread_count === 0) return;
    marked.current = true;
    void apiPost("/portal/updates/read", {}).catch(() => {
      // A failed mark costs a stale badge until the next visit, and nothing
      // else. It must never take down the page the candidate came to read.
      marked.current = false;
    });
  }, [data]);

  return (
    <div>
      <PageHeader
        eyebrow="Candidate Portal"
        title="Updates"
        description="Everything that has happened on your applications, in one place. If an email ever goes missing, it is still here."
      />

      {loading ? (
        <LoadingCards count={4} />
      ) : error ? (
        <EmptyState
          title="Updates unavailable"
          description={error}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          }
        />
      ) : !data || data.updates.length === 0 ? (
        <EmptyState
          icon={BellRing}
          title="Nothing yet"
          description="When you apply for a role, and every time something moves, it will appear here."
        />
      ) : (
        <>
          <Stagger className="space-y-3">
            {data.updates.map((update) => (
              <StaggerItem key={update.id}>
                <Card>
                  <CardContent className="space-y-2 pt-6">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                      {/* Unread is a BINARY state, so it is marked the way a
                          binary state is conventionally marked: a dot beside
                          the title. A coloured rule down the side of the card
                          would be carrying one bit of information with the
                          most recognisable tell in the language, and the dot
                          loses nothing -- which is the repository's stated bar
                          for keeping one (.impeccable-exceptions.md). */}
                      <h2 className="flex items-baseline gap-2 text-base font-semibold">
                        {update.unread ? (
                          <span
                            className="h-2 w-2 shrink-0 translate-y-[-0.1em] rounded-full bg-navy-600"
                            aria-hidden="true"
                          />
                        ) : null}
                        {update.title}
                        {update.unread ? (
                          <span className="sr-only"> (unread)</span>
                        ) : null}
                      </h2>
                      <span className="text-xs">{when(update.created_at)}</span>
                    </div>
                    {update.job_title ? (
                      <p className="text-sm font-medium">
                        {update.job_title}
                        {update.company_name ? ` at ${update.company_name}` : ""}
                      </p>
                    ) : null}
                    <p className="text-sm leading-6">{update.body}</p>
                    <div className="flex flex-wrap items-center gap-4 pt-1">
                      {update.link_path ? (
                        <Button asChild size="sm" variant="outline">
                          <Link href={update.link_path}>Open</Link>
                        </Button>
                      ) : null}
                      {update.emailed ? (
                        <span className="inline-flex items-center gap-1.5 text-xs">
                          <Mail className="h-3.5 w-3.5" aria-hidden="true" />
                          We emailed you about this too
                        </span>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>
              </StaggerItem>
            ))}
          </Stagger>

          {data.total > data.page_size ? (
            <div className="mt-6 flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                disabled={data.page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Newer
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!data.has_next}
                onClick={() => setPage((p) => p + 1)}
              >
                Older
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
