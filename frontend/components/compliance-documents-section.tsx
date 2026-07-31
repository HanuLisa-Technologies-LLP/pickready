"use client";

// The seven compliance & legal records, in two groups (Provider Portal §3.2).
//
// ONE component serves both portals, because the two views must never disagree
// about what is on file. The Provider renders it read-only; the customer's HR
// Head renders it with `onUpload` and `onRemove` and gets the upload controls.
// Read-only is the DEFAULT here for the same reason it is on the server: the
// Provider path passes no handlers, so there is no upload affordance to hide.
//
// Every slot is always rendered, present or not. A missing PAN card has to be
// a visible row saying "Not Available Yet", a short list of what happens to
// exist makes an absent document invisible, which is the opposite of what a
// compliance view is for.

import * as React from "react";
import { Download, Eye, FileText, Trash2, Upload } from "lucide-react";

import type { ComplianceDocumentType, ComplianceSlot } from "@/lib/types";
import { Button } from "@/components/ui/button";

/** Matches services/document_storage.ALLOWED_DOCUMENT_EXTENSIONS. */
const ACCEPT = ".pdf,.jpg,.jpeg,.png";
export const UPLOAD_HINT = "PDF, JPG or PNG, up to 10 MB.";

const GROUP_TITLES: Record<string, string> = {
  tax: "Mandatory Indian Compliance & Tax Documents",
  commercial: "Vital Commercial & Legal Records",
};

const GROUP_ORDER = ["tax", "commercial"] as const;

function formatSize(bytes?: number | null): string | null {
  if (!bytes) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface ComplianceDocumentsSectionProps {
  slots: ComplianceSlot[];
  /** View/download URL builder. Both buttons need one; `inline` opens it. */
  documentHref: (slot: ComplianceSlot, inline: boolean) => string;
  /** Supplied only by the owning customer, its absence makes this read-only. */
  onUpload?: (type: ComplianceDocumentType, file: File) => Promise<void>;
  onRemove?: (type: ComplianceDocumentType) => Promise<void>;
  busyType?: ComplianceDocumentType | null;
}

export function ComplianceDocumentsSection({
  slots,
  documentHref,
  onUpload,
  onRemove,
  busyType = null,
}: ComplianceDocumentsSectionProps) {
  const editable = Boolean(onUpload);

  return (
    <section className="space-y-8">
      <p className="text-sm leading-6">
        {editable
          ? `Filed by your company and visible to PickReady. ${UPLOAD_HINT}`
          : "Filed by the customer's HR Head. Read-only."}
      </p>

      {GROUP_ORDER.map((group) => {
        const groupSlots = slots.filter((slot) => slot.group === group);
        if (groupSlots.length === 0) return null;
        return (
          <div key={group} className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-brand-600">
              {GROUP_TITLES[group]}
            </h3>
            <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface shadow-card">
              {groupSlots.map((slot) => (
                <ComplianceRow
                  key={slot.document_type}
                  slot={slot}
                  documentHref={documentHref}
                  onUpload={onUpload}
                  onRemove={onRemove}
                  busy={busyType === slot.document_type}
                />
              ))}
            </ul>
          </div>
        );
      })}
    </section>
  );
}

function ComplianceRow({
  slot,
  documentHref,
  onUpload,
  onRemove,
  busy,
}: {
  slot: ComplianceSlot;
  documentHref: (slot: ComplianceSlot, inline: boolean) => string;
  onUpload?: (type: ComplianceDocumentType, file: File) => Promise<void>;
  onRemove?: (type: ComplianceDocumentType) => Promise<void>;
  busy: boolean;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const document = slot.document;
  const size = formatSize(document?.size_bytes);

  const pick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Reset first: picking the SAME file twice must still fire a change event,
    // which it will not if the input still holds it.
    event.target.value = "";
    if (file && onUpload) void onUpload(slot.document_type, file);
  };

  return (
    <li className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <span
          className={
            document
              ? "grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-brand-100 text-accent-foreground"
              : "grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-dashed border-border"
          }
        >
          <FileText className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold">{slot.label}</p>
          {document ? (
            <p className="truncate text-xs leading-5">
              {document.file_name}
              {size ? ` · ${size}` : ""} · Uploaded
              {document.uploaded_by_name ? ` by ${document.uploaded_by_name}` : ""}{" "}
              on {new Date(document.uploaded_at).toLocaleDateString()}
            </p>
          ) : (
            <p className="text-xs font-medium leading-5 opacity-80">
              Not Available Yet
            </p>
          )}
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap gap-2">
        {document ? (
          <>
            <Button variant="outline" size="sm"  asChild>
              <a
                href={documentHref(slot, true)}
                target="_blank"
                rel="noopener noreferrer"
              >
                <Eye className="h-3.5 w-3.5" /> View
              </a>
            </Button>
            <Button variant="outline" size="sm"  asChild>
              <a href={documentHref(slot, false)}>
                <Download className="h-3.5 w-3.5" /> Download
              </a>
            </Button>
          </>
        ) : null}

        {onUpload ? (
          <>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={pick}
              aria-label={`Upload ${slot.label}`}
            />
            <Button
              variant="outline"
              size="sm"
              
              disabled={busy}
              onClick={() => inputRef.current?.click()}
            >
              <Upload className="h-3.5 w-3.5" />
              {busy ? "Uploading" : document ? "Replace" : "Upload"}
            </Button>
          </>
        ) : null}

        {document && onRemove ? (
          <Button
            variant="outline"
            size="sm"
            
            disabled={busy}
            onClick={() => void onRemove(slot.document_type)}
          >
            <Trash2 className="h-3.5 w-3.5" /> Remove
          </Button>
        ) : null}
      </div>
    </li>
  );
}
