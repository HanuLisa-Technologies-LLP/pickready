"use client";

// Provider Portal, the Business Development team.
//
// A BD user is ReadyPick's own salesperson, not a customer's employee: the row
// has no tenant, which is exactly why none of the existing invite screens could
// create one. This page is the only way to add them.
//
// THE ONE THING TO UNDERSTAND BEFORE EDITING: adding someone here does NOT
// create a credential. ReadyPick never holds one. The row reserves the email
// address; the person becomes able to sign in when a Firebase identity exists
// for that address and they use the normal login page. The "Signed in" column
// is therefore load-bearing, it is the answer to "I added them, why can they
// not get in".
//
// Disable is still the RIGHT control for someone who may come back: it is
// reversible and keeps their lead ownership intact. Delete was added on
// 2026-08-09 at the client's request for accounts created in error or addresses
// that must genuinely leave. It is irreversible, so it asks for the email to be
// retyped, and it RELEASES the rep's leads rather than deleting them: a rep
// leaving must never take the company's pipeline with them.

import * as React from "react";
import { Plus, Pencil, Power, PowerOff, Trash2, UserRound } from "lucide-react";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api";
import type { BDUser } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type BDForm = {
  email: string;
  full_name: string;
  phone: string;
};

const EMPTY_FORM: BDForm = { email: "", full_name: "", phone: "" };

const STATUS_LABEL: Record<string, string> = {
  invited: "Invited",
  active: "Active",
  disabled: "Disabled",
};

function FieldError({ message }: { message?: string }) {
  return message ? (
    <p className="mt-1 text-xs font-medium text-destructive" role="alert">
      {message}
    </p>
  ) : null;
}

export default function BusinessDevelopmentPage() {
  const { toast } = useToast();

  const [users, setUsers] = React.useState<BDUser[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const [createOpen, setCreateOpen] = React.useState(false);
  const [form, setForm] = React.useState<BDForm>(EMPTY_FORM);
  const [formError, setFormError] = React.useState<string | undefined>();
  const [saving, setSaving] = React.useState(false);

  const [editing, setEditing] = React.useState<BDUser | null>(null);
  const [togglingId, setTogglingId] = React.useState<string | null>(null);

  const [deleting, setDeleting] = React.useState<BDUser | null>(null);
  const [deleteConfirm, setDeleteConfirm] = React.useState("");
  const [deleteBusy, setDeleteBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setUsers(await apiGet<BDUser[]>("/admin/bd-users"));
    } catch (error) {
      setLoadError(
        error instanceof Error
          ? error.message
          : "Could not load the Business Development team."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError(undefined);
    setEditing(null);
    setCreateOpen(true);
  };

  const openEdit = (user: BDUser) => {
    setForm({
      email: user.email,
      full_name: user.full_name ?? "",
      phone: user.phone ?? "",
    });
    setFormError(undefined);
    setEditing(user);
    setCreateOpen(true);
  };

  const save = async () => {
    if (!editing && !EMAIL_RE.test(form.email.trim())) {
      setFormError("Enter a valid work email address.");
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await apiPatch<BDUser>(`/admin/bd-users/${editing.id}`, {
          full_name: form.full_name.trim() || null,
          phone: form.phone.trim() || null,
        });
        toast({ title: "Details updated", description: editing.email });
      } else {
        await apiPost<BDUser>("/admin/bd-users", {
          email: form.email.trim(),
          full_name: form.full_name.trim() || null,
          phone: form.phone.trim() || null,
        });
        toast({
          title: "Business Development account added",
          description:
            "They can sign in on the normal login page with this address, using Google or email and password.",
        });
      }
      setCreateOpen(false);
      setEditing(null);
      setForm(EMPTY_FORM);
      await load();
    } catch (error) {
      toast({
        title: editing
          ? "Could not update this account"
          : "Could not add this account",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const toggleDisabled = async (user: BDUser) => {
    const disabling = user.status !== "disabled";
    setTogglingId(user.id);
    try {
      const updated = await apiPatch<BDUser>(`/admin/bd-users/${user.id}`, {
        status: disabling ? "disabled" : "active",
      });
      setUsers((current) =>
        current.map((row) => (row.id === updated.id ? updated : row))
      );
      toast({
        title: disabling ? "Account disabled" : "Account re-enabled",
        description: disabling
          ? `${updated.email} cannot sign in. Their leads are untouched and this can be undone.`
          : `${updated.email} can sign in again.`,
      });
    } catch (error) {
      toast({
        title: disabling
          ? "Could not disable this account"
          : "Could not re-enable this account",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setTogglingId(null);
    }
  };

  // The confirmation is about intent, not typing precision, so it mirrors the
  // server's own check rather than inventing a stricter one the server would
  // then accept anyway.
  const deleteConfirmed =
    deleting !== null &&
    ["delete", deleting.email.trim().toLowerCase()].includes(
      deleteConfirm.trim().toLowerCase()
    );

  const confirmDelete = async () => {
    if (!deleting || !deleteConfirmed) return;
    setDeleteBusy(true);
    try {
      const result = await apiDelete<{ email: string; leads_released: number }>(
        `/admin/bd-users/${deleting.id}?confirm=${encodeURIComponent(
          deleteConfirm.trim()
        )}`
      );
      setUsers((current) => current.filter((row) => row.id !== deleting.id));
      toast({
        title: "Account deleted",
        description:
          result.leads_released > 0
            ? `${result.email} is removed. Their leads are now unassigned on the BD board and can be reassigned.`
            : `${result.email} is removed. They owned no leads.`,
      });
      setDeleting(null);
      setDeleteConfirm("");
    } catch (error) {
      toast({
        title: "Could not delete this account",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Business Development"
        description="ReadyPick's own sales team. Adding someone here reserves the account; Firebase handles their sign-in credential."
        actions={
          <Button className="gap-2" onClick={openCreate}>
            <Plus className="h-4 w-4" /> Add BD member
          </Button>
        }
      />

      {loadError ? (
        <div className="rounded-md border p-4" role="alert">
          <p className="text-sm font-medium">
            The Business Development team could not be loaded.
          </p>
          <p className="mt-1 text-sm">{loadError}</p>
          <Button className="mt-3" size="sm" onClick={() => void load()}>
            Try again
          </Button>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[200px]">Name</TableHead>
                <TableHead className="min-w-[220px]">Email</TableHead>
                <TableHead>Phone</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Signed in</TableHead>
                <TableHead>Added</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-10 text-center">
                    Loading the Business Development team…
                  </TableCell>
                </TableRow>
              ) : users.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-10 text-center">
                    No Business Development accounts yet.
                  </TableCell>
                </TableRow>
              ) : (
                users.map((user) => (
                  <TableRow key={user.id}>
                    <TableCell>
                      <span className="flex items-center gap-2">
                        <UserRound className="h-4 w-4 shrink-0" />
                        <span className="font-medium">
                          {user.full_name || "Name not set"}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>{user.email}</TableCell>
                    <TableCell>{user.phone || "Not set"}</TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          user.status === "disabled" ? "outline" : "default"
                        }
                      >
                        {STATUS_LABEL[user.status] ?? user.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {user.signed_in ? "Yes" : "Not yet"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {user.created_at
                        ? new Date(user.created_at).toLocaleDateString()
                        : "Unknown"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1"
                          onClick={() => openEdit(user)}
                        >
                          <Pencil className="h-3.5 w-3.5" /> Edit
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1"
                          disabled={togglingId === user.id}
                          onClick={() => void toggleDisabled(user)}
                        >
                          {user.status === "disabled" ? (
                            <>
                              <Power className="h-3.5 w-3.5" /> Enable
                            </>
                          ) : (
                            <>
                              <PowerOff className="h-3.5 w-3.5" /> Disable
                            </>
                          )}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1 text-destructive"
                          data-testid={`delete-bd-${user.id}`}
                          onClick={() => {
                            setDeleteConfirm("");
                            setDeleting(user);
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" /> Delete
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) setEditing(null);
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editing
                ? "Edit Business Development member"
                : "Add a Business Development member"}
            </DialogTitle>
            <DialogDescription>
              {editing
                ? "The email address is the identity this account signs in with, so it cannot be changed here."
                : "No password is set here. They sign in on the normal login page with this address, using Google or email and password."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <FormField
              label="Work email"
              htmlFor="bd-email"
              required={!editing}
              hint={
                editing
                  ? undefined
                  : "This exact address must be the one they sign in with."
              }
            >
              <Input
                id="bd-email"
                type="email"
                value={form.email}
                disabled={saving || Boolean(editing)}
                aria-invalid={Boolean(formError)}
                onChange={(event) => {
                  setForm({ ...form, email: event.target.value });
                  setFormError(undefined);
                }}
              />
              <FieldError message={formError} />
            </FormField>
            <FormField label="Full name" htmlFor="bd-name">
              <Input
                id="bd-name"
                value={form.full_name}
                disabled={saving}
                onChange={(event) =>
                  setForm({ ...form, full_name: event.target.value })
                }
              />
            </FormField>
            <FormField label="Phone" htmlFor="bd-phone">
              <Input
                id="bd-phone"
                value={form.phone}
                disabled={saving}
                onChange={(event) =>
                  setForm({ ...form, phone: event.target.value })
                }
              />
            </FormField>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={saving}
              onClick={() => setCreateOpen(false)}
            >
              Cancel
            </Button>
            <Button disabled={saving} onClick={() => void save()}>
              {saving ? "Saving…" : editing ? "Save changes" : "Add member"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleting(null);
            setDeleteConfirm("");
          }
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Delete this Business Development account?</DialogTitle>
            <DialogDescription>
              This cannot be undone. If this person may come back, Disable is
              reversible and keeps their leads assigned to them.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="rounded-md border p-3">
              <p className="text-sm font-medium">{deleting?.email}</p>
              <p className="mt-2 text-sm">
                Any leads they own stay on the BD board and become unassigned,
                ready to be reassigned. Customers created from their signed
                agreements are not affected.
              </p>
            </div>
            <FormField
              label={`Type ${deleting?.email ?? ""} to confirm`}
              htmlFor="bd-delete-confirm"
            >
              <Input
                id="bd-delete-confirm"
                value={deleteConfirm}
                disabled={deleteBusy}
                autoComplete="off"
                onChange={(event) => setDeleteConfirm(event.target.value)}
              />
            </FormField>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={deleteBusy}
              onClick={() => setDeleting(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteBusy || !deleteConfirmed}
              onClick={() => void confirmDelete()}
            >
              {deleteBusy ? "Deleting…" : "Delete account"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
