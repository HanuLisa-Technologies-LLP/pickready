// The candidate's Settings page was renamed to "My Profile" (client decision,
// 2026-07-27) and absorbed the advanced form and main resume. This route is
// kept purely so existing links and bookmarks land somewhere useful.

import { redirect } from "next/navigation";

export default function PortalSettingsPage() {
  redirect("/portal/profile");
}
