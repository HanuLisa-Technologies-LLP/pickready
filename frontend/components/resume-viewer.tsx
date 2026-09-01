"use client";

// In-app resume viewer (FR-7.1). Reviewers must never be bounced out to a raw
// storage URL to read a resume: this renders the document inside a modal.
// PDFs (and images / extension-less legacy raw uploads) are framed inline;
// Word documents cannot be rendered by a browser, so they get an explicit,
// styled fallback instead of a blank frame. Composed from the generated
// shadcn Dialog primitive, which supplies role="dialog", aria-modal, the focus
// trap, Esc-to-close and focus restoration to the trigger.

import * as React from "react";
import { AlertTriangle, Download, FileText } from "lucide-react";

import { API_BASE, apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

/** How the document can be presented in-browser. */
type ResumeKind = "framable" | "word" | "unsupported";

/** Frames that never fire `load` are treated as failures after this long. */
const FRAME_TIMEOUT_MS = 10_000;

const WORD_EXTENSIONS = ["doc", "docx", "dot", "dotx", "odt", "rtf"];
const FRAMABLE_EXTENSIONS = ["pdf", "png", "jpg", "jpeg", "gif", "webp", "txt"];
const UNSUPPORTED_EXTENSIONS = [
  "xls",
  "xlsx",
  "ppt",
  "pptx",
  "zip",
  "rar",
  "7z",
];

export interface ResumeDescriptor {
  fileName: string;
  extension: string;
  kind: ResumeKind;
}

/**
 * Derive a display filename and a preview strategy from a storage URL.
 * Legacy `raw` uploads are frequently extension-less, those are optimistic
 * framing attempts, guarded by the load/timeout fallback below.
 */
/**
 * Preview strategy for a stored MIME type, when the filename cannot decide.
 *
 * Private-storage object names carry no extension, so a DOCX whose original
 * filename was not recorded used to be classified "framable" and handed to an
 * iframe, which renders nothing. The MIME type recorded at upload is the more
 * reliable signal and is checked first.
 */
export function kindFromMimeType(mimeType?: string | null): ResumeKind | null {
  const value = (mimeType ?? "").toLowerCase().split(";")[0].trim();
  if (!value) return null;
  if (
    value === "application/msword" ||
    value === "application/rtf" ||
    value === "text/rtf" ||
    value.startsWith("application/vnd.openxmlformats-officedocument.wordprocessing") ||
    value === "application/vnd.oasis.opendocument.text"
  )
    return "word";
  if (value === "application/pdf" || value.startsWith("image/") || value === "text/plain")
    return "framable";
  return "unsupported";
}

export function describeResumeUrl(url: string): ResumeDescriptor {
  let pathname = url;
  try {
    pathname = new URL(url, "https://resume.local").pathname;
  } catch {
    // Not an absolute URL, fall back to treating the whole string as a path.
  }

  const lastSegment = pathname.split("/").filter(Boolean).pop() ?? "resume";
  let fileName = lastSegment;
  try {
    fileName = decodeURIComponent(lastSegment);
  } catch {
    // Malformed percent-encoding, keep the raw segment.
  }

  const extension = fileName.includes(".")
    ? (fileName.split(".").pop() ?? "").toLowerCase()
    : "";

  let kind: ResumeKind = "framable";
  if (WORD_EXTENSIONS.includes(extension)) kind = "word";
  else if (UNSUPPORTED_EXTENSIONS.includes(extension)) kind = "unsupported";
  else if (extension && !FRAMABLE_EXTENSIONS.includes(extension))
    kind = "unsupported";

  return { fileName, extension, kind };
}

/**
 * LEGACY ROWS ONLY. Private documents live in S3 today and are served through
 * an authenticated route, so this branch fires for nothing written since the
 * storage migration. It is kept because a profile row written before it still
 * carries a Cloudinary URL: that host serves a real download rather than an
 * inline render only when the `fl_attachment` delivery flag is present. Any
 * other host gets the plain URL.
 */
export function toDownloadUrl(url: string): string {
  if (!url.includes("res.cloudinary.com")) return url;
  if (url.includes("fl_attachment")) return url;
  if (!url.includes("/upload/")) return url;
  return url.replace("/upload/", "/upload/fl_attachment/");
}

function FallbackPanel({
  title,
  message,
  fileName,
  tone = "info",
  children,
}: {
  title: string;
  message: string;
  fileName: string;
  tone?: "info" | "error";
  children?: React.ReactNode;
}) {
  const Icon = tone === "error" ? AlertTriangle : FileText;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-10 text-center">
      <span
        aria-hidden="true"
        className="flex h-14 w-14 items-center justify-center rounded-full border border-border bg-muted"
      >
        <Icon className="h-6 w-6 text-foreground" />
      </span>
      <h3 className="text-base font-semibold text-foreground">{title}</h3>
      <p className="max-w-md text-sm leading-6">
        {message}
      </p>
      <p className="max-w-md break-all font-mono text-xs">
        {fileName}
      </p>
      {children ? <div className="mt-2">{children}</div> : null}
    </div>
  );
}

export function ResumeViewer({
  open,
  onOpenChange,
  resumeUrl,
  profileId,
  resumeFileName,
  resumeMimeType,
  candidateName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Storage URL of the resume file. */
  resumeUrl?: string | null;
  /** Profile used by the authenticated server-side DOCX renderer. */
  profileId?: string | null;
  resumeFileName?: string | null;
  /** MIME type recorded at upload. Decides the preview strategy when the
   *  stored object name carries no extension. */
  resumeMimeType?: string | null;
  /** Shown in the modal header so the reviewer keeps their place. */
  candidateName: string;
}) {
  const [frameState, setFrameState] = React.useState<
    "loading" | "ready" | "error"
  >("loading");
  const [documentPreviewUrl, setDocumentPreviewUrl] = React.useState<string | null>(null);

  const descriptor = React.useMemo(() => {
    if (!resumeUrl) return null;
    const base = describeResumeUrl(resumeFileName?.trim() || resumeUrl);
    // The filename wins when it actually carries an extension; otherwise the
    // recorded MIME type decides, so a DOCX stored under an extension-less
    // private object name is still routed to the server-side renderer instead
    // of being handed to an iframe that renders nothing.
    if (base.extension) return base;
    const fromMime = kindFromMimeType(resumeMimeType);
    return fromMime ? { ...base, kind: fromMime } : base;
  }, [resumeFileName, resumeMimeType, resumeUrl]);
  const downloadUrl = React.useMemo(
    () =>
      profileId
        ? `${API_BASE}/candidates/profiles/${profileId}/resume-file?download=true`
        : resumeUrl
          ? toDownloadUrl(resumeUrl)
          : null,
    [profileId, resumeUrl]
  );

  // Reset the frame lifecycle each time the modal is opened for a document.
  React.useEffect(() => {
    if (open) setFrameState("loading");
  }, [open, resumeUrl]);

  React.useEffect(() => {
    if (
      !open ||
      !profileId ||
      (descriptor?.kind !== "word" && descriptor?.kind !== "framable")
    )
      return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setDocumentPreviewUrl(null);
    setFrameState("loading");
    const endpoint =
      descriptor.kind === "word" ? "resume-preview" : "resume-file";
    void apiFetch(`/candidates/profiles/${profileId}/${endpoint}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Resume preview failed");
        objectUrl = URL.createObjectURL(await response.blob());
        setDocumentPreviewUrl(objectUrl);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFrameState("error");
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [descriptor?.kind, open, profileId]);

  // A cross-origin frame can silently render nothing without firing `error`.
  // Time it out so the reviewer always gets an actionable fallback.
  React.useEffect(() => {
    if (!open || frameState !== "loading") return;
    if (descriptor?.kind !== "framable" || profileId) return;
    const timer = window.setTimeout(
      () => setFrameState("error"),
      FRAME_TIMEOUT_MS
    );
    return () => window.clearTimeout(timer);
  }, [open, frameState, descriptor?.kind, profileId]);

  const actions =
    resumeUrl && downloadUrl ? (
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button asChild size="sm" className="gap-2">
          <a href={downloadUrl} download={descriptor?.fileName}>
            <Download className="h-4 w-4" aria-hidden="true" />
            Download
          </a>
        </Button>
      </div>
    ) : null;

  let body: React.ReactNode;
  if (!resumeUrl || !descriptor) {
    body = (
      <FallbackPanel
        title="No resume on file"
        message="This candidate has not uploaded a resume yet, so there is nothing to preview."
        fileName="-"
      />
    );
  } else if (descriptor.kind === "word" && !profileId) {
    body = (
      <FallbackPanel
        title="Word document"
        message="This Word document cannot be previewed because its profile reference is missing. Download the original file to read it."
        fileName={descriptor.fileName}
      >
        {actions}
      </FallbackPanel>
    );
  } else if (descriptor.kind === "word" && frameState === "error") {
    body = (
      <FallbackPanel
        tone="error"
        title="Word preview could not be loaded"
        message="ReadyPick could not convert this document for the in-app viewer. Download the original file and try again later."
        fileName={descriptor.fileName}
      >
        {actions}
      </FallbackPanel>
    );
  } else if (descriptor.kind === "word") {
    body = (
      <div className="relative h-full w-full bg-muted">
        {!documentPreviewUrl ? (
          <div
            className="absolute inset-0 flex items-center justify-center bg-background"
            role="status"
          >
            <p className="text-sm">
              Preparing Word document
            </p>
          </div>
        ) : (
          <iframe
            src={documentPreviewUrl}
            title={`Resume of ${candidateName}`}
            // The viewer only mounts when the modal opens, so the document is
            // already fetched on demand rather than with the page; `lazy` keeps
            // it that way if this ever renders inline.
            loading="lazy"
            className="h-full w-full border-0 bg-background"
            onLoad={() => setFrameState("ready")}
            onError={() => setFrameState("error")}
          />
        )}
      </div>
    );
  } else if (descriptor.kind === "unsupported") {
    body = (
      <FallbackPanel
        title="Preview not available"
        message={`Preview isn't available for ${
          descriptor.extension ? `.${descriptor.extension}` : "this"
        } files. Download the original file to read it.`}
        fileName={descriptor.fileName}
      >
        {actions}
      </FallbackPanel>
    );
  } else if (descriptor.kind === "framable" && !profileId) {
    body = (
      <FallbackPanel
        tone="error"
        title="Preview could not be loaded"
        message="This resume is missing its secure profile reference. Download the original file and try again later."
        fileName={descriptor.fileName}
      >
        {actions}
      </FallbackPanel>
    );
  } else if (frameState === "error") {
    body = (
      <FallbackPanel
        tone="error"
        title="Preview could not be loaded"
        message="The document did not load in the viewer. It may be a format the browser cannot display, or the file may no longer be reachable. Download the original file instead."
        fileName={descriptor.fileName}
      >
        {actions}
      </FallbackPanel>
    );
  } else {
    body = (
      <div className="relative h-full w-full bg-muted">
        {frameState === "loading" ? (
          <div
            className="absolute inset-0 flex items-center justify-center bg-background"
            role="status"
          >
            <p role="status" className="text-sm">Loading document</p>
          </div>
        ) : null}
        {documentPreviewUrl ? (
          <iframe
            key={documentPreviewUrl}
            src={documentPreviewUrl}
            title={`Resume of ${candidateName}`}
            // The viewer only mounts when the modal opens, so the document is
            // already fetched on demand rather than with the page; `lazy` keeps
            // it that way if this ever renders inline.
            loading="lazy"
            className="h-full w-full border-0 bg-background"
            onLoad={() => setFrameState("ready")}
            onError={() => setFrameState("error")}
          />
        ) : null}
      </div>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid h-[85vh] max-h-[85vh] w-[95vw] max-w-4xl grid-rows-[auto,1fr,auto] gap-0 overflow-hidden p-0">
        <header className="border-b border-border px-6 py-4 pr-14 text-left">
          <DialogTitle className="truncate text-base font-semibold">
            {candidateName}
          </DialogTitle>
          <DialogDescription className="mt-1 truncate text-xs">
            {descriptor ? descriptor.fileName : "No resume file"}
          </DialogDescription>
        </header>

        <div className="min-h-0 overflow-auto">{body}</div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-6 py-3">
          <p className="text-xs">
            Viewing inside ReadyPick, the file is never opened as a bare
            storage link.
          </p>
          {actions}
        </footer>
      </DialogContent>
    </Dialog>
  );
}
