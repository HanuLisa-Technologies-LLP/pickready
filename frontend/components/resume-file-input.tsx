"use client";

import { FileCheck2, FileText, RotateCcw, UploadCloud } from "lucide-react";
import { Input } from "@/components/ui/input";

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
      <p className="text-xs text-muted-foreground">PDF or DOCX, up to 10 MB. Your resume is securely stored in Cloudinary.</p>
      {file ? (
        <div className="rounded-md border bg-muted/30 p-3 text-sm" aria-live="polite">
          <div className="flex items-center gap-2 font-medium"><FileText className="h-4 w-4" />{file.name}</div>
          <div className="mt-1 text-xs text-muted-foreground">{(file.size / 1024 / 1024).toFixed(2)} MB</div>
          {disabled ? (
            <div className="mt-3 space-y-1.5">
              <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-all" style={{ width: `${progress}%` }} /></div>
              <div className="flex items-center gap-1 text-xs text-muted-foreground"><UploadCloud className="h-3.5 w-3.5 animate-pulse" />Uploading securely… {progress}%</div>
            </div>
          ) : progress === 100 && !error ? <div className="mt-2 flex items-center gap-1 text-xs text-green-700"><FileCheck2 className="h-3.5 w-3.5" />Uploaded and linked to this application.</div> : null}
        </div>
      ) : null}
      {error ? <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive" role="alert"><span>{error}</span>{onRetry ? <button type="button" className="inline-flex items-center gap-1 underline" onClick={onRetry}><RotateCcw className="h-3.5 w-3.5" />Retry</button> : null}</div> : null}
    </div>
  );
}
