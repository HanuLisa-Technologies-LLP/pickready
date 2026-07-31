"use client";

import { FileCheck2, FileText, RotateCcw, UploadCloud } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form";

const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPTED = ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"];

export function validateResumeFile(file: File): string | null {
  const extension = file.name.toLowerCase().split(".").pop();
  if (extension !== "pdf" && extension !== "docx") return "Choose a PDF or DOCX resume.";
  if (file.size === 0) return "The selected resume is empty.";
  if (file.size > MAX_BYTES) return "Resume files must be 10 MB or smaller.";
  if (file.type && !ACCEPTED.includes(file.type) && file.type !== "application/octet-stream") {
    return "The selected file type does not match a PDF or DOCX resume.";
  }
  return null;
}

export function ResumeFileInput({
  id,
  file,
  progress = 0,
  error,
  disabled,
  onFileChange,
  onRetry,
}: {
  id: string;
  file: File | null;
  progress?: number;
  error?: string | null;
  disabled?: boolean;
  onFileChange: (file: File | null, error: string | null) => void;
  onRetry?: () => void;
}) {
  const selectingDisabled = Boolean(disabled);
  return (
    <div className="space-y-3">
      <Input
        id={id}
        type="file"
        accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        disabled={selectingDisabled}
        onChange={(event) => {
          const selected = event.target.files?.[0] ?? null;
          onFileChange(selected, selected ? validateResumeFile(selected) : null);
        }}
      />
      {/* No format/size hint and no storage-provider detail here: the calling
          FormField already carries the "PDF or DOCX, up to 10 MB." hint, and
          repeating it rendered the line twice in the apply dialog. Naming the
          storage vendor to an applicant is never appropriate. */}
      {file ? (
        <div className="rounded-xl border border-border bg-secondary p-4 text-sm" aria-live="polite">
          <div className="flex items-center gap-2 font-medium"><FileText className="h-4 w-4" />{file.name}</div>
          <div className="mt-1 text-xs">{(file.size / 1024 / 1024).toFixed(2)} MB</div>
          {disabled ? (
            <div className="mt-3 space-y-1.5">
              <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${progress}%` }} /></div>
              <div className="flex items-center gap-1 text-xs"><UploadCloud className="h-3.5 w-3.5 animate-pulse" />Uploading securely, {progress}%</div>
            </div>
          ) : progress === 100 && !error ? <div className="mt-2 flex items-center gap-1 text-xs text-foreground"><FileCheck2 className="h-3.5 w-3.5" />Uploaded and linked to this application.</div> : null}
        </div>
      ) : null}
      {error ? <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive" role="alert"><span>{error}</span>{onRetry ? <button type="button" className="inline-flex items-center gap-1 underline" onClick={onRetry}><RotateCcw className="h-3.5 w-3.5" />Retry</button> : null}</div> : null}
    </div>
  );
}

/** What the backend knows about the resume already on the candidate's record. */
export interface StoredResume {
  has_resume: boolean;
  filename?: string | null;
  size_bytes?: number | null;
  uploaded_at?: string | null;
}

export type ResumeMode = "upload" | "reuse";

/**
 * Upload-or-reuse resume picker (FR-6.2 / claude.md rule 6). "Reuse my last
 * resume" only appears when the backend confirms a stored resume, so the
 * option is never a dead end.
 */
export function ResumeChoice({
  id,
  mode,
  onModeChange,
  stored,
  storedLoading,
  file,
  progress = 0,
  error,
  disabled,
  onFileChange,
  onRetry,
}: {
  id: string;
  mode: ResumeMode;
  onModeChange: (mode: ResumeMode) => void;
  stored: StoredResume | null;
  storedLoading?: boolean;
  file: File | null;
  progress?: number;
  error?: string | null;
  disabled?: boolean;
  onFileChange: (file: File | null, error: string | null) => void;
  onRetry?: () => void;
}) {
  const canReuse = Boolean(stored?.has_resume);
  return (
    <div className="space-y-3">
      {storedLoading ? (
        <p role="status" className="text-sm">
        Checking for a resume on your record
      </p>
      ) : canReuse ? (
        <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Resume option">
          <Button
            type="button"
            variant={mode === "reuse" ? "secondary" : "outline"}
            aria-pressed={mode === "reuse"}
            disabled={disabled}
            onClick={() => {
              onModeChange("reuse");
              onFileChange(null, null);
            }}
          >
            Main Resume
          </Button>
          <Button
            type="button"
            variant={mode === "upload" ? "secondary" : "outline"}
            aria-pressed={mode === "upload"}
            disabled={disabled}
            onClick={() => onModeChange("upload")}
          >
            Upload a New Resume
          </Button>
        </div>
      ) : (
        <p className="text-sm">
          You don&apos;t have a main resume yet, so a resume upload is required.
          Add one under My Profile to skip this next time.
        </p>
      )}

      {canReuse && mode === "reuse" ? (
        <div className="rounded-xl border border-border bg-secondary p-4 text-sm" aria-live="polite">
          <div className="flex items-center gap-2 font-medium">
            <FileCheck2 className="h-4 w-4" />
            {stored?.filename ?? "Resume on file"}
          </div>
          <div className="mt-1 text-xs">
            {[
              stored?.size_bytes
                ? `${(stored.size_bytes / 1024 / 1024).toFixed(2)} MB`
                : null,
              stored?.uploaded_at
                ? `uploaded ${new Date(stored.uploaded_at).toLocaleDateString()}`
                : null,
            ]
              .filter(Boolean)
              .join(" · ") || "Will be attached to this application."}
          </div>
        </div>
      ) : (
        <FormField
          label="Resume file"
          htmlFor={id}
          required
          hint="PDF or DOCX, up to 10 MB."
        >
          <ResumeFileInput
            id={id}
            file={file}
            progress={progress}
            error={error}
            disabled={disabled}
            onFileChange={onFileChange}
            onRetry={onRetry}
          />
        </FormField>
      )}
      {error && canReuse && mode === "reuse" ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
