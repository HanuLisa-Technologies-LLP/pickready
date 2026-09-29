"use client";

import * as React from "react";
import {
  Check,
  Copy,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Users,
  UserX,
} from "lucide-react";

import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api";
import type { StaffMember, StaffRole } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { CAP } from "@/lib/permissions";
import { usePermissions } from "@/lib/use-permissions";
import {
  STAFF_ROLE_LABELS as ROLE_LABELS,
  isPlausiblePhone,
  manageableRoles as manageableRolesFor,
  requiresDepartment,
} from "@/lib/staff-roles";
import { ReadOnlyNotice } from "@/components/permission-notice";
import { DepartmentPicker } from "@/components/department-picker";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import {
  EmptyState,
  ErrorState,
  LoadingRows,
  RowCard,
} from "@/components/page-primitives";
import { PermissionMatrixModal } from "@/components/permission-matrix-modal";
import { Button } from "@/components/ui/button";
import { ConfirmButton } from "@/components/confirm-button";
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
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function StaffPage() {
  const { toast } = useToast();
  const { user } = useAuth();
  const { can } = usePermissions();
  // The team list is a READ (view_staff); every control on it is a WRITE
  // (manage_staff), and each row also carries the server's own `can_manage`.
  const canManageStaff = can(CAP.manageStaff);
  const manageableRoles = React.useMemo(
    () => (canManageStaff ? manageableRolesFor(user?.role) : ([] as StaffRole[])),
    [user, canManageStaff]
  );

  const [staff, setStaff] = React.useState<StaffMember[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [open, setOpen] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const [editing, setEditing] = React.useState<StaffMember | null>(null);
  // The staff member whose permission matrix is open (spec §7.1).
  const [permissionsFor, setPermissionsFor] = React.useState<string | null>(null);
  const [savingEdit, setSavingEdit] = React.useState(false);
  const [workingId, setWorkingId] = React.useState<string | null>(null);
  const [inviteResult, setInviteResult] = React.useState<StaffMember | null>(null);
  const [copied, setCopied] = React.useState(false);
  const [form, setForm] = React.useState({
    email: "",
    full_name: "",
    phone: "",
    role: "recruiter" as StaffRole,
    department_id: "",
  });
  const [editForm, setEditForm] = React.useState({
    full_name: "",
    phone: "",
    role: "recruiter" as StaffRole,
    department_id: "",
  });

  const load = React.useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await apiGet<StaffMember[] | { staff: StaffMember[] }>(
        "/companies/me/staff"
      );
      setStaff(Array.isArray(result) ? result : result.staff ?? []);
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "Could not load the team."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const formComplete =
    Boolean(form.full_name.trim()) &&
    EMAIL_RE.test(form.email.trim()) &&
    isPlausiblePhone(form.phone) &&
    (!requiresDepartment(form.role) || Boolean(form.department_id));

  React.useEffect(() => {
    const fallback = manageableRoles[0];
    if (!fallback) return;
    if (!manageableRoles.includes(form.role)) {
      setForm((current) => ({ ...current, role: fallback }));
    }
    if (!manageableRoles.includes(editForm.role)) {
      setEditForm((current) => ({ ...current, role: fallback }));
    }
  }, [manageableRoles, form.role, editForm.role]);

  const closeDialog = () => {
    setOpen(false);
    setInviteResult(null);
    setCopied(false);
    setForm({
      email: "",
      full_name: "",
      phone: "",
      role: manageableRoles[0] ?? "hiring_manager",
      department_id: "",
    });
  };

  const create = async () => {
    if (!formComplete) return;
    setCreating(true);
    try {
      const member = await apiPost<StaffMember>("/companies/me/staff", {
        email: form.email.trim(),
        full_name: form.full_name.trim(),
        phone: form.phone.trim(),
        role: form.role,
        department_id: requiresDepartment(form.role) ? form.department_id : null,
      });
      setStaff((current) => [...current, { ...member, invite_link: null }]);
      setInviteResult(member);
      toast({
        title: "Team invitation created",
        description:
          member.email_dispatch === "queued"
            ? "Email queued. You can also copy the invitation link."
            : "Email delivery is not configured. Copy and share the invitation link.",
      });
    } catch (error) {
      toast({
        title: "Could not add team member",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setCreating(false);
    }
  };

  const resend = async (member: StaffMember) => {
    setWorkingId(member.id);
    try {
      const result = await apiPost<StaffMember>(
        `/companies/me/staff/${member.id}/resend-invite`
      );
      setInviteResult(result);
      setOpen(true);
      setStaff((current) =>
        current.map((item) =>
          item.id === member.id ? { ...item, ...result, invite_link: null } : item
        )
      );
      toast({ title: "A fresh invitation was created" });
    } catch (error) {
      toast({
        title: "Could not resend invitation",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setWorkingId(null);
    }
  };

  const deactivate = async (member: StaffMember) => {
    setWorkingId(member.id);
    try {
      const updated = await apiDelete<StaffMember>(
        `/companies/me/staff/${member.id}`
      );
      setStaff((current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      toast({
        title: "Team member deactivated",
        description: member.full_name || member.email,
      });
    } catch (error) {
      toast({
        title: "Could not deactivate account",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setWorkingId(null);
    }
  };

  const reactivate = async (member: StaffMember) => {
    setWorkingId(member.id);
    try {
      const updated = await apiPost<StaffMember>(
        `/companies/me/staff/${member.id}/reactivate`
      );
      setStaff((current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      toast({ title: "Team member reactivated" });
    } catch (error) {
      toast({
        title: "Could not reactivate account",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setWorkingId(null);
    }
  };

  const openEdit = (member: StaffMember) => {
    setEditForm({
      full_name: member.full_name || "",
      phone: member.phone || "",
      role: member.role,
      department_id: member.department_id || "",
    });
    setEditing(member);
  };

  const editComplete =
    Boolean(editForm.full_name.trim()) &&
    (!requiresDepartment(editForm.role) || Boolean(editForm.department_id));

  const saveEdit = async () => {
    if (!editing || !editComplete) return;
    setSavingEdit(true);
    try {
      const updated = await apiPut<StaffMember>(
        `/companies/me/staff/${editing.id}`,
        {
          full_name: editForm.full_name.trim(),
          phone: editForm.phone.trim() || null,
          role: editForm.role,
          approval_level: null,
          department_id: requiresDepartment(editForm.role)
            ? editForm.department_id
            : null,
        }
      );
      setStaff((current) =>
        current.map((item) => (item.id === updated.id ? updated : item))
      );
      setEditing(null);
      toast({ title: "Team member updated" });
    } catch (error) {
      toast({
        title: "Could not update team member",
        description: error instanceof Error ? error.message : undefined,
        variant: "destructive",
      });
    } finally {
      setSavingEdit(false);
    }
  };

  const active = staff.filter(
    (member) => member.status.toLowerCase() !== "disabled"
  ).length;

  return (
    <div>
      <PageHeader
        eyebrow="Customer Portal"
        title="Team"
        description={`${active} active team member${
          active === 1 ? "" : "s"
        }.${canManageStaff ? " Manage permissions for the roles beneath you." : ""}`}
        actions={
          canManageStaff ? (
          <Dialog
            open={open}
            onOpenChange={(next) => (next ? setOpen(true) : closeDialog())}
          >
            <DialogTrigger asChild>
              <Button disabled={loading || manageableRoles.length === 0}>
                <Plus className="h-4 w-4" aria-hidden="true" /> Add team member
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  {inviteResult ? "Invitation ready" : "Add team member"}
                </DialogTitle>
                <DialogDescription>
                  {inviteResult
                    ? "Share this single-use link if the email does not arrive."
                    : "They set their own password from the invitation sent to this email address."}
                </DialogDescription>
              </DialogHeader>
              {inviteResult ? (
                <div className="space-y-4">
                  <div className="rounded-xl border border-border bg-brand-100/50 p-4">
                    <p className="font-medium">
                      {inviteResult.full_name || inviteResult.email}
                    </p>
                    <p className="text-sm">
                      {ROLE_LABELS[inviteResult.role]} · {inviteResult.email}
                    </p>
                  </div>
                  {inviteResult.invite_link ? (
                    <FormField label="Invitation link" htmlFor="team-invite-link">
                      <div className="flex gap-2">
                        <Input
                          id="team-invite-link"
                          readOnly
                          value={inviteResult.invite_link}
                        />
                        <Button
                          type="button"
                          variant="outline"
                          className="gap-1"
                          onClick={() => {
                            // `navigator.clipboard` is undefined over plain
                            // http and rejects when the page is not focused.
                            // Awaiting it bare threw out of the handler and
                            // left the button saying "Copy" with no reason,
                            // for the one link that cannot be recovered from
                            // anywhere else on this screen.
                            void navigator.clipboard
                              ?.writeText(inviteResult.invite_link || "")
                              .then(
                                () => setCopied(true),
                                () =>
                                  toast({
                                    variant: "destructive",
                                    title: "Copy failed",
                                    description:
                                      "Select the link above and copy it by hand.",
                                  })
                              );
                          }}
                        >
                          {copied ? (
                            <Check className="h-4 w-4" aria-hidden="true" />
                          ) : (
                            <Copy className="h-4 w-4" aria-hidden="true" />
                          )}
                          {copied ? "Copied" : "Copy"}
                          <span role="status" className="sr-only">
                            {copied ? "Invitation link copied to clipboard" : ""}
                          </span>
                        </Button>
                      </div>
                    </FormField>
                  ) : (
                    <p role="alert" className="text-sm text-destructive">
                      The server did not return an invitation link. Create a new
                      invitation before closing this dialog.
                    </p>
                  )}
                  <DialogFooter>
                    <Button onClick={closeDialog}>Done</Button>
                  </DialogFooter>
                </div>
              ) : (
                <>
                  <div className="space-y-4">
                    <FormField label="Name" htmlFor="team-name" required>
                      <Input
                        id="team-name"
                        value={form.full_name}
                        aria-invalid={
                          Boolean(form.full_name) && !form.full_name.trim()
                        }
                        onChange={(event) =>
                          setForm({ ...form, full_name: event.target.value })
                        }
                      />
                    </FormField>
                    <FormField label="Email" htmlFor="team-email" required>
                      <Input
                        id="team-email"
                        type="email"
                        value={form.email}
                        aria-invalid={
                          Boolean(form.email) && !EMAIL_RE.test(form.email.trim())
                        }
                        onChange={(event) =>
                          setForm({ ...form, email: event.target.value })
                        }
                      />
                    </FormField>
                    <FormField label="Mobile number" htmlFor="team-phone" required>
                      <Input
                        id="team-phone"
                        type="tel"
                        inputMode="tel"
                        autoComplete="tel"
                        value={form.phone}
                        aria-invalid={
                          Boolean(form.phone) && !isPlausiblePhone(form.phone)
                        }
                        onChange={(event) =>
                          setForm({ ...form, phone: event.target.value })
                        }
                      />
                    </FormField>
                    <FormField label="Role" required>
                      <Select
                        value={form.role}
                        onValueChange={(role) =>
                          setForm({
                            ...form,
                            role: role as StaffRole,
                            department_id: requiresDepartment(role as StaffRole)
                              ? form.department_id
                              : "",
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {manageableRoles.map((role) => (
                            <SelectItem key={role} value={role}>
                              {ROLE_LABELS[role]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormField>
                    {requiresDepartment(form.role) ? (
                      <DepartmentPicker
                        id="team-department"
                        required
                        hint="A Functional Head sees this department's jobs, candidates and reports, and no other."
                        value={form.department_id}
                        onChange={(department) =>
                          setForm((current) => ({
                            ...current,
                            department_id: department.id,
                          }))
                        }
                      />
                    ) : null}
                  </div>
                  <DialogFooter>
                    <Button
                      variant="outline"
                      disabled={creating}
                      onClick={closeDialog}
                    >
                      Cancel
                    </Button>
                    <Button
                      disabled={creating || !formComplete}
                      onClick={() => void create()}
                    >
                      {creating ? "Creating" : "Add member"}
                    </Button>
                  </DialogFooter>
                </>
              )}
            </DialogContent>
          </Dialog>
          ) : undefined
        }
      />

      <ReadOnlyNotice
        canEdit={canManageStaff}
        resource="your team"
        className="mb-6"
      />

      {loadError ? (
        <ErrorState
          title="Could not load your team"
          description={loadError}
          action={
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          }
        />
      ) : loading ? (
        <LoadingRows rows={4} label="Loading team" />
      ) : staff.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No team members yet"
          description={
            canManageStaff
              ? "Add a team member in a role beneath yours, then choose the permissions they need."
              : "Nobody has been added to the team yet."
          }
        />
      ) : (
        <>
        <div className="hidden md:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Department</TableHead>
                <TableHead>Account</TableHead>
                <TableHead>Invitation</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {staff.map((member) => {
                  const disabled = member.status.toLowerCase() === "disabled";
                  const pending =
                    member.invite_status === "pending" ||
                    member.status.toLowerCase() === "invited";
                  return (
                    <TableRow key={member.id}>
                      <TableCell className="font-semibold">
                        {member.full_name || "Name pending"}
                      </TableCell>
                      <TableCell>{member.email}</TableCell>
                      <TableCell>
                        <Badge variant="outline">
                          {ROLE_LABELS[member.role] ?? member.role}
                        </Badge>
                      </TableCell>
                      <TableCell>{member.department_name ?? ""}</TableCell>
                      <TableCell>
                        <Badge variant={disabled ? "outline" : "secondary"} className="capitalize">
                          {member.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <span className="capitalize">
                          {member.invite_status ?? (disabled ? "revoked" : "accepted")}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        {member.can_manage ? (
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="gap-1"
                            disabled={workingId === member.id}
                            onClick={() => openEdit(member)}
                          >
                            <Pencil className="h-3.5 w-3.5" /> Edit
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="gap-1"
                            disabled={workingId === member.id}
                            onClick={() => setPermissionsFor(member.id)}
                          >
                            <ShieldCheck className="h-3.5 w-3.5" /> Permissions
                          </Button>
                          {pending && !disabled ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="gap-1"
                              disabled={workingId === member.id}
                              onClick={() => void resend(member)}
                            >
                              <RefreshCw className="h-3.5 w-3.5" /> Resend
                            </Button>
                          ) : null}
                          {disabled ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="gap-1"
                              disabled={workingId === member.id}
                              onClick={() => void reactivate(member)}
                            >
                              <RotateCcw className="h-3.5 w-3.5" /> Reactivate
                            </Button>
                          ) : (
                            <ConfirmButton
                              variant="outline"
                              size="sm"
                              className="gap-1"
                              disabled={workingId === member.id}
                              title={`Deactivate ${member.full_name || member.email}?`}
                              description="They lose access to this workspace immediately. Their jobs, notes and history stay. You can invite them back later."
                              confirmLabel="Deactivate"
                              onConfirm={() => void deactivate(member)}
                            >
                              <UserX className="h-3.5 w-3.5" aria-hidden="true" />{" "}
                              Deactivate
                            </ConfirmButton>
                          )}
                        </div>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  );
              })}
            </TableBody>
          </Table>
        </div>

        {/* Below md the same people, stacked as cards. */}
        <ul className="space-y-3 md:hidden">
          {staff.map((member) => {
            const disabled = member.status.toLowerCase() === "disabled";
            const pending =
              member.invite_status === "pending" ||
              member.status.toLowerCase() === "invited";
            return (
              <li key={member.id}>
                <RowCard
                  title={member.full_name || "Name pending"}
                  meta={member.email}
                  actions={
                    <Badge variant="outline">
                      {ROLE_LABELS[member.role] ?? member.role}
                    </Badge>
                  }
                >
                  {member.department_name ? (
                    <p className="text-xs">Department: {member.department_name}</p>
                  ) : null}
                  <p className="text-xs capitalize">
                    Account: {member.status}, invitation:{" "}
                    {member.invite_status ?? (disabled ? "revoked" : "accepted")}
                  </p>
                  {member.can_manage ? (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={workingId === member.id}
                      onClick={() => openEdit(member)}
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={workingId === member.id}
                      onClick={() => setPermissionsFor(member.id)}
                    >
                      <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />{" "}
                      Permissions
                    </Button>
                    {pending && !disabled ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={workingId === member.id}
                        onClick={() => void resend(member)}
                      >
                        <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />{" "}
                        Resend
                      </Button>
                    ) : null}
                    {disabled ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={workingId === member.id}
                        onClick={() => void reactivate(member)}
                      >
                        <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />{" "}
                        Reactivate
                      </Button>
                    ) : (
                      <ConfirmButton
                        variant="outline"
                        size="sm"
                        disabled={workingId === member.id}
                        title={`Deactivate ${member.full_name || member.email}?`}
                        description="They lose access to this workspace immediately. Their jobs, notes and history stay. You can invite them back later."
                        confirmLabel="Deactivate"
                        onConfirm={() => void deactivate(member)}
                      >
                        <UserX className="h-3.5 w-3.5" aria-hidden="true" />{" "}
                        Deactivate
                      </ConfirmButton>
                    )}
                  </div>
                  ) : null}
                </RowCard>
              </li>
            );
          })}
        </ul>
        </>
      )}

      <Dialog open={editing !== null} onOpenChange={(next) => {
        if (!next && !savingEdit) setEditing(null);
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit team member</DialogTitle>
            <DialogDescription>
              Update the person&apos;s profile or role. Their sign-in identity
              and invitation history remain unchanged.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <FormField label="Name" htmlFor="edit-team-name" required>
              <Input
                id="edit-team-name"
                value={editForm.full_name}
                onChange={(event) =>
                  setEditForm({ ...editForm, full_name: event.target.value })
                }
              />
            </FormField>
            <FormField label="Phone" htmlFor="edit-team-phone">
              <Input
                id="edit-team-phone"
                value={editForm.phone}
                onChange={(event) =>
                  setEditForm({ ...editForm, phone: event.target.value })
                }
              />
            </FormField>
            <FormField label="Role">
              <Select
                value={editForm.role}
                onValueChange={(role) =>
                  setEditForm({
                    ...editForm,
                    role: role as StaffRole,
                    department_id: requiresDepartment(role as StaffRole)
                      ? editForm.department_id
                      : "",
                  })
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {manageableRoles.map((role) => (
                    <SelectItem key={role} value={role}>{ROLE_LABELS[role]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormField>
            {requiresDepartment(editForm.role) ? (
              <DepartmentPicker
                id="edit-team-department"
                required
                value={editForm.department_id}
                onChange={(department) =>
                  setEditForm((current) => ({
                    ...current,
                    department_id: department.id,
                  }))
                }
              />
            ) : null}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={savingEdit}
              onClick={() => setEditing(null)}
            >
              Cancel
            </Button>
            <Button
              disabled={savingEdit || !editComplete}
              onClick={() => void saveEdit()}
            >
              {savingEdit ? "Saving" : "Save changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <PermissionMatrixModal
        open={permissionsFor !== null}
        onOpenChange={(next) => !next && setPermissionsFor(null)}
        userId={permissionsFor}
      />
    </div>
  );
}

