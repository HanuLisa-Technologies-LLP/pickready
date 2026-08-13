"use client";

import * as React from "react";
import { Building2, Loader2 } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { apiPost } from "@/lib/api";
import { homePathForRole, useAuth } from "@/lib/auth-context";
import { ROLE_LABEL, selectContext } from "@/lib/firebase-session";
import type { AuthContextsResponse, AuthContextOption } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

const SENSITIVE_PREFIXES = ["/org/billing", "/org/profile"];

export function WorkspaceSwitcher() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, setSession } = useAuth();
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [contexts, setContexts] = React.useState<AuthContextOption[]>([]);
  const [contextToken, setContextToken] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState<AuthContextOption | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const load = async () => {
    setBusy(true);
    setError(null);
    setPending(null);
    try {
      const result = await apiPost<AuthContextsResponse>("/auth/workspaces");
      setContexts(result.contexts);
      setContextToken(result.context_token);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not load your workspaces."
      );
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!pending || !contextToken) return;
    setBusy(true);
    setError(null);
    try {
      const session = await selectContext(contextToken, pending.user_id);
      setSession(session.user, session.capabilities ?? []);
      setOpen(false);
      // Client navigation keeps the browser process alive; the keyed workspace
      // boundary independently remounts all page state before the new route
      // renders, so no tenant-A DOM survives this tenant-B session.
      router.replace(homePathForRole(session.user.role));
      router.refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not switch workspace."
      );
    } finally {
      setBusy(false);
    }
  };

  const alternatives = contexts.filter((context) => context.user_id !== user?.id);
  const sensitive = SENSITIVE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(prefix + "/")
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) void load();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="w-full justify-start gap-2">
          <Building2 className="h-4 w-4" aria-hidden="true" />
          Switch workspace
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {pending ? `Switch to ${pending.tenant_name ?? "PickReady"}?` : "Switch workspace"}
          </DialogTitle>
          <DialogDescription>
            {pending
              ? sensitive
                ? "You are leaving a sensitive billing or company-profile page. Confirm the destination before the current workspace data is cleared."
                : "Confirm the destination. The current workspace page state will be cleared before the new workspace opens."
              : "Your active workspace controls every company record shown in this session."}
          </DialogDescription>
        </DialogHeader>

        {busy && !pending ? (
          <div className="flex items-center gap-2 py-4 text-sm" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading workspaces
          </div>
        ) : null}

        {!busy && !pending && alternatives.length === 0 && !error ? (
          <p className="rounded-lg border border-border bg-secondary p-3 text-sm">
            This identity has no other workspace.
          </p>
        ) : null}

        {!pending ? (
          <div className="space-y-2">
            {alternatives.map((context) => (
              <button
                key={context.user_id}
                type="button"
                className="w-full rounded-lg border border-border p-3 text-left transition-colors hover:border-brand-600/50 hover:bg-brand-100/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setPending(context)}
              >
                <span className="block font-semibold">
                  {context.tenant_name ?? "PickReady"}
                </span>
                <span className="block text-xs opacity-80">
                  {ROLE_LABEL[context.role]}
                </span>
              </button>
            ))}
          </div>
        ) : null}

        {error ? (
          <p className="rounded-lg border border-destructive/40 p-3 text-sm" role="alert">
            {error}
          </p>
        ) : null}

        {pending ? (
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setPending(null)}>
              Back
            </Button>
            <Button disabled={busy} onClick={() => void confirm()}>
              {busy ? "Switching…" : "Confirm switch"}
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
