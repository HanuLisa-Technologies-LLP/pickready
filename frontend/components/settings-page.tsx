"use client";

// Shared Settings/Profile page. This is the ONLY place the theme toggle
// lives (claude.md rule 10 / PRD §8 Theming).
//
// Profile CRUD contract:
//   GET   /portal/me  -> { id, full_name, email, phone, role }
//   PATCH /portal/me  body { full_name?, phone? } -> same shape
// `email` is read-only: Firebase owns credentials and recovery, so the UI
// never offers to change it. Role is read-only too (RBAC is server-side).

import * as React from "react";
import { Moon, Pencil, Sun } from "lucide-react";

import { apiGet, apiPatch } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { useAuth } from "@/lib/auth-context";
import { useTheme } from "@/lib/theme-provider";
import { PageHeader } from "@/components/app-shell";
import { ChangePasswordCard } from "@/components/change-password";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import { InlineError, Section } from "@/components/page-primitives";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";

interface MeProfile {
  id?: string;
  full_name: string;
  email: string;
  phone?: string | null;
  role?: string | null;
}

const NAME_MAX = 255;

/** Name is required, 1 to 255 chars. Returns an error message or null. */
export function validateFullName(value: string): string | null {
  const name = value.trim();
  if (!name) return "Enter your name.";
  if (name.length > NAME_MAX) return `Keep your name under ${NAME_MAX} characters.`;
  return null;
}

export function SettingsPage({
  title = "Settings",
  description = "Your account details and how the product looks.",
  showRole = true,
  children,
}: {
  title?: string;
  description?: string;
  /** Candidates have exactly one role, so showing it is noise (2026-07-27). */
  showRole?: boolean;
  /** Extra cards rendered between the account card and Appearance. */
  children?: React.ReactNode;
} = {}) {
  const { user, refresh } = useAuth();
  const { theme, setTheme } = useTheme();
  const { toast } = useToast();

  const [profile, setProfile] = React.useState<MeProfile | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [draft, setDraft] = React.useState({ full_name: "", phone: "" });
  const [nameError, setNameError] = React.useState<string | null>(null);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  const applyProfile = React.useCallback((next: MeProfile) => {
    setProfile(next);
    setDraft({ full_name: next.full_name ?? "", phone: next.phone ?? "" });
  }, []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      applyProfile(await apiGet<MeProfile>("/portal/me"));
    } catch (error) {
      // Fall back to the session identity so the page is never a dead end.
      if (user) {
        applyProfile({
          full_name: user.full_name,
          email: user.email,
          role: user.role,
        });
      }
      setLoadError(apiErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [applyProfile, user]);

  React.useEffect(() => {
    void load();
    // Load once the session is known; `load` already closes over the fallback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pristine =
    draft.full_name === (profile?.full_name ?? "") &&
    draft.phone === (profile?.phone ?? "");

  const cancel = () => {
    setDraft({
      full_name: profile?.full_name ?? "",
      phone: profile?.phone ?? "",
    });
    setNameError(null);
    setSaveError(null);
    setEditing(false);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    const invalid = validateFullName(draft.full_name);
    setNameError(invalid);
    if (invalid) return;
    setSaving(true);
    setSaveError(null);
    try {
      // Use the response, never an optimistic local guess.
      const updated = await apiPatch<MeProfile>("/portal/me", {
        full_name: draft.full_name.trim(),
        phone: draft.phone.trim(),
      });
      applyProfile(updated);
      setEditing(false);
      toast({ title: "Profile updated" });
      // Refresh the session so the sidebar name changes immediately.
      await refresh();
    } catch (error) {
      const message = apiErrorMessage(error);
      setSaveError(message);
      toast({
        title: "Could not save your profile",
        description: message,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const email = profile?.email ?? user?.email ?? "-";
  const role = profile?.role ?? user?.role ?? null;

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title={title} description={description} />
      <div className="space-y-6">
        <Section
          title="Account"
          contentClassName="space-y-4"
          actions={
            !editing ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={loading}
                onClick={() => setEditing(true)}
              >
                <Pencil className="h-4 w-4" aria-hidden="true" /> Edit
              </Button>
            ) : null
          }
        >
            {loadError ? <InlineError>{loadError}</InlineError> : null}

            {editing ? (
              <form className="space-y-4" onSubmit={save} noValidate>
                <FormField
                  label="Name"
                  htmlFor="profile-name"
                  required
                  error={nameError}
                >
                  <Input
                    id="profile-name"
                    autoComplete="name"
                    maxLength={NAME_MAX}
                    value={draft.full_name}
                    disabled={saving}
                    onChange={(e) => {
                      setDraft({ ...draft, full_name: e.target.value });
                      if (nameError) setNameError(null);
                    }}
                  />
                </FormField>
                <FormField
                  label="Phone"
                  htmlFor="profile-phone"
                  hint="Optional, used for recruiter contact only."
                >
                  <Input
                    id="profile-phone"
                    type="tel"
                    autoComplete="tel"
                    value={draft.phone}
                    disabled={saving}
                    onChange={(e) => setDraft({ ...draft, phone: e.target.value })}
                  />
                </FormField>
                <ReadOnlyRow label="Email" value={email} />
                {showRole ? (
                  <ReadOnlyRow
                    label="Role"
                    value={role ? role.replace(/_/g, " ") : "-"}
                    capitalize
                  />
                ) : null}
                {saveError ? <InlineError>{saveError}</InlineError> : null}
                <div className="flex gap-2">
                  <Button type="submit" disabled={saving || pristine}>
                    {saving ? "Saving" : "Save changes"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={saving}
                    onClick={cancel}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            ) : (
              <div className="space-y-3">
                <ReadOnlyRow
                  label="Name"
                  value={loading ? "Loading" : profile?.full_name || "-"}
                />
                <Separator />
                <ReadOnlyRow
                  label="Phone"
                  value={loading ? "Loading" : profile?.phone || "Not added"}
                />
                <Separator />
                <ReadOnlyRow label="Email" value={email} />
                {showRole ? (
                  <>
                    <Separator />
                    <ReadOnlyRow
                      label="Role"
                      value={role ? role.replace(/_/g, " ") : "-"}
                      capitalize
                    />
                  </>
                ) : null}
              </div>
            )}
        </Section>

        {/* Only renders for accounts that actually have a password. */}
        <ChangePasswordCard />

        {children}

        <Section
          title="Appearance"
          description="Switch between the light and dark theme."
        >
          <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-secondary px-4 py-3">
            <Label
              htmlFor="theme-toggle"
              className="flex items-center gap-2 text-sm font-medium"
            >
              {theme === "dark" ? (
                <Moon className="h-4 w-4" aria-hidden="true" />
              ) : (
                <Sun className="h-4 w-4" aria-hidden="true" />
              )}
              Dark mode
            </Label>
            <Switch
              id="theme-toggle"
              checked={theme === "dark"}
              onCheckedChange={(checked) =>
                setTheme(checked ? "dark" : "light")
              }
            />
          </div>
        </Section>
      </div>
    </div>
  );
}

function ReadOnlyRow({
  label,
  value,
  capitalize,
}: {
  label: string;
  value: string;
  capitalize?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
      <span className="opacity-80">{label}</span>
      <span className={`font-semibold ${capitalize ? "capitalize" : ""}`}>
        {value}
      </span>
    </div>
  );
}
