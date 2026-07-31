"use client";

import * as React from "react";
import { Download, Loader2 } from "lucide-react";
import type { SheetData } from "write-excel-file/browser";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";

export type ExportCell = string | number | boolean | null | undefined;
export type ExportRow = Record<string, ExportCell>;

function titleFromKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function safeFileName(value: string): string {
  const base = value
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${base || "pickready-export"}-${new Date().toISOString().slice(0, 10)}.xlsx`;
}

export function ExportXlsxButton({
  rows,
  fileName,
  label = "Export .xlsx",
  disabled = false,
}: {
  rows: ExportRow[];
  fileName: string;
  label?: string;
  disabled?: boolean;
}) {
  const { toast } = useToast();
  const [working, setWorking] = React.useState(false);

  const download = async () => {
    if (working || rows.length === 0) return;
    setWorking(true);
    try {
      const keys = Array.from(
        rows.reduce((all, row) => {
          Object.keys(row).forEach((key) => all.add(key));
          return all;
        }, new Set<string>())
      );
      const header = keys.map((key) => ({
        value: titleFromKey(key),
        fontWeight: "bold" as const,
        backgroundColor: "#EEE9FF",
        color: "#25145C",
        height: 26,
      }));
      const body = rows.map((row) =>
        keys.map((key) => ({
          value: row[key] ?? "",
          wrap: true,
        }))
      );
      const sheetData: SheetData = [header, ...body];
      const { default: writeXlsxFile } = await import("write-excel-file/browser");
      const workbook = writeXlsxFile(sheetData, {
        columns: keys.map((key) => ({
          width: Math.min(
            44,
            Math.max(
              12,
              titleFromKey(key).length + 2,
              ...rows.map((row) => String(row[key] ?? "").length + 2)
            )
          ),
        })),
      });
      await workbook.toFile(safeFileName(fileName));
      toast({
        title: "Spreadsheet downloaded",
        description: `${rows.length} ${rows.length === 1 ? "row" : "rows"} exported.`,
      });
    } catch (error) {
      toast({
        variant: "destructive",
        title: "Could not create the spreadsheet",
        description: error instanceof Error ? error.message : "Try again in a moment.",
      });
    } finally {
      setWorking(false);
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      disabled={disabled || working || rows.length === 0}
      onClick={() => void download()}
    >
      {working ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <Download className="h-4 w-4" aria-hidden="true" />
      )}
      {working ? "Preparing" : label}
    </Button>
  );
}
