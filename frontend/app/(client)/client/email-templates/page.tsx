"use client";

// Email template editor (FR-8.5): the platform ships no fixed copy —
// each tenant maintains its own templates.

import * as React from "react";
import { Plus } from "lucide-react";

import { apiGet, apiPost, apiPut } from "@/lib/api";
import type { EmailTemplate } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export default function EmailTemplatesPage() {
  const { toast } = useToast();
  const [templates, setTemplates] = React.useState<EmailTemplate[]>([]);
  const [selected, setSelected] = React.useState<EmailTemplate | null>(null);
  const [isNew, setIsNew] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [loading, setLoading] = React.useState(true);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<EmailTemplate[] | { templates: EmailTemplate[] }>(
        "/companies/me/email-templates"
      );
      setTemplates(Array.isArray(res) ? res : res.templates ?? []);
    } catch {
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const startNew = () => {
    setIsNew(true);
    setSelected({ name: "", subject: "", body: "" });
  };

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      if (isNew) {
        await apiPost("/companies/me/email-templates", {
          name: selected.name,
          subject: selected.subject,
          body: selected.body,
        });
      } else {
        await apiPut("/companies/me/email-templates", selected);
      }
      toast({ title: "Template saved", description: selected.name });
      setIsNew(false);
      void load();
    } catch (e) {
      toast({
        title: "Save failed",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Email Templates"
        description="Outreach, interview invites and status emails sent from your verified domain use these templates."
        actions={
          <Button className="gap-2" onClick={startNew}>
            <Plus className="h-4 w-4" /> New template
          </Button>
        }
      />
      <div className="grid gap-6 md:grid-cols-[240px,1fr]">
        <div className="space-y-1">
          {loading ? (
            <p className="p-2 text-sm text-muted-foreground">Loading…</p>
          ) : templates.length === 0 ? (
            <p className="p-2 text-sm text-muted-foreground">
              No templates yet.
            </p>
          ) : (
            templates.map((t) => (
              <button
                key={t.id ?? t.name}
                className={cn(
                  "block w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
                  selected && !isNew && (selected.id ?? selected.name) === (t.id ?? t.name)
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-accent"
                )}
                onClick={() => {
                  setIsNew(false);
                  setSelected(t);
                }}
              >
                {t.name}
              </button>
            ))
          )}
        </div>
        <Card>
          <CardContent className="pt-6">
            {selected ? (
              <div className="space-y-4">
                <FormField label="Template name" htmlFor="tpl-name" required>
                  <Input
                    id="tpl-name"
                    value={selected.name}
                    onChange={(e) =>
                      setSelected({ ...selected, name: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Subject" htmlFor="tpl-subject" required>
                  <Input
                    id="tpl-subject"
                    value={selected.subject}
                    onChange={(e) =>
                      setSelected({ ...selected, subject: e.target.value })
                    }
                  />
                </FormField>
                <FormField
                  label="Body"
                  htmlFor="tpl-body"
                  required
                  hint="Placeholders like {{candidate_name}}, {{job_title}}, {{interview_time}} are substituted at send time."
                >
                  <Textarea
                    id="tpl-body"
                    rows={12}
                    value={selected.body}
                    onChange={(e) =>
                      setSelected({ ...selected, body: e.target.value })
                    }
                  />
                </FormField>
                <Button
                  onClick={() => void save()}
                  disabled={saving || !selected.name || !selected.subject}
                >
                  {saving ? "Saving…" : isNew ? "Create template" : "Save changes"}
                </Button>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Select a template on the left, or create a new one.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
