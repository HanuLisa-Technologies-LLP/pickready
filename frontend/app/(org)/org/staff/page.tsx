"use client";

// Staff management (contract rev 2): GET/POST/DELETE /companies/me/staff.
// Client (or an HR Manager with `manage_staff`) creates HR Managers,
// Recruiters and Hiring Managers. Only Hiring Manager is capped at 5 active
// accounts — HR/Recruiter are uncapped. The server enforces the cap too and
// returns 409 on a 6th active hiring manager; we surface that as a toast.

import * as React from "react";
import { Plus, UserX } from "lucide-react";

import { apiDelete, apiGet, apiPost } from "@/lib/api";
import type { ApprovalLevelName, StaffMember, StaffRole } from "@/lib/types";
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

const MAX_HIRING_MANAGERS = 5;
const NONE = "__none__";

const ROLE_LABELS: Record<StaffRole, string> = {
  hr_manager: "HR Manager",
  recruiter: "Recruiter",
  hiring_manager: "Hiring Manager",
};

const APPROVAL_LEVELS: ApprovalLevelName[] = [
  "requested",
  "recommended",
  "approved",
  "ratified",
];

const EMPTY_FORM = {
  email: "",
  full_name: "",
  phone: "",
  role: "recruiter" as StaffRole,
  approval_level: NONE,
};

export default function StaffPage() {
  const { toast } = useToast();
  const [staff, setStaff] = React.useState<StaffMember[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [open, setOpen] = React.useState(false);
  const [form, setForm] = React.useState(EMPTY_FORM);
  const [creating, setCreating] = React.useState(false);
  const [removing, setRemoving] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<StaffMember[] | { staff: StaffMember[] }>(
        "/companies/me/staff"
      );
      setStaff(Array.isArray(res) ? res : res.staff ?? []);
    } catch (e) {
      toast({
        title: "Failed to load staff",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const isActive = (m: StaffMember) => m.status?.toLowerCase() !== "disabled";
  const activeHmCount = staff.filter(
    (m) => m.role === "hiring_manager" && isActive(m)
  ).length;
  const hmAtLimit = activeHmCount >= MAX_HIRING_MANAGERS;
  const counts = {
    hr_manager: staff.filter((m) => m.role === "hr_manager" && isActive(m))
      .length,
    recruiter: staff.filter((m) => m.role === "recruiter" && isActive(m))
      .length,
  };

  const create = async () => {
    setCreating(true);
    try {
      await apiPost("/companies/me/staff", {
        email: form.email,
        full_name: form.full_name,
        role: form.role,
        ...(form.phone ? { phone: form.phone } : {}),
        ...(form.role === "hiring_manager" && form.approval_level !== NONE
          ? { approval_level: form.approval_level }
          : {}),
      });
      toast({
        title: "Staff member added",
        description: `${form.full_name} (${ROLE_LABELS[form.role]}) will sign in via OTP.`,
      });
      setOpen(false);
      setForm(EMPTY_FORM);
      void load();
    } catch (e) {
      // Server-side 409 for a 6th active hiring manager surfaces here.
      toast({
        title: "Could not add staff member",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setCreating(false);
    }
  };

  const deactivate = async (m: StaffMember) => {
    setRemoving(m.id);
    try {
      await apiDelete(`/companies/me/staff/${m.id}`);
      toast({
        title: "Account deactivated",
        description: m.full_name || m.email,
      });
      void load();
    } catch (e) {
      toast({
        title: "Could not deactivate",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="Staff"
        description={`${counts.hr_manager} HR Manager(s) · ${counts.recruiter} Recruiter(s) · ${activeHmCount} of ${MAX_HIRING_MANAGERS} Hiring Managers.`}
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2" disabled={loading}>
                <Plus className="h-4 w-4" /> Add staff
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Add a staff member</DialogTitle>
                <DialogDescription>
                  They will sign in via OTP. Hiring Managers are limited to{" "}
                  {MAX_HIRING_MANAGERS} active accounts; HR Managers and
                  Recruiters are not capped.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <FormField label="Full name" htmlFor="st-name" required>
                  <Input
                    id="st-name"
                    value={form.full_name}
                    onChange={(e) =>
                      setForm({ ...form, full_name: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Email" htmlFor="st-email" required>
                  <Input
                    id="st-email"
                    type="email"
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                  />
                </FormField>
                <FormField label="Phone (optional)" htmlFor="st-phone">
                  <Input
                    id="st-phone"
                    type="tel"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  />
                </FormField>
                <FormField label="Role" required>
                  <Select
                    value={form.role}
                    onValueChange={(v) => setForm({ ...form, role: v as StaffRole })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="hr_manager">HR Manager</SelectItem>
                      <SelectItem value="recruiter">Recruiter</SelectItem>
                      <SelectItem value="hiring_manager" disabled={hmAtLimit}>
                        Hiring Manager
                        {hmAtLimit ? " — limit reached (5)" : ""}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </FormField>
                {form.role === "hiring_manager" ? (
                  <FormField
                    label="Approval level (optional)"
                    hint="Assign this Hiring Manager as the approver of a level in the job approval chain."
                  >
                    <Select
                      value={form.approval_level}
                      onValueChange={(v) =>
                        setForm({ ...form, approval_level: v })
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE}>None</SelectItem>
                        {APPROVAL_LEVELS.map((l) => (
                          <SelectItem key={l} value={l} className="capitalize">
                            {l}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </FormField>
                ) : null}
              </div>
              <DialogFooter>
                <Button
                  onClick={() => void create()}
                  disabled={creating || !form.email || !form.full_name}
                >
                  {creating ? "Adding…" : "Add staff member"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Email</TableHead>
            <TableHead>Phone</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Approval level</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          ) : staff.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">
                No staff accounts yet.
              </TableCell>
            </TableRow>
          ) : (
            staff.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="font-medium">{m.full_name}</TableCell>
                <TableCell>{m.email}</TableCell>
                <TableCell>{m.phone ?? "—"}</TableCell>
                <TableCell>
                  <Badge variant="outline">
                    {ROLE_LABELS[m.role] ?? m.role}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge
                    variant={isActive(m) ? "secondary" : "outline"}
                    className="capitalize"
                  >
                    {m.status ?? "active"}
                  </Badge>
                </TableCell>
                <TableCell>
                  {m.approval_level ? (
                    <Badge variant="outline" className="capitalize">
                      {m.approval_level}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {isActive(m) ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1"
                      disabled={removing === m.id}
                      onClick={() => void deactivate(m)}
                    >
                      <UserX className="h-4 w-4" />
                      {removing === m.id ? "Deactivating…" : "Deactivate"}
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
