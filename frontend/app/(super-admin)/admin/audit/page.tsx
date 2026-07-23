"use client";

// Audit log viewer (FR-11.3, PRD §8 auditability).

import * as React from "react";

import { apiGet } from "@/lib/api";
import type { AuditLogEntry, Tenant } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
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

const ALL = "__all__";

export default function AuditLogPage() {
  const { toast } = useToast();
  const [tenants, setTenants] = React.useState<Tenant[]>([]);
  const [scope, setScope] = React.useState<string>(ALL);
  const [entries, setEntries] = React.useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [limit, setLimit] = React.useState(100);

  React.useEffect(() => {
    apiGet<Tenant[] | { tenants: Tenant[] }>("/admin/tenants")
      .then((res) => setTenants(Array.isArray(res) ? res : res.tenants ?? []))
      .catch(() => {});
  }, []);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (scope !== ALL) params.set("tenant_id", scope);
      params.set("limit", String(limit));
      const res = await apiGet<
        AuditLogEntry[] | { entries: AuditLogEntry[] }
      >(`/admin/audit-log?${params.toString()}`);
      setEntries(Array.isArray(res) ? res : res.entries ?? []);
    } catch (e) {
      toast({
        title: "Failed to load audit log",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [scope, limit, toast]);

  React.useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <PageHeader
        title="Audit Log"
        description="Immutable record of approvals, permission changes, status changes and cross-tenant access."
        actions={
          <div className="flex items-center gap-2">
            <Select value={scope} onValueChange={setScope}>
              <SelectTrigger className="w-52">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All tenants</SelectItem>
                {tenants.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              onClick={() => setLimit((l) => l + 100)}
              disabled={loading}
            >
              Load more
            </Button>
          </div>
        }
      />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Time</TableHead>
            <TableHead>Actor</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Detail</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          ) : entries.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                No audit entries.
              </TableCell>
            </TableRow>
          ) : (
            entries.map((e) => (
              <TableRow key={e.id}>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                  {e.created_at ? new Date(e.created_at).toLocaleString() : "—"}
                </TableCell>
                <TableCell className="text-sm">
                  {e.actor_email ?? e.actor_id ?? "system"}
                </TableCell>
                <TableCell className="font-mono text-xs">{e.action}</TableCell>
                <TableCell className="max-w-md truncate text-xs text-muted-foreground">
                  {typeof e.detail === "string"
                    ? e.detail
                    : e.detail
                      ? JSON.stringify(e.detail)
                      : "—"}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
