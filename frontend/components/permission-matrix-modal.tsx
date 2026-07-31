"use client";

// The HR Head's per-person permission matrix (spec §7.1).
//
// Each capability has THREE states, and the UI has to show all three honestly,
// because "this box is ticked" means something different depending on why:
//
//   Follows role  , no pin; tracks the role default, and keeps tracking it if
//                    the role matrix changes later.
//   Granted       , pinned on for this person specifically.
//   Revoked       , pinned off for this person specifically.
//
// A two-state checkbox would collapse "follows role (currently on)" into
// "granted", silently freezing that capability for this person the next time
// the role default changes. So the control is a three-way selector, not a tick.

import * as React from "react";
import { Loader2, Save } from "lucide-react";

import { apiGet, apiPatch } from "@/lib/api";
import type { StaffPermissions } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type State = "inherit" | "grant" | "revoke";

/** Readable names for the capability strings. Anything not listed falls back
 *  to its raw name with underscores replaced, a new backend capability shows
 *  up usable rather than invisible. */
const CAPABILITY_LABELS: Record<string, string> = {
  create_job: "Create jobs",
  publish_job: "Publish jobs",
  edit_job_description: "Edit job descriptions",
  edit_company_profile: "Edit company profile",
  create_company_page: "Edit company page",
  trigger_matching: "Run AI matching",
  view_review_screen: "View candidates & reports",
  view_databank: "View the candidate databank",
  upload_resumes: "Upload resumes",
  send_outreach: "Send candidate emails",
  decide_profile: "Shortlist, reject, or hold",
  schedule_interviews: "Schedule interviews",
  update_pipeline_status: "Update application status",
  add_compensation: "Add compensation",
  view_dashboard: "View the dashboard",
  manage_staff: "Manage staff & permissions",
  manage_email_templates: "Manage email templates",
  configure_approval_levels: "Configure approval levels",
  approve_job: "Approve jobs",
  edit_role_permissions: "Edit role permissions (platform owner)",
};

const label = (capability: string) =>
  CAPABILITY_LABELS[capability] ?? capability.replaceAll("_", " ");

function stateOf(capability: string, overrides: Record<string, boolean>): State {
  if (!(capability in overrides)) return "inherit";
  return overrides[capability] ? "grant" : "revoke";
}

export function PermissionMatrixModal({
  open,
  onOpenChange,
  userId,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  userId: string | null;
  onSaved?: () => void;
}) {
  const { toast } = useToast();
  const [data, setData] = React.useState<StaffPermissions | null>(null);
  const [overrides, setOverrides] = React.useState<Record<string, boolean>>({});
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (!open || !userId) return;
    let cancelled = false;
    setLoading(true);
    apiGet<StaffPermissions>(`/companies/me/staff/${userId}/permissions`)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setOverrides({ ...res.overrides });
      })
      .catch((e) => {
        if (cancelled) return;
        toast({
          title: "Couldn't load permissions",
          description: e instanceof Error ? e.message : undefined,
          variant: "destructive",
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, userId, toast]);

  const setState = (capability: string, next: State) =>
    setOverrides((prev) => {
      const copy = { ...prev };
      if (next === "inherit") delete copy[capability];
      else copy[capability] = next === "grant";
      return copy;
    });

  const save = async () => {
    if (!userId) return;
    setSaving(true);
    try {
      const res = await apiPatch<StaffPermissions>(
        `/companies/me/staff/${userId}/permissions`,
        { overrides }
      );
      setData(res);
      setOverrides({ ...res.overrides });
      toast({ title: "Permissions saved", description: res.full_name ?? undefined });
      onOpenChange(false);
      onSaved?.();
    } catch (e) {
      toast({
        title: "Couldn't save permissions",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const roleDefaults = new Set(data?.role_defaults ?? []);
  const effective = new Set(
    (data?.all_capabilities ?? []).filter((c) =>
      c in overrides ? overrides[c] : roleDefaults.has(c)
    )
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            Permissions, {data?.full_name || data?.email || "team member"}
          </DialogTitle>
          <DialogDescription>
            Leave a permission on &ldquo;Follows role&rdquo; and it tracks the{" "}
            {data?.role?.replaceAll("_", " ") ?? "role"} default, including future
            changes. Grant or revoke to pin it for this person only.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : data ? (
          <ul className="divide-y">
            {data.all_capabilities.map((capability) => {
              const state = stateOf(capability, overrides);
              const isOn = effective.has(capability);
              return (
                <li
                  key={capability}
                  className="flex flex-wrap items-center justify-between gap-3 py-2.5"
                >
                  <div className="min-w-[220px]">
                    <p className="text-sm font-medium">{label(capability)}</p>
                    <p className="text-xs">
                      {isOn ? "Allowed" : "Not allowed"}
                      {state === "inherit"
                        ? ` · follows role (${roleDefaults.has(capability) ? "allowed" : "not allowed"})`
                        : state === "grant"
                          ? " · granted to this person"
                          : " · revoked for this person"}
                    </p>
                  </div>
                  <div
                    className="flex gap-1"
                    role="group"
                    aria-label={`${label(capability)} permission`}
                  >
                    {(
                      [
                        ["inherit", "Follows role"],
                        ["grant", "Grant"],
                        ["revoke", "Revoke"],
                      ] as [State, string][]
                    ).map(([value, text]) => (
                      <Button
                        key={value}
                        type="button"
                        size="sm"
                        variant={state === value ? "default" : "outline"}
                        aria-pressed={state === value}
                        onClick={() => setState(capability, value)}
                      >
                        {text}
                      </Button>
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : null}

        <DialogFooter className="items-center gap-3">
          <Badge variant="secondary">
            {Object.keys(overrides).length} pinned for this person
          </Badge>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={saving || loading} onClick={() => void save()} className="gap-1.5">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {saving ? "Saving" : "Save permissions"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
