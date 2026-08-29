"use client";

// Every version, with who wrote it and when.
//
// Authorship roles only. A version list is the record of who decided what this
// client's hiring philosophy is, and it is the thing the read-only roles are
// deliberately not given: they get the compiled artifact in force, which is
// what they need in order to understand why weights landed where they did.
//
// The reference is the VERSION, never the fingerprint. The fingerprint exists
// so two versions can be told apart by a person reading a support thread; it is
// shown in the monospace face the product already uses for a reference code,
// and it authorises nothing.

import * as React from "react";

import { apiGet } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState, LoadingRows, Section as Card } from "@/components/page-primitives";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { CompanyDnaVersionList } from "./types";
import { companyDnaPath } from "./types";

const STATUS_WORDS: Record<string, string> = {
  draft: "In progress",
  complete: "In force",
  superseded: "Replaced",
};

function when(value: string | null): string {
  if (!value) return "Not yet";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function VersionHistory({ clientId }: { clientId: string }) {
  const [data, setData] = React.useState<CompanyDnaVersionList | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      setData(
        await apiGet<CompanyDnaVersionList>(`${companyDnaPath(clientId)}/versions`)
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "The history could not be loaded."
      );
    }
  }, [clientId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <Card title="Version history">
        <p role="alert" className="text-sm leading-6">
          {error}
        </p>
        <Button className="mt-4" variant="outline" onClick={() => void load()}>
          Try again
        </Button>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card title="Version history">
        <LoadingRows rows={3} label="Loading version history" />
      </Card>
    );
  }

  if (data.items.length === 0) {
    return (
      <Card title="Version history">
        <EmptyState
          title="No versions yet"
          description="Once you complete the intake, every version you create is listed here with its author and date."
        />
      </Card>
    );
  }

  return (
    <Card
      title="Version history"
      description="Every version stays readable. A job assessed under an earlier one was assessed against that version's criteria, and that does not change when you write a new one."
    >
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Version</TableHead>
              <TableHead>State</TableHead>
              <TableHead>Written by</TableHead>
              <TableHead>Started</TableHead>
              <TableHead>Confirmed</TableHead>
              <TableHead>Reference</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.items.map((item) => (
              <TableRow key={item.version} className={item.is_current ? "bg-navy-50" : undefined}>
                <TableCell className="font-medium">
                  {item.version}
                  {item.is_current ? (
                    <span className="ml-2 rounded-full bg-teal-100 px-2 py-0.5 text-xs font-semibold">
                      In force
                    </span>
                  ) : null}
                </TableCell>
                <TableCell>{STATUS_WORDS[item.status] ?? item.status}</TableCell>
                <TableCell>{item.authored_by ?? "Not recorded"}</TableCell>
                <TableCell>{when(item.created_at)}</TableCell>
                <TableCell>{when(item.completed_at)}</TableCell>
                <TableCell className="font-mono text-xs">
                  {item.checksum ? item.checksum.slice(0, 12) : "Not compiled"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}
