"use client";

/**
 * The Skills step (Vivekium release, Phase 1; owner ruling D1).
 *
 * WHAT THIS REPLACED
 * ------------------
 * The Tatva Assessment matrix editor and the Matching Categories editor are
 * gone. What a recruiter reviews now is one list of skills in three buckets,
 * Must-have, Nice-to-have and Behavioural, at most five each. Sutra drafts it
 * from the JD, the saved SWOT and the Company Profile; the team adds, pastes,
 * renames, moves and removes; Save Skills writes the hidden assessment context
 * and makes the job invitable.
 *
 * WHAT THIS SCREEN DELIBERATELY DOES NOT SHOW
 * -------------------------------------------
 * No matrix, no weight, no priority, no evidence line and no grade word per
 * skill. Sutra's priority and its "what good evidence looks like" line are
 * hidden context the assessment reads; showing them would invite editing a
 * thing the recruiter does not own. Counts are spelled out in words, like the
 * proctoring report, so no digit sits beside a hiring decision.
 *
 * THE SERVER DECIDES, AND ITS SENTENCES ARE SHOWN VERBATIM
 * --------------------------------------------------------
 * The five-per-bucket limit, a name clash on add, rename or move, the lock,
 * the save validation and an outage are all refusals the API words. This
 * component renders each one exactly as sent: a paraphrase is a second author
 * for a rule, and the two drift. An outage on Save names no skill, because the
 * provider being down is not something the reviewer can fix by editing one.
 *
 * LOCKED IS A STATE, NOT A PERMISSION
 * -----------------------------------
 * Once a candidate starts the assessment (D5) the skills and the grade are
 * read-only for everybody. That is said in a plain state sentence. The
 * read-only sentence of `<ReadOnlyNotice>` is reserved for a person who lacks
 * the capability on a bucket that is still editable, which is a different
 * fact and must not be told the same way.
 */

import * as React from "react";
import {
  ArrowRightLeft,
  Check,
  Loader2,
  Lock,
  Pencil,
  Plus,
  RotateCcw,
  Save,
  X,
} from "lucide-react";

import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  ApiError,
} from "@/lib/api";
import { CAP, resolvePermission } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";
import { cn } from "@/lib/utils";
import { ReadOnlyNotice } from "@/components/permission-notice";
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** The assessments router is mounted at /api/v2 ONLY; `api-mount-parity`
 *  pins that every skills path is written from this full mount. */
const BASE = "/api/v2/assessments/jobs";

export type SkillBucket = "must_have" | "nice_to_have" | "behavioural";

export const BUCKETS: SkillBucket[] = ["must_have", "nice_to_have", "behavioural"];

export const BUCKET_LABEL: Record<SkillBucket, string> = {
  must_have: "Must-have",
  nice_to_have: "Nice-to-have",
  behavioural: "Behavioural",
};

const BUCKET_HINT: Record<SkillBucket, string> = {
  must_have: "The role cannot be performed without these.",
  nice_to_have: "Helpful, but not disqualifying.",
  behavioural: "Observable workplace behaviours the role demands.",
};

const BUCKET_PLACEHOLDER: Record<SkillBucket, string> = {
  must_have: "Skill or capability",
  nice_to_have: "Skill or capability",
  behavioural: "Behaviour",
};

/** Which capability edits which bucket: the fallback while the payload loads. */
const BUCKET_CAPABILITY: Record<SkillBucket, string> = {
  must_have: CAP.editMustHaveSkills,
  nice_to_have: CAP.editNiceToHaveSkills,
  behavioural: CAP.editBehaviouralCompetencies,
};

/** Where a skill came from, as a word. Provenance is read, not decoded. */
const SOURCE_LABEL: Record<SkillSource, string> = {
  jd: "From the JD",
  swot: "From the SWOT",
  company: "From the Company Profile",
  team: "Added by your team",
};

export type SkillSource = "swot" | "jd" | "company" | "team";

export interface SkillOut {
  id: string;
  name: string;
  source: SkillSource;
  /** The reporting authority's own SWOT sentence, verbatim, or null. */
  from_swot: string | null;
}

export type SkillsDraftStatus = "not_started" | "drafting" | "drafted" | "failed";

export interface SkillsOut {
  job_id: string;
  draft_status: SkillsDraftStatus;
  /** Fixed server copy for a failed draft. Rendered verbatim. */
  draft_error: string | null;
  saved: boolean;
  locked: boolean;
  max_per_bucket: number;
  redraft_available: boolean;
  /** Active skills the team added or renamed, which a re-draft replaces. */
  human_authored_names: string[];
  /** Why the skills cannot be saved yet, in the server's words. */
  blocking_reason: string | null;
  buckets: Record<SkillBucket, SkillOut[]>;
  can_edit: Record<SkillBucket, boolean>;
  can_save: boolean;
}

/** How often a drafting job is re-read, and for how long. The server serves a
 *  stale draft as failed after its own window, so the cap is a backstop. */
const DRAFT_POLL_MS = 3000;
const DRAFT_POLL_LIMIT = 300;

const LOCKED_SENTENCE =
  "Locked: a candidate has started the assessment. The skills and the grade can no longer change.";

const NUMBER_WORDS = [
  "None",
  "One",
  "Two",
  "Three",
  "Four",
  "Five",
  "Six",
  "Seven",
  "Eight",
  "Nine",
  "Ten",
  "Eleven",
  "Twelve",
  "Thirteen",
  "Fourteen",
  "Fifteen",
  "Sixteen",
  "Seventeen",
  "Eighteen",
  "Nineteen",
  "Twenty",
];

/** A count in words, capitalised: "Three". Past twenty it says so in words. */
export function countWord(n: number): string {
  return NUMBER_WORDS[n] ?? "More than twenty";
}

/** "Three of five", the bucket counter, with no digit in it. */
export function bucketCount(n: number, max: number): string {
  return `${countWord(n)} of ${countWord(max).toLowerCase()}`;
}

/** One pasted block to a list of names: newline or comma, blanks and repeats out. */
export function parseNames(raw: string): string[] {
  return Array.from(
    new Set(
      raw
        .split(/[\n,]+/)
        .map((value) => value.trim())
        .filter(Boolean)
    )
  );
}

/**
 * Every sentence a refusal carries, exactly as the server wrote it.
 *
 * FastAPI wraps a refusal as `{detail: ...}`. The skills routes send a string,
 * a list of strings (a validation that names every problem), or an object with
 * a `message` and a list of the skills it concerns. Anything else falls back
 * to the message the API client already derived, never to invented copy.
 */
export function serverSentences(error: unknown): string[] {
  if (error instanceof ApiError) {
    const body = error.detail;
    const detail =
      body && typeof body === "object" && "detail" in (body as object)
        ? (body as { detail: unknown }).detail
        : undefined;
    if (typeof detail === "string" && detail.trim()) return [detail];
    if (Array.isArray(detail)) {
      const lines = detail
        .map((item) =>
          typeof item === "string"
            ? item
            : item && typeof item === "object" && "msg" in item
              ? String((item as { msg: unknown }).msg)
              : ""
        )
        .filter(Boolean);
      if (lines.length) return lines;
    }
    if (detail && typeof detail === "object") {
      const record = detail as Record<string, unknown>;
      const lines: string[] = [];
      if (typeof record.message === "string") lines.push(record.message);
      for (const key of ["problems", "errors", "refused"]) {
        const list = record[key];
        if (Array.isArray(list)) {
          for (const item of list) {
            if (typeof item === "string") lines.push(item);
            else if (item && typeof item === "object") {
              const entry = item as Record<string, unknown>;
              const text = [entry.name, entry.reason]
                .filter((part) => typeof part === "string" && part)
                .join(": ");
              if (text) lines.push(text);
            }
          }
        }
      }
      if (lines.length) return lines;
    }
  }
  return [error instanceof Error ? error.message : String(error)];
}

function skillsPath(jobId: string, suffix = ""): string {
  return `${BASE}/${jobId}/skills${suffix}`;
}

// ── One skill ────────────────────────────────────────────────────────────────

function SkillChip({
  skill,
  bucket,
  canEdit,
  moveTargets,
  busy,
  onRename,
  onMove,
  onRemove,
}: {
  skill: SkillOut;
  bucket: SkillBucket;
  canEdit: boolean;
  /** Buckets this skill may be moved to, with whether each has room. */
  moveTargets: { bucket: SkillBucket; full: boolean }[];
  busy: boolean;
  onRename: (name: string) => Promise<boolean>;
  onMove: (target: SkillBucket) => Promise<boolean>;
  onRemove: () => Promise<boolean>;
}) {
  const [mode, setMode] = React.useState<"view" | "rename" | "move">("view");
  const [name, setName] = React.useState(skill.name);

  React.useEffect(() => {
    if (mode === "view") setName(skill.name);
  }, [skill.name, mode]);

  const provenance = SOURCE_LABEL[skill.source] ?? null;

  if (mode === "rename") {
    return (
      <li className="rounded-lg border p-2">
        <form
          className="flex items-center gap-2"
          onSubmit={async (event) => {
            event.preventDefault();
            const next = name.trim();
            if (!next || next === skill.name) {
              setMode("view");
              return;
            }
            if (await onRename(next)) setMode("view");
          }}
        >
          <Input
            aria-label={`Rename ${skill.name}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={255}
            autoFocus
          />
          <Button
            type="submit"
            size="sm"
            variant="outline"
            disabled={busy || !name.trim()}
            aria-label="Save the new name"
          >
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setMode("view")}
            aria-label="Cancel renaming"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </form>
      </li>
    );
  }

  return (
    <li className="rounded-lg border p-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="break-words text-sm font-medium">{skill.name}</p>
          {provenance ? (
            <p
              className="text-xs"
              title={skill.from_swot ? `You said: "${skill.from_swot}"` : undefined}
            >
              {provenance}
            </p>
          ) : null}
        </div>
        {canEdit ? (
          <div className="flex shrink-0 items-center gap-0.5">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 w-7 p-0"
              disabled={busy}
              aria-label={`Rename ${skill.name}`}
              onClick={() => setMode("rename")}
            >
              <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
            {moveTargets.length > 0 ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 w-7 p-0"
                disabled={busy}
                aria-label={`Move ${skill.name}`}
                aria-expanded={mode === "move"}
                onClick={() => setMode(mode === "move" ? "view" : "move")}
              >
                <ArrowRightLeft className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 w-7 p-0"
              disabled={busy}
              aria-label={`Remove ${skill.name}`}
              onClick={() => void onRemove()}
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </Button>
          </div>
        ) : null}
      </div>
      {mode === "move" ? (
        <div
          role="group"
          aria-label={`Move ${skill.name} from ${BUCKET_LABEL[bucket]}`}
          className="mt-2 flex flex-wrap gap-1.5"
        >
          {moveTargets.map((target) => (
            <Button
              key={target.bucket}
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || target.full}
              onClick={async () => {
                if (await onMove(target.bucket)) setMode("view");
              }}
            >
              {target.full
                ? `${BUCKET_LABEL[target.bucket]} is full`
                : `Move to ${BUCKET_LABEL[target.bucket]}`}
            </Button>
          ))}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setMode("view")}
          >
            Cancel
          </Button>
        </div>
      ) : null}
    </li>
  );
}

// ── Adding: one line, or a pasted list ───────────────────────────────────────

function AddSkills({
  bucket,
  busy,
  onAdd,
  onPaste,
}: {
  bucket: SkillBucket;
  busy: boolean;
  onAdd: (name: string) => Promise<boolean>;
  onPaste: (names: string[]) => Promise<boolean>;
}) {
  const [name, setName] = React.useState("");
  const [pasting, setPasting] = React.useState(false);
  const [pasted, setPasted] = React.useState("");
  const label = BUCKET_LABEL[bucket];

  if (pasting) {
    const names = parseNames(pasted);
    return (
      <div className="space-y-2">
        <Textarea
          aria-label={`Paste a list of ${label} skills`}
          rows={4}
          value={pasted}
          placeholder="One per line, or separated by commas"
          onChange={(e) => setPasted(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            disabled={busy || names.length === 0}
            onClick={async () => {
              if (await onPaste(names)) {
                setPasted("");
                setPasting(false);
              }
            }}
          >
            Add the list
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              setPasted("");
              setPasting(false);
            }}
          >
            Cancel
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <form
        className="flex items-center gap-2"
        onSubmit={async (event) => {
          event.preventDefault();
          const next = name.trim();
          if (!next) return;
          if (await onAdd(next)) setName("");
        }}
      >
        <Input
          aria-label={`Add a ${label} skill`}
          placeholder={BUCKET_PLACEHOLDER[bucket]}
          value={name}
          maxLength={255}
          onChange={(e) => setName(e.target.value)}
        />
        <Button
          type="submit"
          size="sm"
          variant="outline"
          disabled={busy || !name.trim()}
          aria-label={`Add to ${label}`}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </form>
      <button
        type="button"
        className="text-xs underline underline-offset-2"
        onClick={() => setPasting(true)}
      >
        Paste a list
      </button>
    </div>
  );
}

// ── The panel ────────────────────────────────────────────────────────────────

export function JobSkillsPanel({
  jobId,
  reloadKey = 0,
  redraftSignal = 0,
  onChanged,
  className,
}: {
  jobId: string;
  /** Bump to re-read the skills, e.g. after a SWOT save dispatched a draft. */
  reloadKey?: number;
  /** Bump to open the re-draft confirmation, e.g. from the SWOT panel's
   *  "Re-draft skills from the updated SWOT". Never acts without a click. */
  redraftSignal?: number;
  /** Told after every successful write, so the publish checklist re-reads. */
  onChanged?: () => void;
  className?: string;
}) {
  const { toast } = useToast();
  const { can } = usePermissions();

  const [view, setView] = React.useState<SkillsOut | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [refusal, setRefusal] = React.useState<string[] | null>(null);
  const [confirmRedraft, setConfirmRedraft] = React.useState(false);
  const [pollExhausted, setPollExhausted] = React.useState(false);
  const polls = React.useRef(0);
  const panelRef = React.useRef<HTMLDivElement>(null);

  const load = React.useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      setLoadError(null);
      try {
        const res = await apiGet<SkillsOut>(skillsPath(jobId));
        setView(res);
      } catch (error) {
        setLoadError(
          error instanceof Error ? error.message : "The skills could not be loaded."
        );
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [jobId]
  );

  React.useEffect(() => {
    polls.current = 0;
    setPollExhausted(false);
    void load();
  }, [load, reloadKey]);

  // A draft is dispatched work: re-read until it lands or fails. The server
  // turns a stale draft into a failed one, so this ends on its own; the limit
  // is only there so a broken server cannot keep a tab polling for ever.
  const drafting = view?.draft_status === "drafting";
  React.useEffect(() => {
    if (!drafting) return;
    if (polls.current >= DRAFT_POLL_LIMIT) {
      setPollExhausted(true);
      return;
    }
    const timer = window.setTimeout(() => {
      polls.current += 1;
      void load(true);
    }, DRAFT_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [drafting, view, load]);

  // The SWOT panel's re-draft call to action lands here. It opens the
  // confirmation and nothing more: a re-draft is never silent.
  const lastSignal = React.useRef(redraftSignal);
  React.useEffect(() => {
    if (redraftSignal === lastSignal.current) return;
    lastSignal.current = redraftSignal;
    panelRef.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    setConfirmRedraft(true);
  }, [redraftSignal]);

  const bucketEditable = (bucket: SkillBucket): boolean =>
    !view?.locked &&
    resolvePermission(can(BUCKET_CAPABILITY[bucket]), view?.can_edit?.[bucket]);

  const canRedraft =
    !!view && !view.locked && BUCKETS.every((bucket) => bucketEditable(bucket));

  /** Run one write. The response is the whole new view; a refusal is shown
   *  verbatim and the view is re-read, so the screen never shows a state the
   *  server did not keep. */
  const write = async (
    request: () => Promise<SkillsOut>,
    success?: string
  ): Promise<boolean> => {
    setBusy(true);
    setRefusal(null);
    try {
      const next = await request();
      setView(next);
      onChanged?.();
      if (success) toast({ title: success });
      return true;
    } catch (error) {
      setRefusal(serverSentences(error));
      void load(true);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const add = (bucket: SkillBucket, name: string) =>
    write(() => apiPost<SkillsOut>(skillsPath(jobId), { bucket, name }));

  const paste = (bucket: SkillBucket, names: string[]) =>
    write(() =>
      apiPost<SkillsOut>(skillsPath(jobId, "/bulk"), { bucket, names })
    );

  const rename = (skill: SkillOut, name: string) =>
    write(() => apiPatch<SkillsOut>(skillsPath(jobId, `/${skill.id}`), { name }));

  const move = (skill: SkillOut, bucket: SkillBucket) =>
    write(() =>
      apiPatch<SkillsOut>(skillsPath(jobId, `/${skill.id}`), { bucket })
    );

  const remove = (skill: SkillOut) =>
    write(() => apiDelete<SkillsOut>(skillsPath(jobId, `/${skill.id}`)));

  const redraft = async () => {
    if (!view) return;
    setConfirmRedraft(false);
    polls.current = 0;
    setPollExhausted(false);
    await write(
      () =>
        apiPost<SkillsOut>(skillsPath(jobId, "/draft"), {
          // Consent to replacing the team's own skills was given in the
          // dialog, which named them. With none, there is nothing to consent to.
          confirm_overwrite: view.human_authored_names.length > 0,
        }),
      "Sutra is drafting the skills"
    );
  };

  const save = async () => {
    setSaving(true);
    setRefusal(null);
    try {
      const next = await apiPost<SkillsOut>(skillsPath(jobId, "/save"));
      setView(next);
      onChanged?.();
      toast({
        title: "Skills saved",
        description: "Every candidate on this job is assessed against these skills.",
      });
    } catch (error) {
      // 503 (the assessment writer is down), 422 (every problem named) and
      // 409 (locked, or the skills changed underneath) all arrive worded.
      setRefusal(serverSentences(error));
      void load(true);
    } finally {
      setSaving(false);
    }
  };

  const total = view
    ? BUCKETS.reduce((sum, bucket) => sum + view.buckets[bucket].length, 0)
    : 0;
  const anyEditable = BUCKETS.some((bucket) => bucketEditable(bucket));
  const controlsBusy = busy || saving || drafting;

  let stateSentence: string | null = null;
  if (view) {
    if (view.locked) stateSentence = LOCKED_SENTENCE;
    else if (drafting)
      stateSentence =
        "Sutra is drafting the skills from the JD, the saved SWOT and the Company Profile.";
    else if (view.saved)
      stateSentence = "Saved. Every candidate on this job is assessed against these skills.";
    else if (view.blocking_reason) stateSentence = view.blocking_reason;
    else if (view.draft_status === "not_started" && total === 0)
      stateSentence = "Save the SWOT to draft skills, or add them yourself.";
    else stateSentence = "Not saved yet. Candidates cannot be invited until the skills are saved.";
  }

  return (
    <Card
      id="job-skills"
      ref={panelRef}
      className={cn("mb-6 scroll-mt-6", className)}
    >
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-4 space-y-0">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            Skills
            {view?.locked ? (
              <Badge variant="secondary">Locked</Badge>
            ) : view?.saved ? (
              <Badge variant="secondary">Saved</Badge>
            ) : null}
          </CardTitle>
          <CardDescription>
            What every candidate on this job is assessed against. At most five
            in each list.
          </CardDescription>
        </div>
        {view && canRedraft && view.redraft_available && !drafting ? (
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            disabled={controlsBusy}
            onClick={() => setConfirmRedraft(true)}
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            Re-draft skills from the updated SWOT
          </Button>
        ) : null}
      </CardHeader>

      <CardContent className="space-y-4 text-sm">
        {loading ? (
          <LoadingRows rows={3} label="Loading the skills" />
        ) : loadError || !view ? (
          <ErrorState
            title="The skills could not be loaded"
            description={loadError ?? undefined}
            action={
              <Button variant="outline" onClick={() => void load()}>
                Try again
              </Button>
            }
          />
        ) : (
          <>
            {stateSentence ? (
              <p
                role="status"
                className={cn(
                  "flex items-start gap-2",
                  view.locked && "font-medium"
                )}
              >
                {view.locked ? (
                  <Lock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                ) : drafting ? (
                  <Loader2
                    className="mt-0.5 h-4 w-4 shrink-0 motion-safe:animate-spin"
                    aria-hidden="true"
                  />
                ) : null}
                <span>{stateSentence}</span>
              </p>
            ) : null}

            {drafting && pollExhausted ? (
              <p role="status">
                The draft is taking longer than expected. Refresh the page to
                check on it.
              </p>
            ) : null}

            {view.draft_status === "failed" && !view.locked ? (
              <div role="alert" className="space-y-2 border border-destructive/40 p-4">
                <p className="font-medium">The skills draft did not finish</p>
                {view.draft_error ? <p>{view.draft_error}</p> : null}
                {canRedraft ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={controlsBusy}
                    onClick={() => setConfirmRedraft(true)}
                  >
                    Draft again
                  </Button>
                ) : null}
              </div>
            ) : null}

            {refusal && refusal.length > 0 ? (
              <div role="alert" className="border border-destructive/40 p-4">
                {refusal.length === 1 ? (
                  <p>{refusal[0]}</p>
                ) : (
                  <ul className="list-disc space-y-1 pl-5">
                    {refusal.map((line, index) => (
                      <li key={`${index}-${line}`}>{line}</li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null}

            {!view.locked && !anyEditable ? (
              <ReadOnlyNotice canEdit={false} resource="this job's skill list" />
            ) : null}

            <div className="grid gap-4 lg:grid-cols-3">
              {BUCKETS.map((bucket) => {
                const skills = view.buckets[bucket] ?? [];
                const editable = bucketEditable(bucket) && !drafting;
                const full = skills.length >= view.max_per_bucket;
                return (
                  <section
                    key={bucket}
                    aria-label={BUCKET_LABEL[bucket]}
                    className="space-y-3 rounded-xl border p-4"
                  >
                    <header className="space-y-0.5">
                      <div className="flex items-baseline justify-between gap-2">
                        <h3 className="font-semibold">{BUCKET_LABEL[bucket]}</h3>
                        <p className="shrink-0 text-xs">
                          {bucketCount(skills.length, view.max_per_bucket)}
                        </p>
                      </div>
                      <p className="text-xs">{BUCKET_HINT[bucket]}</p>
                    </header>

                    {skills.length === 0 ? (
                      <p className="text-xs">No skills in this list yet.</p>
                    ) : (
                      <ul className="space-y-2">
                        {skills.map((skill) => (
                          <SkillChip
                            key={skill.id}
                            skill={skill}
                            bucket={bucket}
                            canEdit={editable}
                            busy={controlsBusy}
                            moveTargets={BUCKETS.filter(
                              (target) =>
                                target !== bucket && bucketEditable(target)
                            ).map((target) => ({
                              bucket: target,
                              full:
                                (view.buckets[target] ?? []).length >=
                                view.max_per_bucket,
                            }))}
                            onRename={(name) => rename(skill, name)}
                            onMove={(target) => move(skill, target)}
                            onRemove={() => remove(skill)}
                          />
                        ))}
                      </ul>
                    )}

                    {editable ? (
                      full ? (
                        <p className="text-xs">
                          This list is full. Remove or move a skill to add
                          another.
                        </p>
                      ) : (
                        <AddSkills
                          bucket={bucket}
                          busy={controlsBusy}
                          onAdd={(name) => add(bucket, name)}
                          onPaste={(names) => paste(bucket, names)}
                        />
                      )
                    ) : !view.locked && anyEditable && !drafting ? (
                      <ReadOnlyNotice
                        canEdit={false}
                        resource={`the ${BUCKET_LABEL[bucket]} list`}
                      />
                    ) : null}
                  </section>
                );
              })}
            </div>

            {view.can_save && !view.locked ? (
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <Button
                  className="gap-1.5"
                  disabled={controlsBusy || total === 0}
                  onClick={() => void save()}
                >
                  {saving ? (
                    <Loader2
                      className="h-4 w-4 motion-safe:animate-spin"
                      aria-hidden="true"
                    />
                  ) : (
                    <Save className="h-4 w-4" aria-hidden="true" />
                  )}
                  {saving ? "Saving" : view.saved ? "Save skills again" : "Save skills"}
                </Button>
                <p className="text-xs">
                  Saving writes what the assessment needs to know about each
                  skill. It can take a few seconds.
                </p>
              </div>
            ) : null}
          </>
        )}
      </CardContent>

      <Dialog open={confirmRedraft} onOpenChange={setConfirmRedraft}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Re-draft the skills?</DialogTitle>
            <DialogDescription>
              Sutra replaces the current skills with a fresh draft from the JD,
              the saved SWOT and the Company Profile. The new draft has to be
              saved again before candidates can be invited.
            </DialogDescription>
          </DialogHeader>
          {view && view.human_authored_names.length > 0 ? (
            <div className="space-y-2 text-sm">
              <p className="font-medium">
                Your team added or renamed these, and the re-draft replaces
                them:
              </p>
              <ul className="list-disc space-y-1 pl-5">
                {view.human_authored_names.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {!canRedraft && view && !view.locked ? (
            <ReadOnlyNotice
              canEdit={false}
              resource="this job's skill list"
            />
          ) : null}
          {view?.locked ? <p className="text-sm">{LOCKED_SENTENCE}</p> : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmRedraft(false)}>
              Keep the current skills
            </Button>
            {canRedraft ? (
              <Button disabled={controlsBusy} onClick={() => void redraft()}>
                Replace with a new draft
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
