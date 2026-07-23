"use client";

// Owner console — tenant onboarding only (contract rev 2). The Owner does NOT
// create staff; staff are created by the Client (or an HR Manager with
// `manage_staff`) in the client-org portal. The old "Assign staff" modal and
// its POST /admin/tenants/{id}/staff call have been removed.

import * as React from "react";
import { Plus } from "lucide-react";

import { apiGet, apiPost } from "@/lib/api";
import type { Tenant } from "@/lib/types";
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/ui/form";
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

export default function TenantsPage() {
  const { toast } = useToast();
  const [tenants, setTenants] = React.useState<Tenant[]>([]);
  const [loading, setLoading] = React.useState(true);

  // Create tenant dialog
  const [createOpen, setCreateOpen] = React.useState(false);
  const [form, setForm] = React.useState({
    name: "",
    domain: "",
    client_email: "",
    client_phone: "",
  });
  const [creating, setCreating] = React.useState(false);

  const load = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGet<Tenant[] | { tenants: Tenant[] }>(
        "/admin/tenants"
      );
      setTenants(Array.isArray(res) ? res : res.tenants ?? []);
    } catch (e) {
      toast({
        title: "Failed to load tenants",
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

  const createTenant = async () => {
    setCreating(true);
    try {
      await apiPost("/admin/tenants", {
        name: form.name,
        domain: form.domain,
        client_email: form.client_email,
        client_phone: form.client_phone,
      });
      toast({ title: "Tenant created", description: form.name });
      setCreateOpen(false);
      setForm({ name: "", domain: "", client_email: "", client_phone: "" });
      void load();
    } catch (e) {
      toast({
        title: "Could not create tenant",
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
        title="Tenants"
        description="Client companies onboarded on the platform."
        actions={
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2">
                <Plus className="h-4 w-4" /> New tenant
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Onboard a new client tenant</DialogTitle>
                <DialogDescription>
                  Creates the tenant and its Client account. The Client signs
                  in via OTP (dual email+mobile on first login) and manages
                  their own staff.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <FormField label="Company name" htmlFor="t-name" required>
                  <Input
                    id="t-name"
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </FormField>
                <FormField
                  label="Email/sending domain"
                  htmlFor="t-domain"
                  required
                  hint="Used for client-domain outbound email (Resend verified domain)."
                >
                  <Input
                    id="t-domain"
                    placeholder="acme.com"
                    value={form.domain}
                    onChange={(e) =>
                      setForm({ ...form, domain: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Client email" htmlFor="t-email" required>
                  <Input
                    id="t-email"
                    type="email"
                    value={form.client_email}
                    onChange={(e) =>
                      setForm({ ...form, client_email: e.target.value })
                    }
                  />
                </FormField>
                <FormField label="Client mobile" htmlFor="t-phone" required>
                  <Input
                    id="t-phone"
                    type="tel"
                    value={form.client_phone}
                    onChange={(e) =>
                      setForm({ ...form, client_phone: e.target.value })
                    }
                  />
                </FormField>
              </div>
              <DialogFooter>
                <Button
                  onClick={() => void createTenant()}
                  disabled={
                    creating ||
                    !form.name ||
                    !form.domain ||
                    !form.client_email ||
                    !form.client_phone
                  }
                >
                  {creating ? "Creating…" : "Create tenant"}
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
            <TableHead>Domain</TableHead>
            <TableHead>Client email</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={3} className="text-center text-muted-foreground">
                Loading…
              </TableCell>
            </TableRow>
          ) : tenants.length === 0 ? (
            <TableRow>
              <TableCell colSpan={3} className="text-center text-muted-foreground">
                No tenants yet.
              </TableCell>
            </TableRow>
          ) : (
            tenants.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-medium">{t.name}</TableCell>
                <TableCell>{t.domain}</TableCell>
                <TableCell>{t.client_email ?? "—"}</TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
