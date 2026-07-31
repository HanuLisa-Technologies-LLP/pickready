import { BDSettingsPage } from "@/components/bd/settings";

export const metadata = { title: "Settings" };

// The theme toggle lives ONLY here (CLAUDE.md rule 10).
export default function BDSettings() {
  return <BDSettingsPage />;
}
