"use client";

/**
 * The three slide-over panels the row's action columns open.
 *
 * Ready Pick Profile (the system's reasoning), Team Review (a person's
 * independent read of it), and the Stage move. They are separate panels for
 * the reason the specification gives: conflating the first two would hide the
 * accountability layer, which is who relied on what, when, and what they
 * concluded independently.
 */

import * as React from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { apiGet, apiPost, apiPut } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";

import type {
  DashboardRow,
  ReadyPickProfile,
  StageOptions,
  TeamReviewPanel,
} from "./types";

const BASE = "/dashboard";

function PanelState({
  loading,
  error,
  children,
}: {
  loading: boolean;
  error: string | null;
  children: React.ReactNode;
}) {
  if (loading) {
    return (
      <p className="flex items-center gap-2 py-8 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Loading
      </p>
    );
  }
  // NO SILENT FALLBACK. A panel that failed to load says so; it never renders
  // an empty shape, which is indistinguishable from a candidate the engine
  // found nothing on.
  if (error) {
    return (
      <p className="rounded-xl border border-dashed p-5 text-sm" role="alert">
        {error}
      </p>
    );
  }
  return <>{children}</>;
}

/* ── Column 6: the Ready Pick Profile panel ──────────────────────────────── */

export function ReadyPickProfilePanel({
  row,
  open,
  onOpenChange,
}: {
  row: DashboardRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [profile, setProfile] = React.useState<ReadyPickProfile | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open || !row) return;
    let active = true;
    setLoading(true);
    setError(null);
    apiGet<ReadyPickProfile>(
      `${BASE}/jobs/${row.job_id}/candidates/${row.link_id}/profile`
    )
      .then((result) => {
        if (active) setProfile(result);
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "The Ready Pick Profile could not be loaded."
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, row]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Ready Pick Profile</SheetTitle>
          <SheetDescription>
            {row?.full_name}
            <span className="ml-2 select-all font-mono text-[11px]">
              {row?.system_id}
            </span>
          </SheetDescription>
        </SheetHeader>
        <div className="mt-6 space-y-6">
          <PanelState loading={loading} error={error}>
            {profile ? (
              <>
                {profile.under_integrity_review ? (
                  <p
                    className="flex items-start gap-2 rounded-xl border border-warning bg-warning/10 p-4 text-sm"
                    role="status"
                  >
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    <span>
                      Held for integrity review. Nothing here rejects this
                      candidate; the stage control stays locked until an HR
                      Manager records a decision.
                    </span>
                  </p>
                ) : null}

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide">
                    Why this candidate
                  </h3>
                  <p className="mt-2 text-sm leading-6">
                    {profile.why_this_candidate ??
                      "No write-up has been produced for this candidate yet."}
                  </p>
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide">
                    Dimension ratings
                  </h3>
                  {/* NAMED ratings, never raw numbers (spec-doc6 D8 / C2). The
                      raw figures live in the calibration view, which two roles
                      reach and every read of which is logged. */}
                  <dl className="mt-2 divide-y rounded-xl border">
                    {profile.dimensions.map((dimension) => (
                      <div key={dimension.dimension} className="p-3">
                        <dt className="text-sm font-semibold">{dimension.label}</dt>
                        <dd className="mt-1 text-sm">
                          {dimension.rated ? (
                            <span className="capitalize">{dimension.rating}</span>
                          ) : (
                            <span className="italic">Not assessed</span>
                          )}
                          {dimension.insufficient_evidence ? (
                            <span className="ml-2 rounded border border-border px-1.5 py-0.5 text-[11px]">
                              Insufficient evidence
                            </span>
                          ) : null}
                        </dd>
                        <dd className="mt-1 text-[12px] leading-5">
                          {dimension.question}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </section>

                {profile.open_flags.length ? (
                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wide">
                      Open flags
                    </h3>
                    <ul className="mt-2 space-y-2">
                      {profile.open_flags.map((flag) => (
                        <li
                          key={flag.gate}
                          className="rounded-xl border border-warning p-3 text-sm"
                        >
                          <p className="font-semibold">{flag.gate}</p>
                          {flag.reasons.map((reason) => (
                            <p key={reason} className="mt-1 leading-5">
                              {reason}
                            </p>
                          ))}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                <section className="text-[12px] leading-5">
                  <h3 className="text-xs font-semibold uppercase tracking-wide">
                    Configuration
                  </h3>
                  <p className="mt-2">
                    Scorecard version {profile.scorecard_version ?? "not recorded"}.
                    Company DNA version{" "}
                    {profile.company_dna_version ?? "not recorded"}.
                  </p>
                  <p>Scoring mode: {profile.scoring_mode ?? "not recorded"}.</p>
                </section>
              </>
            ) : null}
          </PanelState>
        </div>
      </SheetContent>
    </Sheet>
  );
}

/* ── Column 7: the Team Review panel ─────────────────────────────────────── */

export function TeamReviewSheet({
  row,
  open,
  onOpenChange,
  onSaved,
}: {
  row: DashboardRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const [panel, setPanel] = React.useState<TeamReviewPanel | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [verdict, setVerdict] = React.useState<string | null>(null);
  const [remarks, setRemarks] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (!open || !row) return;
    let active = true;
    setLoading(true);
    setError(null);
    apiGet<TeamReviewPanel>(
      `${BASE}/jobs/${row.job_id}/candidates/${row.link_id}/team-review`
    )
      .then((result) => {
        if (!active) return;
        setPanel(result);
        const mine = result.entries.find((entry) => entry.editable);
        setVerdict(mine?.verdict ?? null);
        setRemarks(mine?.remarks ?? "");
      })
      .catch((cause) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "Team Review could not be loaded."
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, row]);

  const save = async () => {
    if (!row || !verdict) return;
    setSaving(true);
    try {
      const result = await apiPut<TeamReviewPanel>(
        `${BASE}/jobs/${row.job_id}/candidates/${row.link_id}/team-review`,
        { verdict, remarks }
      );
      setPanel(result);
      // NO NUDGE. A verdict that differs from the Ready Pick Score gets exactly
      // this toast and nothing else: no warning, no confirmation step, no
      // "are you sure", no colour change. spec-doc6 8.2 and PRODUCT.md.
      toast({ title: "Team Review saved" });
      onSaved();
    } catch (cause) {
      toast({
        title: "Team Review not saved",
        description:
          cause instanceof Error ? cause.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Team Review</SheetTitle>
          <SheetDescription>
            Your own independent read, recorded beside everyone else&apos;s.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-6 space-y-6">
          <PanelState loading={loading} error={error}>
            {panel ? (
              <>
                {panel.can_write ? (
                  <section className="space-y-3">
                    <fieldset>
                      <legend className="text-xs font-semibold uppercase tracking-wide">
                        Your verdict
                      </legend>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {panel.verdicts.map((value) => (
                          <label
                            key={value}
                            className={cn(
                              "cursor-pointer rounded-md border px-3 py-1.5 text-sm",
                              verdict === value
                                ? "border-navy-600 bg-navy-50 font-semibold"
                                : "border-border"
                            )}
                          >
                            <input
                              type="radio"
                              name="team-review-verdict"
                              className="sr-only"
                              value={value}
                              checked={verdict === value}
                              onChange={() => setVerdict(value)}
                            />
                            {panel.verdict_labels[value] ?? value}
                          </label>
                        ))}
                      </div>
                    </fieldset>
                    <label className="block text-xs font-semibold uppercase tracking-wide">
                      What you saw
                      <Textarea
                        className="mt-2"
                        rows={4}
                        value={remarks}
                        onChange={(event) => setRemarks(event.target.value)}
                      />
                    </label>
                    <Button
                      type="button"
                      onClick={save}
                      disabled={!verdict || !remarks.trim() || saving}
                    >
                      {saving ? "Saving" : "Save my review"}
                    </Button>
                  </section>
                ) : (
                  <p className="rounded-xl border border-dashed p-4 text-sm">
                    You can read this panel and cannot add to it.
                  </p>
                )}

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide">
                    The panel
                  </h3>
                  {panel.entries.length ? (
                    <ul className="mt-2 divide-y rounded-xl border">
                      {panel.entries.map((entry) => (
                        <li key={entry.id} className="p-3">
                          <p className="text-sm font-semibold">
                            {entry.verdict_label}
                            {/* RBAC 29: author and timestamp, always. */}
                            <span className="ml-2 font-normal">
                              {entry.reviewer_email ?? entry.reviewer_user_id}
                            </span>
                          </p>
                          <p className="mt-1 whitespace-pre-wrap text-sm leading-6">
                            {entry.remarks}
                          </p>
                          <p className="mt-1 text-[11px]">
                            {new Date(entry.updated_at).toLocaleString()}
                            {entry.editable ? " (yours)" : ""}
                          </p>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 rounded-xl border border-dashed p-4 text-sm">
                      Nobody has reviewed this candidate yet.
                    </p>
                  )}
                </section>
              </>
            ) : null}
          </PanelState>
        </div>
      </SheetContent>
    </Sheet>
  );
}

/* ── Column 8: the Stage move ────────────────────────────────────────────── */

export function StageSheet({
  row,
  open,
  onOpenChange,
  onMoved,
  canDisposition,
}: {
  row: DashboardRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMoved: () => void;
  canDisposition: boolean;
}) {
  const { toast } = useToast();
  const [options, setOptions] = React.useState<StageOptions | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(() => {
    if (!row) return;
    setLoading(true);
    setError(null);
    apiGet<StageOptions>(
      `${BASE}/jobs/${row.job_id}/candidates/${row.link_id}/stage`
    )
      .then(setOptions)
      .catch((cause) =>
        setError(
          cause instanceof Error ? cause.message : "Stage could not be loaded."
        )
      )
      .finally(() => setLoading(false));
  }, [row]);

  React.useEffect(() => {
    if (open) load();
  }, [open, load]);

  const move = async (status: string) => {
    if (!row) return;
    setBusy(true);
    try {
      const result = await apiPost<StageOptions>(
        `${BASE}/jobs/${row.job_id}/candidates/${row.link_id}/stage`,
        { status }
      );
      setOptions(result);
      toast({ title: `Moved to ${result.stage_label}` });
      onMoved();
    } catch (cause) {
      toast({
        title: "Stage not changed",
        description:
          cause instanceof Error ? cause.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  const dispose = async (disposition: string) => {
    if (!row) return;
    setBusy(true);
    try {
      await apiPost(
        `${BASE}/jobs/${row.job_id}/candidates/${row.link_id}/integrity-disposition`,
        { disposition }
      );
      toast({ title: "Integrity finding dispositioned" });
      load();
      onMoved();
    } catch (cause) {
      toast({
        title: "Disposition not recorded",
        description:
          cause instanceof Error ? cause.message : "Please try again.",
        variant: "destructive",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Stage</SheetTitle>
          <SheetDescription>{row?.full_name}</SheetDescription>
        </SheetHeader>
        <div className="mt-6 space-y-5">
          <PanelState loading={loading} error={error}>
            {options ? (
              <>
                <p className="text-sm">
                  Currently <strong>{options.stage_label}</strong>.
                </p>
                {options.can_move ? (
                  <div className="flex flex-col gap-2">
                    {/* The options come from the server. The UI hardcodes no
                        stage list: the FSM is the only thing that knows which
                        moves are legal, and each stage carries a promise the
                        transition emails reference. */}
                    {options.allowed_transitions.map((option) => (
                      <Button
                        key={option.status}
                        type="button"
                        variant="outline"
                        disabled={busy}
                        onClick={() => move(option.status)}
                      >
                        {option.label}
                      </Button>
                    ))}
                  </div>
                ) : (
                  <p
                    className="rounded-xl border border-warning bg-warning/10 p-4 text-sm"
                    role="status"
                  >
                    {options.disabled_reason}
                  </p>
                )}

                {row?.under_integrity_review && canDisposition ? (
                  <section className="space-y-2 border-t pt-4">
                    <h3 className="text-xs font-semibold uppercase tracking-wide">
                      Integrity disposition
                    </h3>
                    <p className="text-[12px] leading-5">
                      Recording a decision is what unlocks the stage control.
                      All four are decisions; none of them is an approval, and
                      nothing here rejects the candidate on its own.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {["cleared", "escalated", "overridden", "rejected"].map(
                        (disposition) => (
                          <Button
                            key={disposition}
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() => dispose(disposition)}
                            className="capitalize"
                          >
                            {disposition}
                          </Button>
                        )
                      )}
                    </div>
                  </section>
                ) : null}
              </>
            ) : null}
          </PanelState>
        </div>
      </SheetContent>
    </Sheet>
  );
}
