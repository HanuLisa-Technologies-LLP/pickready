"use client";

// Projects inside the candidate's validation profile (Project Evidence
// Intelligence, 2026-09-01). Optional by design: a candidate is never forced
// to add one, and the copy says so. The storage promise is served by the
// backend (retention_notice) so this UI can never drift from what the
// pipeline actually does: originals are analysed and then deleted, only the
// derived evidence summary is kept. Nothing here offers a download of an
// original file, because none exists to offer.

import * as React from "react";
import { FolderGit2, Loader2, Plus, Trash2, Upload } from "lucide-react";

import { apiDelete, apiGet, apiPost, apiUploadWithProgress } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type ProjectFile = {
  filename: string;
  size_bytes: number;
  family: string;
  label: string;
  supported: boolean;
};

type Project = {
  id: string;
  name: string;
  description: string;
  repository_url: string | null;
  submission_kind: string;
  status: string;
  status_detail: string | null;
  failure_code: string | null;
  can_retry: boolean;
  files: ProjectFile[];
  created_at: string;
  processed_at: string | null;
};

type ProjectLimits = {
  max_projects: number;
  max_files: number;
  max_file_bytes: number;
  max_total_bytes: number;
  description_max_words: number;
  supported_repository_hosts: string[];
};

type ProjectsResponse = {
  retention_notice: string;
  limits: ProjectLimits;
  projects: Project[];
};

const PENDING_STATUSES = new Set(["submitted", "processing", "persisted"]);

const STATUS_LABELS: Record<string, string> = {
  submitted: "Queued",
  processing: "Analysing",
  persisted: "Finishing",
  processed: "Evidence ready",
  partially_processed: "Summary ready",
  failed_security: "Could not accept",
  failed_extraction: "Could not read",
  failed_evidence_generation: "Analysis failed",
};

function countWords(text: string): number {
  return (text.match(/[\w'-]+/g) ?? []).length;
}

function StatusBadge({ status }: { status: string }) {
  const label = STATUS_LABELS[status] ?? status;
  const pending = PENDING_STATUSES.has(status);
  const failed = status.startsWith("failed");
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium " +
        (failed
          ? "border-destructive/40 text-destructive"
          : pending
            ? "border-border"
            : "border-teal-600/40 text-teal-700 dark:text-teal-300")
      }
    >
      {pending ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
      {label}
    </span>
  );
}

export function ProjectsSection() {
  const { toast } = useToast();
  const [data, setData] = React.useState<ProjectsResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [showForm, setShowForm] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [repoUrl, setRepoUrl] = React.useState("");
  const [files, setFiles] = React.useState<File[]>([]);
  const [submitting, setSubmitting] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const refresh = React.useCallback(async () => {
    try {
      setData(await apiGet<ProjectsResponse>("/portal/me/projects"));
    } catch {
      // The section stays usable on a transient fetch failure; the next
      // poll or action re-fetches.
    }
  }, []);

  React.useEffect(() => {
    apiGet<ProjectsResponse>("/portal/me/projects")
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  const anyPending = data?.projects.some((p) => PENDING_STATUSES.has(p.status));
  React.useEffect(() => {
    if (!anyPending) return;
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [anyPending, refresh]);

  const limits = data?.limits;
  const wordCount = countWords(description);
  const wordLimit = limits?.description_max_words ?? 100;
  const overWordLimit = wordCount > wordLimit;
  const atProjectLimit =
    limits != null && (data?.projects.length ?? 0) >= limits.max_projects;

  const validateSelection = (selected: File[]): string | null => {
    if (!limits) return null;
    if (selected.length > limits.max_files) {
      return `A project can include at most ${limits.max_files} files.`;
    }
    const oversize = selected.find((f) => f.size > limits.max_file_bytes);
    if (oversize) {
      const mb = Math.floor(limits.max_file_bytes / 1024 / 1024);
      return `${oversize.name} is larger than the per-file limit of ${mb} MB.`;
    }
    const total = selected.reduce((sum, f) => sum + f.size, 0);
    if (total > limits.max_total_bytes) {
      const mb = Math.floor(limits.max_total_bytes / 1024 / 1024);
      return `The selected files exceed the total limit of ${mb} MB.`;
    }
    return null;
  };

  const resetForm = () => {
    setName("");
    setDescription("");
    setRepoUrl("");
    setFiles([]);
    setError(null);
    setProgress(0);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const submit = async () => {
    if (!name.trim()) {
      setError("Give the project a name.");
      return;
    }
    if (!description.trim() || overWordLimit) {
      setError(
        overWordLimit
          ? `The description must be ${wordLimit} words or fewer.`
          : "Describe the project in a few sentences."
      );
      return;
    }
    if (files.length === 0 && !repoUrl.trim()) {
      setError("Add at least one file or a public repository link.");
      return;
    }
    const invalid = validateSelection(files);
    if (invalid) {
      setError(invalid);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("name", name.trim());
      form.append("description", description.trim());
      if (repoUrl.trim()) form.append("repository_url", repoUrl.trim());
      for (const file of files) form.append("files", file);
      await apiUploadWithProgress<Project>(
        "/portal/me/projects",
        form,
        setProgress
      );
      toast({
        title: "Project added",
        description:
          "We are analysing it now. You can add another project meanwhile.",
      });
      resetForm();
      setShowForm(false);
      await refresh();
    } catch (submitError) {
      const message = apiErrorMessage(submitError);
      setError(message);
      toast({
        title: "Could not add the project",
        description: message,
        variant: "destructive",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const retry = async (project: Project) => {
    try {
      await apiPost(`/portal/me/projects/${project.id}/reprocess`);
      await refresh();
    } catch (retryError) {
      toast({
        title: "Could not retry",
        description: apiErrorMessage(retryError),
        variant: "destructive",
      });
    }
  };

  const remove = async (project: Project) => {
    try {
      await apiDelete(`/portal/me/projects/${project.id}`);
      toast({ title: "Project removed" });
      await refresh();
    } catch (removeError) {
      toast({
        title: "Could not remove the project",
        description: apiErrorMessage(removeError),
        variant: "destructive",
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Projects</CardTitle>
        <CardDescription>
          {data?.retention_notice ??
            "Adding projects is optional. Relevant project evidence can strengthen your applications."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <p role="status" className="text-sm">
            Checking your projects
          </p>
        ) : (
          <>
            {(data?.projects ?? []).map((project) => (
              <div key={project.id} className="rounded-md border p-3 text-sm">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 font-medium">
                    <FolderGit2 className="h-4 w-4" />
                    {project.name}
                  </div>
                  <StatusBadge status={project.status} />
                </div>
                {project.repository_url ? (
                  <p className="mt-1 break-all text-xs">
                    {project.repository_url}
                  </p>
                ) : null}
                {project.files.length > 0 ? (
                  <p className="mt-1 text-xs">
                    {project.files.length}{" "}
                    {project.files.length === 1 ? "file" : "files"}
                    {project.files.some((f) => !f.supported)
                      ? " (some formats were recorded but could not be analysed)"
                      : ""}
                  </p>
                ) : null}
                {project.status_detail ? (
                  <p className="mt-1 text-xs">{project.status_detail}</p>
                ) : null}
                <div className="mt-2 flex gap-2">
                  {project.can_retry ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void retry(project)}
                    >
                      Retry analysis
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="gap-1 text-destructive"
                    onClick={() => void remove(project)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Remove
                  </Button>
                </div>
              </div>
            ))}

            {!showForm ? (
              <div className="space-y-2">
                {(data?.projects.length ?? 0) === 0 ? (
                  <p className="text-sm">
                    No projects yet. If you have work that shows your skills,
                    real code, designs, drawings, reports or analyses, adding
                    it here gives employers stronger evidence than a resume
                    line.
                  </p>
                ) : null}
                <Button
                  type="button"
                  variant="outline"
                  className="gap-2"
                  disabled={atProjectLimit}
                  onClick={() => setShowForm(true)}
                >
                  <Plus className="h-4 w-4" />
                  Add project
                </Button>
                {atProjectLimit ? (
                  <p className="text-xs">
                    You have reached the maximum of {limits?.max_projects}{" "}
                    projects. Remove one to add another.
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="space-y-3 rounded-md border p-3">
                <div className="space-y-1.5">
                  <label htmlFor="project-name" className="text-sm font-medium">
                    Project name
                  </label>
                  <Input
                    id="project-name"
                    value={name}
                    maxLength={160}
                    disabled={submitting}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Smart Garage Management System"
                  />
                </div>
                <div className="space-y-1.5">
                  <label
                    htmlFor="project-description"
                    className="text-sm font-medium"
                  >
                    What it is and what you did
                  </label>
                  <Textarea
                    id="project-description"
                    value={description}
                    rows={4}
                    disabled={submitting}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="What the project does, what you built yourself, and what it shows about your skills."
                  />
                  <p
                    className={
                      "text-xs " +
                      (overWordLimit ? "font-medium text-destructive" : "")
                    }
                  >
                    {wordCount} of {wordLimit} words
                  </p>
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="project-repo" className="text-sm font-medium">
                    Public repository link (optional)
                  </label>
                  <Input
                    id="project-repo"
                    value={repoUrl}
                    disabled={submitting}
                    onChange={(event) => setRepoUrl(event.target.value)}
                    placeholder="https://github.com/you/project"
                  />
                  <p className="text-xs">
                    Public repositories only. We never ask for repository
                    credentials.
                  </p>
                </div>
                <div className="space-y-1.5">
                  <label
                    htmlFor="project-files"
                    className="text-sm font-medium"
                  >
                    Project files (optional)
                  </label>
                  <Input
                    ref={fileInputRef}
                    id="project-files"
                    type="file"
                    multiple
                    disabled={submitting}
                    onChange={(event) => {
                      const selected = Array.from(event.target.files ?? []);
                      setFiles(selected);
                      setError(validateSelection(selected));
                    }}
                  />
                  <p className="text-xs">
                    Code, documents, drawings, CAD files, spreadsheets,
                    reports. Up to {limits?.max_files ?? 20} files. Unsupported
                    formats are recorded so you will know what could not be
                    analysed.
                  </p>
                </div>

                {submitting ? (
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

                <div className="flex gap-2">
                  <Button
                    type="button"
                    className="gap-2"
                    disabled={submitting}
                    onClick={() => void submit()}
                  >
                    <Upload className="h-4 w-4" />
                    {submitting ? "Submitting" : "Submit project"}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={submitting}
                    onClick={() => {
                      resetForm();
                      setShowForm(false);
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
