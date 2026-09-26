import { redirect } from "next/navigation";

// /org home. Jobs are the shared entry point for every org role.
//
// A SERVER redirect, not `router.replace` in an effect. The client version had
// to ship the page, hydrate, run the effect and only then navigate, so every
// sign-in paid a round trip and showed a skeleton for a screen nobody was ever
// meant to see. It is the only redirect-only route left: the compatibility
// redirects (`/bd/social`, the candidate settings page and the bare
// `/portal/assessments`) were deleted at the 2026-09 compatibility cutoff.
export default function OrgHomePage() {
  redirect("/org/jobs");
}
