"use client";

import * as React from "react";
import { BellRing, Briefcase, ListChecks, UserRound } from "lucide-react";

import { apiGet } from "@/lib/api";
import { AppShell } from "@/components/app-shell";

export default function PortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // The unread badge on Updates (workflow section 14). Fetched from the
  // summary route rather than the list, because this layout renders on every
  // candidate page and pulling rows nobody will show is a cost with no reader.
  //
  // A failed fetch renders NO badge rather than a zero: "we could not count"
  // and "you have nothing waiting" are different facts, and showing the second
  // when we mean the first is how somebody stops checking a page that has
  // something on it.
  const [unread, setUnread] = React.useState(0);
  React.useEffect(() => {
    let cancelled = false;
    apiGet<{ unread_count: number }>("/portal/updates/summary")
      .then((res) => {
        if (!cancelled) setUnread(res.unread_count ?? 0);
      })
      .catch(() => {
        if (!cancelled) setUnread(0);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <AppShell
      title="Candidate Portal"
      nav={[
        // Order and labels are the client's (2026-07-27): New Jobs, then
        // Applied Jobs, then the unified My Profile (formerly "Settings").
        // Updates joins them between the two lists and the profile: it is
        // where a candidate goes to find out what happened, so it belongs
        // beside the applications it describes rather than at the end.
        { href: "/portal", label: "New Jobs", icon: Briefcase, exact: true },
        { href: "/portal/applications", label: "Applied Jobs", icon: ListChecks },
        {
          href: "/portal/updates",
          label: "Updates",
          icon: BellRing,
          badge: unread,
        },
        { href: "/portal/profile", label: "My Profile", icon: UserRound },
      ]}
    >
      {children}
    </AppShell>
  );
}
