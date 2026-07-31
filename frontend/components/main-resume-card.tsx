"use client";

// The candidate's MAIN resume, managed on My Profile (FR-6.2 / client decision
// 2026-07-27). Uploaded once, re-uploadable whenever, and offered on every
// application alongside "upload a new resume" for that one application.
//
// The storage vendor is never named to a candidate (claude.md), they are told
// the accepted formats and size limit, nothing about where the bytes land.

import * as React from "react";
import { FileText, Upload } from "lucide-react";

import { apiGet, apiUploadWithProgress } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  validateResumeFile,
  type StoredResume,
} from "@/components/resume-file-input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function MainResumeCard({
  onChange,
}: {
  onChange?: (resume: StoredResume) => void;
}) {
  const { toast } = useToast();
  const [stored, setStored] = React.useState<StoredResume | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [file, setFile] = React.useState<File | null>(null);
  const [uploading, setUploading] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    apiGet<StoredResume>("/portal/me/resume")
      .then(setStored)
      .catch(() => setStored({ has_resume: false }))
      .finally(() => setLoading(false));
  }, []);

  const upload = async () => {
    if (!file) return;
    const invalid = validateResumeFile(file);
    if (invalid) {
      setError(invalid);
      return;
    }
    setUploading(true);
    setProgress(0);
    setError(null);
    try {
      const form = new FormData();
      form.append("resume", file);
      const saved = await apiUploadWithProgress<StoredResume>(
        "/portal/me/resume",
        form,
        setProgress,
        "PUT"
      );
      setStored(saved);
      onChange?.(saved);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      toast({
        title: stored?.has_resume ? "Main resume replaced" : "Main resume saved",
        description: "It will be offered on every application from now on.",
      });
    } catch (uploadError) {
      const message = apiErrorMessage(uploadError);
      setError(message);
      toast({
        title: "Upload failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setUploading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Main resume</CardTitle>
        <CardDescription>
          The resume offered every time you apply. Replace it whenever you like, 
          past applications keep the resume you actually sent them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <p role="status" className="text-sm">
        Checking your record
      </p>
        ) : stored?.has_resume ? (
          <div className="rounded-md border p-3 text-sm">
            <div className="flex items-center gap-2 font-medium">
              <FileText className="h-4 w-4" />
              {stored.filename ?? "Resume on file"}
            </div>
            <div className="mt-1 text-xs">
              {[
                stored.size_bytes
                  ? `${(stored.size_bytes / 1024 / 1024).toFixed(2)} MB`
                  : null,
                stored.uploaded_at
                  ? `uploaded ${new Date(stored.uploaded_at).toLocaleDateString()}`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </div>
          </div>
        ) : (
          <p className="text-sm">
            No main resume yet. Upload one so you can apply without attaching a
            file each time.
          </p>
        )}

        <div className="space-y-2">
          <Input
            ref={inputRef}
            id="main-resume-file"
            type="file"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            disabled={uploading}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              setFile(selected);
              setError(selected ? validateResumeFile(selected) : null);
              setProgress(0);
            }}
          />
          <p className="text-xs">PDF or DOCX, up to 10 MB.</p>
        </div>

        {uploading ? (
          <div className="space-y-1.5" aria-live="polite">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-xs">Uploading {progress}%</p>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        ) : null}

        <Button
          type="button"
          className="gap-2"
          disabled={!file || uploading || Boolean(error)}
          onClick={() => void upload()}
        >
          <Upload className="h-4 w-4" />
          {uploading
            ? "Uploading"
            : stored?.has_resume
              ? "Replace main resume"
              : "Upload main resume"}
        </Button>
      </CardContent>
    </Card>
  );
}
