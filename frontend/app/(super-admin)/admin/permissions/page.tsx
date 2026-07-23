"use client";

// Permission matrix editor (FR-11.2): grid of role × capability toggles,
// per-tenant or the global template. Permissions are data, not code.

import * as React from "react";

import { apiGet, apiPut } from "@/lib/api";
import type { PermissionEntry, Tenant } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const GLOBAL = "__global__";

const ROLE_LABELS: Record<string, string> = {
  recruiter: "Recruiter",
  hr_manager: "HR Manager",
  hiring_manager: "Hiring Manager",
  client: "Client (Company)",
};

export default function PermissionsPage() {
  const { toast } = useToast();
  const [tenants, setTenants] = React.useState<Tenant[]>([]);
  const [scope, setScope] = React.useState<string>(GLOBAL);
  const [entries, setEntries] = React.useState<PermissionEntry[]>([]);
  const [dirty, setDirty] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    apiGet<Tenant[] | { tenants: Tenant[] }>("/admin/tenants")
      .then((res) => setTenants(Array.isArray(res) ? res : res.tenants ?? []))
      .catch(() => {});
  }, []);

  const load = React.useCallback(
    async (s: string) => {
      setLoading(true);
      setDirty(false);
      try {
        const qs = s === GLOBAL ? "" : `?tenant_id=${encodeURIComponent(s)}`;
        const res = await apiGet<
          PermissionEntry[] | { entries: PermissionEntry[] }
        >(`/admin/permissions${qs}`);
        setEntries(Array.isArray(res) ? res : res.entries ?? []);
      } catch (e) {
        toast({
          title: "Failed to load permissions",
          description: e instanceof Error ? e.message : undefined,
          variant: "destructive",
        });
        setEntries([]);
      } finally {
        setLoading(false);
      }
    },
    [toast]
  );

  React.useEffect(() => {
    void load(scope);
  }, [scope, load]);

  const roles = React.useMemo(() => {
    const found = Array.from(new Set(entries.map((e) => e.role)));
    const preferred = ["recruiter", "hr_manager", "hiring_manager", "client"];
    return preferred
      .filter((r) => found.includes(r))
      .concat(found.filter((r) => !preferred.includes(r)));
  }, [entries]);

  const capabilities = React.useMemo(
    () => Array.from(new Set(entries.map((e) => e.capability))),
    [entries]
  );

  const isAllowed = (role: string, capability: string) =>
    entries.find((e) => e.role === role && e.capability === capability)
      ?.allowed ?? false;

  const setAllowed = (role: string, capability: string, allowed: boolean) => {
    setEntries((prev) => {
      const idx = prev.findIndex(
        (e) => e.role === role && e.capability === capability
      );
      if (idx === -1) {
        return [...prev, { role, capability, allowed }];
      }
      const next = prev.slice();
      next[idx] = { ...next[idx], allowed };
      return next;
    });
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPut("/admin/permissions", {
        ...(scope === GLOBAL ? {} : { tenant_id: scope }),
        entries,
      });
      toast({ title: "Permissions saved" });
      setDirty(false);
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
    <div>
      <PageHeader
        title="Permission Matrix"
        description="Toggle capabilities per role — globally or per tenant. Audit-logged."
        actions={
          <div className="flex items-center gap-2">
            <Select value={scope} onValueChange={setScope}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={GLOBAL}>Global template</SelectItem>
                {tenants.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button onClick={() => void save()} disabled={!dirty || saving}>
              {saving ? "Saving…" : "Save changes"}
            </Button>
          </div>
        }
      />

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : capabilities.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No permission entries found for this scope.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="min-w-[280px]">Capability</TableHead>
              {roles.map((r) => (
                <TableHead key={r} className="text-center">
                  {ROLE_LABELS[r] ?? r}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {capabilities.map((cap) => (
              <TableRow key={cap}>
                <TableCell className="font-mono text-xs">{cap}</TableCell>
                {roles.map((role) => (
                  <TableCell key={role} className="text-center">
                    <Switch
                      checked={isAllowed(role, cap)}
                      onCheckedChange={(v) => setAllowed(role, cap, v)}
                      aria-label={`${role} — ${cap}`}
                    />
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
