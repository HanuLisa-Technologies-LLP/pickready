"use client";

// AI Dashboard, Customer Portal (2026-08-09). Same shape as the Dashboard page
// beside it: a thin route around a component, so the view can be reused if the
// Provider Portal ever wants a cross-customer version of it.

import { AIDashboardView } from "@/components/ai-dashboard";

export default function OrgAIDashboardPage() {
  return <AIDashboardView />;
}
