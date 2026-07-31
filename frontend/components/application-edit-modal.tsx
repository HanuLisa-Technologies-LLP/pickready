"use client";

// Editing an application during the 5-day grace period (spec §5.1).
//
// Two things are editable, matching the spec: the RESUME, and the validation
// form answers. Everything else is deliberately fixed, you cannot change which
// job you applied to (that is a new application) or your identity.

import * as React from "react";
import { Loader2, Save } from "lucide-react";

import { api } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { ResumeFileInput } from "@/components/resume-file-input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function ApplicationEditModal({
  open,
  onOpenChange,
  linkId,
  jobTitle,
  daysRemaining,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  linkId: string | null;
  jobTitle: string;
  daysRemaining: number;
  onSaved?: () => void;
}) {
  const { toast } = useToast();
  const [resume, setResume] = React.useState<File | null>(null);
  const [refreshForm, setRefreshForm] = React.useState(false);
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setResume(null);
      setRefreshForm(false);
    }
  }, [open, linkId]);

  const save = async () => {
    if (!linkId || (!resume && !refreshForm)) return;
    setSaving(true);
    try {
      const form = new FormData();
      if (resume) form.append("resume", resume);
      form.append("refresh_profile_form", String(refreshForm));
      await api(`/portal/applications/${linkId}`, {
        method: "PATCH",
        formData: form,
      });
      toast({
        title: "Application updated",
        description: resume
          ? "Your new resume is with the hiring team."
          : "Your latest profile answers are now on this application.",
      });
      onOpenChange(false);
      onSaved?.();
    } catch (e) {
      toast({
        title: "Couldn't update your application",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Update your application, {jobTitle}</DialogTitle>
          <DialogDescription>
            Applications for this role have closed, but you can still update
            yours for {daysRemaining} more {daysRemaining === 1 ? "day" : "days"}.
            You cannot change which role you applied to.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <div>
            <p className="mb-2 text-sm font-medium">Replace your resume</p>
            <ResumeFileInput
              id="grace-resume"
              file={resume}
              onFileChange={setResume}
            />
            <p className="mt-1.5 text-xs">
              Optional. The new file replaces the one on this application only, 
              your other applications keep the resume they were sent with.
            </p>
          </div>

          <div>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-foreground"
                checked={refreshForm}
                onChange={(e) => setRefreshForm(e.target.checked)}
              />
              <span>
                Also update this application with my current My Profile answers
                <span className="mt-0.5 block text-xs">
                  Use this if you have edited your profile since you applied.
                </span>
              </span>
            </label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            className="gap-1.5"
            disabled={saving || (!resume && !refreshForm)}
            onClick={() => void save()}
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            {saving ? "Saving" : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
