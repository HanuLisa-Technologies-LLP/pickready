import { ReachPage } from "@/components/bd/reach-page";

// `app/layout.tsx` appends "| PickReady" through a template, so the title here
// is just the page name.
export const metadata = { title: "Personal Reach" };

// Personal Reach is the BD Portal index: it is where a rep starts their day.
export default function PersonalReachPage() {
  return (
    <ReachPage
      channel="personal"
      title="Personal Reach"
      description="Companies the team approached directly, and how far each one has got."
    />
  );
}
