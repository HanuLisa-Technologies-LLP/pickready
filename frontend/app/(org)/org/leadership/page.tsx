"use client";

// Leadership Intelligence (owner spec 2026-09-29, section 28), which replaced
// the Drishti page. The screen is `components/leadership-intelligence`; this
// route only mounts it, so a test can render the component without a router.

import { LeadershipIntelligencePage } from "@/components/leadership-intelligence";

export default function LeadershipPage() {
  return <LeadershipIntelligencePage />;
}
