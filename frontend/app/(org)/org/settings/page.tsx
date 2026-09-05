import { SettingsPage } from "@/components/settings-page";
import { EmailSendersCard } from "@/components/email-senders-card";

export const metadata = { title: "Settings" };

// The theme toggle lives ONLY here (claude.md rule 10).
export default function OrgSettingsPage() {
  return (
    <SettingsPage>
      {/* Corporate email senders (2026-09-05 spec): the card hides itself
          for accounts without manage_email_senders. */}
      <EmailSendersCard />
    </SettingsPage>
  );
}
