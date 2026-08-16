"use client";

// Customer Portal → Compliance. The WRITE half of the Provider Portal's
// compliance section: the HR Head files their company's own tax and commercial
// records here, and ReadyPick reads them read-only.
//
// Reachable only by someone holding `manage_compliance_documents`, granted to
// the Company Admin by default. A recruiter who navigates here directly gets
// the same 403 the API returns, surfaced as a message rather than an empty
// page, so the answer is "not yours to file" instead of "something broke".

import * as React from "react";

import { API_BASE, ApiError, apiDelete, apiGet, apiUpload } from "@/lib/api";
import type { ComplianceDocumentType, ComplianceSlot } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingRows } from "@/components/page-primitives";
import { ComplianceDocumentsSection } from "@/components/compliance-documents-section";

export default function CompliancePage() {
  const { toast } = useToast();
  const [slots, setSlots] = React.useState<ComplianceSlot[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [forbidden, setForbidden] = React.useState(false);
  const [busyType, setBusyType] =
    React.useState<ComplianceDocumentType | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setSlots(await apiGet<ComplianceSlot[]>("/companies/me/compliance-documents"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setForbidden(true);
      } else {
        setLoadError(
          error instanceof Error ? error.message : "Could not load documents."
        );
      }
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const upload = async (type: ComplianceDocumentType, file: File) => {
    setBusyType(type);
    try {
      const form = new FormData();
      form.append("document_type", type);
      form.append("file", file);
      // The endpoint returns the whole slot list, so the page never has to
      // merge a single row back into the seven.
      setSlots(
        await apiUpload<ComplianceSlot[]>("/companies/me/compliance-documents", form)
      );
      toast({ title: "Document filed", description: file.name });
    } catch (error) {
      toast({
        title: "Could not file the document",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusyType(null);
    }
  };

  const remove = async (type: ComplianceDocumentType) => {
    setBusyType(type);
    try {
      setSlots(
        await apiDelete<ComplianceSlot[]>(
          `/companies/me/compliance-documents/${type}`
        )
      );
      toast({ title: "Document removed" });
    } catch (error) {
      toast({
        title: "Could not remove the document",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setBusyType(null);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Compliance and legal documents"
        description="Your company's tax and commercial records. ReadyPick can view these; only your company can upload or replace them."
      />

      {forbidden ? (
        <ErrorState
          title="Managed by your Company Admin"
          description="Ask them to file these records, or to grant you access."
        />
      ) : loadError ? (
        <ErrorState
          title="Documents could not be loaded"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          }
        />
      ) : loading ? (
        <LoadingRows rows={7} label="Loading documents" />
      ) : (
        <ComplianceDocumentsSection
          slots={slots}
          busyType={busyType}
          onUpload={upload}
          onRemove={remove}
          documentHref={(slot, inline) =>
            `${API_BASE}/companies/me/compliance-documents/${slot.document_type}/download${
              inline ? "?inline=true" : ""
            }`
          }
        />
      )}
    </div>
  );
}
