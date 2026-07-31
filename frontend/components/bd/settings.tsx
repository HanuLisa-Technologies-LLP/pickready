"use client";

// BD Portal settings: the rep's own details, their password, and the theme.
//
// This does NOT reuse `components/settings-page.tsx`, which reads and writes
// `/portal/me`. A BD user has no candidate profile and no tenant, their details
// live behind `GET|PATCH /bd/me`, and the shapes differ (`name` here,
// `full_name` there). The layout, the copy and the Appearance card deliberately
// match that page so the two portals feel identical.
//
// There is no password field and no password endpoint. Firebase owns
// credentials and recovery (CLAUDE.md rule 2), so `ChangePasswordCard` is
// mounted unchanged: it talks to the Firebase client SDK and calls no PickReady
// route. It renders only for accounts that actually have a password, so a
// Google-only rep is not offered a dead end.
//
// The theme toggle lives ONLY here, never in a navbar (CLAUDE.md rule 10).

import * as React from "react";
import { Moon, Pencil, Sun } from "lucide-react";

import { apiGet, apiPatch } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { useAuth } from "@/lib/auth-context";
import { useTheme } from "@/lib/theme-provider";
import type { BDProfile, BDProfileUpdate } from "@/lib/bd-types";
import { PageHeader } from "@/components/app-shell";
import { ChangePasswordCard } from "@/components/change-password";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";

const NAME_MAX = 255;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface Draft {
  name: string;
  email: string;
  phone: string;
}

export function validateBDProfile(draft: Draft): Partial<Record<keyof Draft, string>> {
  const errors: Partial<Record<keyof Draft, string>> = {};
  if (!draft.name.trim()) errors.name = "Enter your name.";
  else if (draft.name.trim().length > NAME_MAX) {
    errors.name = `Keep your name under ${NAME_MAX} characters.`;
  }
  if (!draft.email.trim()) errors.email = "Enter your email address.";
  else if (!EMAIL_RE.test(draft.email.trim())) {
    errors.email = "Enter a valid email address.";
  }
  return errors;
}

export function BDSettingsPage() {
  const { refresh } = useAuth();
  const { theme, setTheme } = useTheme();
  const { toast } = useToast();

  const [profile, setProfile] = React.useState<BDProfile | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [draft, setDraft] = React.useState<Draft>({
    name: "",
    email: "",
    phone: "",
  });
  const [errors, setErrors] = React.useState<Partial<Record<keyof Draft, string>>>(
    {}
  );
  const [saveError, setSaveError] = React.useState<string | null>(null);

  const apply = React.useCallback((next: BDProfile) => {
    setProfile(next);
    setDraft({
      name: next.name ?? "",
      email: next.email ?? "",
      phone: next.phone ?? "",
    });
  }, []);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      apply(await apiGet<BDProfile>("/bd/me"));
    } catch (error) {
      setLoadError(apiErrorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [apply]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const pristine =
    draft.name === (profile?.name ?? "") &&
    draft.email === (profile?.email ?? "") &&
    draft.phone === (profile?.phone ?? "");

  const cancel = () => {
    if (profile) apply(profile);
    setErrors({});
    setSaveError(null);
    setEditing(false);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    const found = validateBDProfile(draft);
    setErrors(found);
    if (Object.keys(found).length) return;

    setSaving(true);
    setSaveError(null);
    try {
      const body: BDProfileUpdate = {
        name: draft.name.trim(),
        email: draft.email.trim(),
        phone: draft.phone.trim(),
      };
      // Use the response, never an optimistic local guess.
      apply(await apiPatch<BDProfile>("/bd/me", body));
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

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Settings"
        description="Your account details and appearance preferences."
      />
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <CardTitle>Account</CardTitle>
              {!editing ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="gap-2"
                  disabled={loading || Boolean(loadError)}
                  onClick={() => setEditing(true)}
                >
                  <Pencil className="h-4 w-4" /> Edit
                </Button>
              ) : null}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {loadError ? (
              <div role="alert" className="space-y-3">
                <p className="text-sm">
                  Your details could not be loaded. {loadError}
                </p>
                <Button size="sm" onClick={() => void load()}>
                  Try again
                </Button>
              </div>
            ) : loading ? (
              <div className="space-y-3" aria-busy="true">
                <span className="sr-only">Loading your details</span>
                <Skeleton className="h-5 w-full" />
                <Skeleton className="h-5 w-full" />
                <Skeleton className="h-5 w-2/3" />
              </div>
            ) : editing ? (
              <form className="space-y-4" onSubmit={save} noValidate>
                <FormField
                  label="Name"
                  htmlFor="bd-name"
                  required
                  error={errors.name}
                >
                  <Input
                    id="bd-name"
                    autoComplete="name"
                    maxLength={NAME_MAX}
                    value={draft.name}
                    disabled={saving}
                    onChange={(event) =>
                      setDraft({ ...draft, name: event.target.value })
                    }
                  />
                </FormField>
                <FormField
                  label="Email"
                  htmlFor="bd-email"
                  required
                  error={errors.email}
                  hint="This updates your PickReady record. Your sign-in identity is managed separately."
                >
                  <Input
                    id="bd-email"
                    type="email"
                    autoComplete="email"
                    maxLength={320}
                    value={draft.email}
                    disabled={saving}
                    onChange={(event) =>
                      setDraft({ ...draft, email: event.target.value })
                    }
                  />
                </FormField>
                <FormField label="Phone" htmlFor="bd-phone">
                  <Input
                    id="bd-phone"
                    type="tel"
                    autoComplete="tel"
                    maxLength={50}
                    value={draft.phone}
                    disabled={saving}
                    onChange={(event) =>
                      setDraft({ ...draft, phone: event.target.value })
                    }
                  />
                </FormField>
                {saveError ? (
                  <p role="alert" className="text-sm text-destructive">
                    {saveError}
                  </p>
                ) : null}
                <div className="flex gap-2">
                  <Button type="submit" disabled={saving || pristine}>
                    {saving ? "Saving…" : "Save changes"}
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
                <ReadOnlyRow label="Name" value={profile?.name || "-"} />
                <Separator />
                <ReadOnlyRow label="Email" value={profile?.email || "-"} />
                <Separator />
                <ReadOnlyRow label="Phone" value={profile?.phone || "Not added"} />
              </div>
            )}
          </CardContent>
        </Card>

        {/* Firebase owns credentials. This card renders only for accounts that
            actually have a password. */}
        <ChangePasswordCard />

        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
            <CardDescription>
              Switch between the light and dark theme.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <Label htmlFor="theme-toggle" className="flex items-center gap-2">
                {theme === "dark" ? (
                  <Moon className="h-4 w-4" />
                ) : (
                  <Sun className="h-4 w-4" />
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
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function ReadOnlyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 text-sm">
      <span>{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
