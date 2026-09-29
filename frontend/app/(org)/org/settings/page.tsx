import { SettingsPage } from "@/components/settings-page";
import { EmailSendersCard } from "@/components/email-senders-card";

export const metadata = { title: "Settings" };

// The theme toggle lives ONLY here (claude.md rule 10).
export default function OrgSettingsPage() {
  return (
    <SettingsPage>
      {/* Corporate email senders (2026-09-05 spec): the card hides itself
          for accounts without view_email_senders, and shows the list without
          its Add control to a reader who may not manage it. */}
      <EmailSendersCard />
    </SettingsPage>
  );
}
