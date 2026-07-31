"use client";

// Email template editor (FR-8.5): the platform ships no fixed copy, each tenant
// maintains its own templates.

import * as React from "react";
import { Mail, Plus } from "lucide-react";

import { apiGet, apiPost, apiPut } from "@/lib/api";
import type { EmailTemplate } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import {
  EmptyState,
  LoadingRows,
  Section,
} from "@/components/page-primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form";
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
        eyebrow="Customer Portal"
        title="Email Templates"
        description="Outreach, interview invites and status emails sent from your verified domain use these templates."
        actions={
          <Button onClick={startNew}>
            <Plus className="h-4 w-4" aria-hidden="true" /> New template
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[260px,1fr]">
        <div className="rounded-xl border border-border bg-surface p-2 shadow-card">
          {loading ? (
            <LoadingRows rows={3} className="p-1" label="Loading templates" />
          ) : templates.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm">
              No templates yet. Create your first one.
            </p>
          ) : (
            <ul className="space-y-1" aria-label="Templates">
              {templates.map((t) => {
                const active =
                  !!selected &&
                  !isNew &&
                  (selected.id ?? selected.name) === (t.id ?? t.name);
                return (
                  <li key={t.id ?? t.name}>
                    <button
                      type="button"
                      aria-current={active ? "true" : undefined}
                      className={cn(
                        "block w-full truncate rounded-lg px-3 py-2.5 text-left text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        active
                          ? "bg-brand-600 font-semibold text-white shadow-brand"
                          : "font-medium hover:bg-brand-100/70 hover:text-accent-foreground"
                      )}
                      onClick={() => {
                        setIsNew(false);
                        setSelected(t);
                      }}
                    >
                      {t.name}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <Section title={isNew ? "New template" : selected?.name || "Template"}>
          {selected ? (
            <div className="space-y-5">
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
                hint="Placeholders like {{candidate_name}}, {{job_title}} and {{interview_time}} are filled in at send time."
              >
                <Textarea
                  id="tpl-body"
                  rows={12}
                  className="font-mono text-xs"
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
                {saving ? "Saving" : isNew ? "Create template" : "Save changes"}
              </Button>
            </div>
          ) : (
            <EmptyState
              icon={Mail}
              title="No template open"
              description="Pick a template from the list, or create a new one."
              action={
                <Button variant="outline" onClick={startNew}>
                  <Plus className="h-4 w-4" aria-hidden="true" /> New template
                </Button>
              }
            />
          )}
        </Section>
      </div>
    </div>
  );
}
