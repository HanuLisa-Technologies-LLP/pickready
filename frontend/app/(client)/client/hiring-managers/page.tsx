"use client";

// Hiring Manager accounts (FR-2.2): max 5 per tenant — add disabled at 5.

import * as React from "react";
import { Plus } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { HiringManager } from "@/lib/types";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const MAX_HIRING_MANAGERS = 5;

export default function HiringManagersPage() {
  const { toast } = useToast();
  const [managers, setManagers] = React.useState<HiringManager[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [open, setOpen] = React.useState(false);
  const [form, setForm] = React.useState({ email: "", full_name: "", phone: "" });
  const [creating, setCreating] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<
        HiringManager[] | { hiring_managers: HiringManager[] }
      >("/companies/me/hiring-managers");
      setManagers(
        Array.isArray(res) ? res : res.hiring_managers ?? []
      );
    } catch (e) {
      toast({
        title: "Failed to load hiring managers",
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

  const atLimit = managers.length >= MAX_HIRING_MANAGERS;

  const create = async () => {
    setCreating(true);
    try {
      await apiPost("/companies/me/hiring-managers", {
        email: form.email,
        full_name: form.full_name,
        ...(form.phone ? { phone: form.phone } : {}),
      });
      toast({
        title: "Hiring Manager invited",
        description: `${form.full_name} will sign in via OTP.`,
      });
      setOpen(false);
      setForm({ email: "", full_name: "", phone: "" });
      void load();
    } catch (e) {
      toast({
        title: "Could not create account",
        description: e instanceof Error ? e.message : undefined,
        variant: "destructive",
      });
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Hiring Managers"
        description={`${managers.length} of ${MAX_HIRING_MANAGERS} accounts used.`}
        actions={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2" disabled={atLimit || loading}>
                <Plus className="h-4 w-4" />
                {atLimit ? "Limit reached (5)" : "Add Hiring Manager"}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Invite a Hiring Manager</DialogTitle>
                <DialogDescription>
                  They will receive an OTP-based invite. Maximum{" "}
                  {MAX_HIRING_MANAGERS} Hiring Manager accounts per company.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <FormField label="Full name" htmlFor="hm-name" required>
                  <Input
                    id="hm-name"
                    value={form.full_name}
                    onChange={(e) =>
                      setForm({ ...form, full_name: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Email" htmlFor="hm-email" required>
                  <Input
                    id="hm-email"
                    type="email"
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                  />
                </FormField>
                <FormField label="Phone (optional)" htmlFor="hm-phone">
                  <Input
                    id="hm-phone"
                    type="tel"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  />
                </FormField>
              </div>
              <DialogFooter>
                <Button
                  onClick={() => void create()}
                  disabled={creating || !form.email || !form.full_name}
                >
                  {creating ? "Inviting…" : "Invite"}
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
            <TableHead>Approval level</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          ) : managers.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="text-center text-muted-foreground">
                No Hiring Manager accounts yet.
              </TableCell>
            </TableRow>
          ) : (
            managers.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="font-medium">{m.full_name}</TableCell>
                <TableCell>{m.email}</TableCell>
                <TableCell>{m.phone ?? "—"}</TableCell>
                <TableCell>
                  {m.approval_level ? (
                    <Badge variant="outline" className="capitalize">
                      {m.approval_level}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
