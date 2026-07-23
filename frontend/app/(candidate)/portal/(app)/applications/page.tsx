"use client";

// Application stage status tracker (FR-9.1).

import * as React from "react";

import { apiGet } from "@/lib/api";
import type { PortalApplication } from "@/lib/types";
import { PageHeader } from "@/components/app-shell";
import { StatusBadge } from "@/components/status-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export default function PortalApplicationsPage() {
  const [applications, setApplications] = React.useState<PortalApplication[]>([]);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    apiGet<PortalApplication[] | { applications: PortalApplication[] }>(
      "/portal/applications"
    )
      .then((res) =>
        setApplications(Array.isArray(res) ? res : res.applications ?? [])
      )
      .catch(() => setApplications([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <PageHeader
        title="My Applications"
        description="Current stage of each application."
      />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Job</TableHead>
            <TableHead>Company</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Last update</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          ) : applications.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                No applications yet.
              </TableCell>
            </TableRow>
          ) : (
            applications.map((a) => (
              <TableRow key={a.id}>
                <TableCell className="font-medium">{a.job_title}</TableCell>
                <TableCell>{a.company_name ?? "—"}</TableCell>
                <TableCell>
                  <StatusBadge status={a.status} />
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {a.updated_at
                    ? new Date(a.updated_at).toLocaleDateString()
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
