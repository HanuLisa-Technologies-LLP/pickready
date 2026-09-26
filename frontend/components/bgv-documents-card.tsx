"use client";

// Verification documents on My Profile: academic certificates and address
// proof (vivekium feature 5, "Freshers: no employer BGV. Academic certificates
// and address proof only.").
//
// EVERY TYPE IS SHOWN, INCLUDING THE EMPTY ONES. The server returns the whole
// list of accepted types beside the documents, the same rule the compliance
// slots follow: a short list is one a missing address proof can hide in.
//
// WHAT THIS CARD DOES NOT CLAIM. A document here confirms nothing and gates
// nothing. There is no recruiter route to these files, so the copy says they
// stay on the candidate's profile; it never says an employer has seen or
// checked them, because nothing does.
//
// THE LIMITS ARE THE SERVER'S. The hint under the heading is rendered by the
// server from the settings that enforce it, and a refusal (a type it does not
// take, a file too large, one too many of a kind) is shown in its own words.

import * as React from "react";
import { FileCheck2, Loader2, Trash2, Upload } from "lucide-react";

import { apiDelete, apiGet, apiUpload } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { readableSize } from "@/lib/conversations";
import { InlineError } from "@/components/page-primitives";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";

/** Mirrors `schemas.bgv_workflow.BGVDocumentOut`. No object key, by design. */
export interface BgvDocument {
  id: string;
  document_type: string;
  document_label: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  uploaded_at: string;
}

/** Mirrors `schemas.bgv_workflow.BGVDocumentsOut`. */
export interface BgvDocumentsOut {
  documents: BgvDocument[];
  accepted_types: { key: string; label: string }[];
  upload_hint: string;
  max_per_type: number;
}

const ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";

export function BgvDocumentsCard() {
  const { toast } = useToast();
  const [state, setState] = React.useState<BgvDocumentsOut | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  // The one operation in flight, named so only its own control shows a spinner.
  const [busy, setBusy] = React.useState<string | null>(null);
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  const load = React.useCallback(async () => {
    try {
      setState(await apiGet<BgvDocumentsOut>("/bgv/me/documents"));
      setLoadError(null);
    } catch (failure) {
      setLoadError(apiErrorMessage(failure));
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const upload = async (type: string, file: File) => {
    setBusy(`upload:${type}`);
    setErrors((current) => ({ ...current, [type]: "" }));
    try {
      const form = new FormData();
      form.append("document_type", type);
      form.append("file", file);
      setState(await apiUpload<BgvDocumentsOut>("/bgv/me/documents", form));
      toast({ title: "Document added" });
    } catch (failure) {
      setErrors((current) => ({ ...current, [type]: apiErrorMessage(failure) }));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (document: BgvDocument) => {
    setBusy(`delete:${document.id}`);
    try {
      setState(await apiDelete<BgvDocumentsOut>(`/bgv/me/documents/${document.id}`));
      toast({ title: "Document removed" });
    } catch (failure) {
      toast({
        title: "The document was not removed",
        description: apiErrorMessage(failure),
        variant: "destructive",
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <FileCheck2 className="h-4 w-4" aria-hidden="true" />
          <CardTitle className="text-base">Verification documents</CardTitle>
        </div>
        <CardDescription>
          Your academic certificates and proof of address. They stay on your
          profile, and you can remove any of them at any time.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loadError && !state ? (
          <div className="space-y-3">
            <InlineError>
              Your documents could not be loaded. {loadError}
            </InlineError>
            <Button size="sm" variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        ) : !state ? (
          <p role="status" className="text-sm">
            Loading your documents
          </p>
        ) : (
          <>
            <p className="text-xs">{state.upload_hint}</p>
            <ul className="space-y-3">
              {state.accepted_types.map((type) => (
                <DocumentSlot
                  key={type.key}
                  typeKey={type.key}
                  label={type.label}
                  documents={state.documents.filter(
                    (document) => document.document_type === type.key,
                  )}
                  full={
                    state.documents.filter(
                      (document) => document.document_type === type.key,
                    ).length >= state.max_per_type
                  }
                  busy={busy}
                  error={errors[type.key] || null}
                  onUpload={(file) => void upload(type.key, file)}
                  onRemove={(document) => void remove(document)}
                />
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function DocumentSlot({
  typeKey,
  label,
  documents,
  full,
  busy,
  error,
  onUpload,
  onRemove,
}: {
  typeKey: string;
  label: string;
  documents: BgvDocument[];
  full: boolean;
  busy: string | null;
  error: string | null;
  onUpload: (file: File) => void;
  onRemove: (document: BgvDocument) => void;
}) {
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const inputId = `bgv-document-${typeKey}`;
  const uploading = busy === `upload:${typeKey}`;

  return (
    <li className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{label}</p>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          aria-label={`Add ${label}`}
          disabled={busy !== null || full}
          onChange={(event) => {
            const file = event.target.files?.[0];
            // Cleared so choosing the same file again after a refusal fires.
            event.target.value = "";
            if (file) onUpload(file);
          }}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy !== null || full}
          onClick={() => inputRef.current?.click()}
        >
          {uploading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Upload className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          <span className="ml-1">{uploading ? "Uploading" : "Add file"}</span>
        </Button>
      </div>
      {documents.length === 0 ? (
        <p className="text-xs">Not added yet.</p>
      ) : (
        <ul className="space-y-1">
          {documents.map((document) => (
            <li
              key={document.id}
              className="flex flex-wrap items-center justify-between gap-2 text-xs"
            >
              <span className="min-w-0 truncate">
                {document.original_filename} ({readableSize(document.size_bytes)},
                added {new Date(document.uploaded_at).toLocaleDateString()})
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy !== null}
                aria-label={`Remove ${document.original_filename}`}
                onClick={() => onRemove(document)}
              >
                {busy === `delete:${document.id}` ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                )}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {full ? (
        <p className="text-xs">
          This type is full. Remove a file to add a different one.
        </p>
      ) : null}
      {error ? <InlineError>{error}</InlineError> : null}
    </li>
  );
}
