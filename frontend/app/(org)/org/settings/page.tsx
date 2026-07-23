import { SettingsPage } from "@/components/settings-page";

export const metadata = { title: "Settings — PickReady" };

// The theme toggle lives ONLY here (claude.md rule 10).
export default function OrgSettingsPage() {
  return <SettingsPage />;
}
