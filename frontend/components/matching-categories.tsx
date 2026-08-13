"use client";

// The job's own Matching category list (spec 3.2).
//
// Matching no longer runs on one fixed set of four parameters across every job.
// The AI proposes at least five categories at job creation; the recruiter adds,
// modifies, replaces or removes them; and the finalised list applies
// automatically to every candidate sourced for that job, with no further step
// per candidate.
//
// THE LINE THIS SCREEN HAS TO KEEP VISIBLE
// ----------------------------------------
// Every category here is judged from RESUME TEXT ALONE, before the candidate
// has answered anything. Skill DEPTH and VERIFIED behaviour belong to the PPI
// matrix below and are assessed in a conversation. The two share named
// territory in places, so the description under the heading says which is
// which: a recruiter who adds "React expertise" here expecting a depth check
// will not get one, and would have no way to know.
//
// The minimum of five is enforced SERVER-side. This screen disables the Save
// control below five, but a disabled button is not an enforcement.

import * as React from "react";
import { Check, Loader2, Pencil, Plus, Trash2 } from "lucide-react";

import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const BASE = "/api/v2/matching/jobs";

export interface MatchingCategory {
  id: string;
  key: string;
  name: string;
  description: string | null;
  ordinal: number;
}

export interface MatchingCategories {
  job_id: string;
  finalized: boolean;
  categories: MatchingCategory[];
  minimum: number;
  maximum: number;
  blocking_reason: string | null;
}

function errorMessage(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | null)?.detail;
  return typeof detail === "string" && detail ? detail : fallback;
}

function CategoryRow({
  category,
  frozen,
  onSave,
  onRemove,
}: {
  category: MatchingCategory;
  frozen: boolean;
  onSave: (next: { name: string; description: string | null }) => Promise<void>;
  onRemove: () => Promise<void>;
}) {
  const [editing, setEditing] = React.useState(false);
  const [name, setName] = React.useState(category.name);
  const [description, setDescription] = React.useState(category.description ?? "");

  if (!editing) {
    return (
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3">
        <div className="min-w-0">
          <p className="font-medium">{category.name}</p>
          {category.description ? (
            <p className="text-xs">{category.description}</p>
          ) : null}
        </div>
        {frozen ? null : (
          <div className="flex shrink-0 gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(true)}
              aria-label={`Edit ${category.name}`}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void onRemove()}
              aria-label={`Remove ${category.name}`}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <Input
        value={name}
        onChange={(event) => setName(event.target.value)}
        aria-label="Category name"
      />
      <Textarea
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        rows={2}
        placeholder="What in a resume answers this?"
        aria-label="What this category checks"
      />
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={!name.trim()}
          onClick={async () => {
            await onSave({ name: name.trim(), description: description.trim() || null });
            setEditing(false);
          }}
        >
          <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          Save
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setName(category.name);
            setDescription(category.description ?? "");
            setEditing(false);
          }}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function MatchingCategoriesCard({ jobId }: { jobId: string }) {
  const { toast } = useToast();
  const [state, setState] = React.useState<MatchingCategories | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  const [adding, setAdding] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");

  const load = React.useCallback(async () => {
    try {
      setState(await apiGet<MatchingCategories>(`${BASE}/${jobId}/categories`));
    } catch {
      setState(null);
    }
    setLoading(false);
  }, [jobId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const mutate = React.useCallback(
    async (action: () => Promise<unknown>, failureTitle: string) => {
      setBusy(true);
      try {
        await action();
        await load();
        return true;
      } catch (error) {
        toast({
          title: failureTitle,
          description: errorMessage(error, "Please try again."),
          variant: "destructive",
        });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [load, toast]
  );

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Loading the matching categories
        </CardContent>
      </Card>
    );
  }

  if (!state) return null;

  const frozen = state.finalized;
  const atCeiling = state.categories.length >= state.maximum;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Matching categories</CardTitle>
          <Badge variant={frozen ? "secondary" : "outline"}>
            {frozen ? "Saved" : "Awaiting review"}
          </Badge>
        </div>
        <CardDescription>
          What every sourced resume for this job is rated against, before anyone
          is assessed. This is a background and logistics check judged from
          resume text alone: skill depth and demonstrated behaviour are assessed
          in the conversation, against the evaluation matrix below.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {state.categories.length === 0 ? (
          <p className="text-sm">
            The categories for this job are still being prepared. They are
            proposed from the job description, so they appear here a few moments
            after the job is created. Refresh to check.
          </p>
        ) : (
          <>
            <p className="text-xs">
              {state.categories.length} of at least {state.minimum}, at most{" "}
              {state.maximum}.
            </p>

            <div className="space-y-2">
              {state.categories.map((category) => (
                <CategoryRow
                  key={category.id}
                  category={category}
                  frozen={frozen}
                  onSave={(next) =>
                    mutate(
                      () =>
                        apiPut(`${BASE}/${jobId}/categories/${category.id}`, next),
                      "Couldn't save that change"
                    ).then(() => undefined)
                  }
                  onRemove={() =>
                    mutate(
                      () => apiDelete(`${BASE}/${jobId}/categories/${category.id}`),
                      "Couldn't remove that category"
                    ).then(() => undefined)
                  }
                />
              ))}
            </div>

            {frozen || atCeiling ? null : adding ? (
              <div className="space-y-2 rounded-md border border-dashed p-3">
                <Input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Category name, for example Notice period fit"
                  aria-label="New category name"
                />
                <Textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  rows={2}
                  placeholder="What in a resume answers this?"
                  aria-label="What the new category checks"
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    disabled={busy || !name.trim()}
                    onClick={async () => {
                      const ok = await mutate(
                        () =>
                          apiPost(`${BASE}/${jobId}/categories`, {
                            name: name.trim(),
                            description: description.trim() || null,
                          }),
                        "Couldn't add that category"
                      );
                      if (ok) {
                        setName("");
                        setDescription("");
                        setAdding(false);
                      }
                    }}
                  >
                    Add category
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
                <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                Add a category
              </Button>
            )}

            {state.blocking_reason ? (
              <p className="rounded-md border border-amber-600 p-3 text-xs">
                {state.blocking_reason}
              </p>
            ) : null}

            {frozen ? (
              <p className="text-xs">
                These categories are saved and candidates have been ranked
                against them. They are frozen from here, because two candidates
                ranked against different lists cannot be compared.
              </p>
            ) : (
              <Button
                disabled={busy || Boolean(state.blocking_reason)}
                onClick={async () => {
                  const ok = await mutate(
                    () => apiPost(`${BASE}/${jobId}/categories/finalize`),
                    "Couldn't save the categories"
                  );
                  if (ok) toast({ title: "Matching categories saved" });
                }}
              >
                Save categories
              </Button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
