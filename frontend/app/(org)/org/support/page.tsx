import { SupportCustomerPanel } from "@/components/support-customer-panel";

export const metadata = { title: "Support" };

// The customer's own support conversations. A page rather than a settings card
// because it is somewhere people come BACK to: a thread has a reply waiting in
// it, and a card buried in Settings is a card nobody returns to.
//
// Not gated client-side beyond the nav entry: every route behind this screen
// runs through `require_capability("open_support_threads")`, and a page hidden
// by a stale client-side list is a page somebody is told does not exist.
export default function OrgSupportPage() {
  return <SupportCustomerPanel />;
}
