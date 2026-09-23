import { SupportProviderQueue } from "@/components/support-provider-queue";

export const metadata = { title: "Support" };

// Vivekium's cross-customer support queue.
//
// The UI route is /admin/support and the API it calls is /provider/support,
// which is this product's standing split: the Provider Portal lives at /admin
// in the UI and /provider in the API, and every other Provider screen already
// works this way. Every route it calls runs through `get_superadmin_db`, which
// enforces the owner audience and audit-logs the cross-tenant access.
export default function AdminSupportPage() {
  return <SupportProviderQueue />;
}
