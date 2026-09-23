import { redirect } from "next/navigation";

// /org home. Jobs are the shared entry point for every org role.
//
// A SERVER redirect, not `router.replace` in an effect. The client version had
// to ship the page, hydrate, run the effect and only then navigate, so every
// sign-in paid a round trip and showed a skeleton for a screen nobody was ever
// meant to see. The same shape as the other two redirect-only routes in this
// product, `/bd/social` and the candidate settings page.
export default function OrgHomePage() {
  redirect("/org/jobs");
}
