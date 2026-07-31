import { ReachPage } from "@/components/bd/reach-page";

export const metadata = { title: "Social Reach" };

// The same funnel as Personal Reach, with one extra column: a social lead
// carries the platform it came from, and that source is required.
export default function SocialReachPage() {
  return (
    <ReachPage
      channel="social"
      title="Social Reach"
      description="Companies found on LinkedIn, Google, Facebook, Instagram or X, and how far each one has got."
    />
  );
}
