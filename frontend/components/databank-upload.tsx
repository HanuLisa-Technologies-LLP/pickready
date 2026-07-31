"use client";

// Upload Candidate Data Bank (client change, 2026-07-28).
//
// The recruitment team can add up to 25 candidates to a job in one go by
// dropping in their resumes. Those candidates are tagged "Databank" in the
// Type of Procurement column, and from that point on they are treated exactly
// like anyone who applied: same parsing, same embedding, same matching, same
// assessment. The tag is provenance, not a different code path.
//
// Parsing happens in Celery, never in the request, so this returns as soon as
// the files are stored and the ratings fill in afterwards.

import * as React from "react";
import { Loader2, Upload } from "lucide-react";

import { API_BASE } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";

/** The server refuses a 26th file. Say so before the request, not after. */
const MAX_FILES = 25;

interface UploadResult {
  filename: string;
  ok: boolean;
  error?: string | null;
  identified?: boolean;
}

interface UploadResponse {
  received: number;
  created: number;
  failed: number;
  results: UploadResult[];
}

export function DatabankUpload({
  jobId,
  onUploaded,
  className,
}: {
  jobId: string;
  onUploaded?: () => void;
  className?: string;
}) {
  const { toast } = useToast();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [busy, setBusy] = React.useState(false);
  const [failures, setFailures] = React.useState<UploadResult[]>([]);

  const upload = async (files: FileList) => {
    if (files.length === 0) return;
    if (files.length > MAX_FILES) {
      toast({
        title: `Too many files`,
        description: `Up to ${MAX_FILES} resumes at a time. You selected ${files.length}.`,
        variant: "destructive",
      });
      return;
    }

    const body = new FormData();
    Array.from(files).forEach((file) => body.append("files", file));

    setBusy(true);
    setFailures([]);
    try {
      // A multipart upload cannot go through the JSON helper, so this is a
      // direct fetch. Credentials are the session cookie, same as everywhere.
      const res = await fetch(`${API_BASE}/jobs/${jobId}/candidates/databank`, {
        method: "POST",
        body,
        credentials: "include",
      });
      if (!res.ok) {
        let detail = `Upload failed (${res.status})`;
        try {
          const payload = await res.json();
          detail = apiErrorMessage(payload) || detail;
        } catch {
          // Keep the status-code message.
        }
        throw new Error(detail);
      }
      const data: UploadResponse = await res.json();
      // Partial success is expected and fine: one unreadable PDF must not
      // discard the other twenty-four.
      setFailures(data.results?.filter((r) => !r.ok) ?? []);
      toast({
        title: `${data.created} of ${data.received} added to the databank`,
        description:
          data.failed > 0
            ? `${data.failed} could not be read. See the list below.`
            : "Parsing and matching are running now. Ratings appear shortly.",
        variant: data.created === 0 ? "destructive" : undefined,
      });
      if (data.created > 0) onUploaded?.();
    } catch (error) {
      toast({
        title: "Could not upload the resumes",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div className={cn("flex flex-wrap items-start gap-3", className)}>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.doc,.docx,.txt,.rtf"
        className="hidden"
        onChange={(e) => e.target.files && void upload(e.target.files)}
      />
      <Button
        type="button"
        variant="secondary"
        className="gap-1.5"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Upload className="h-4 w-4" />
        )}
        {busy ? "Uploading" : "Upload Candidate Data Bank"}
      </Button>

      <div className="min-w-[16rem] flex-1">
        <p className="text-xs">
          Up to {MAX_FILES} resumes at a time. They join this job as Databank
          candidates and are parsed, matched and assessed like any other
          applicant.
        </p>
        {failures.length > 0 ? (
          <ul className="mt-2 space-y-1 text-xs">
            {failures.map((f) => (
              <li key={f.filename}>
                <span className="font-medium">{f.filename}</span>
                {f.error ? `: ${f.error}` : ": could not be read"}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
