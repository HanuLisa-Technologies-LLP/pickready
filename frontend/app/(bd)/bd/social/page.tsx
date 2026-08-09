import { redirect } from "next/navigation";

// Social Reach merged into BD Reach on 2026-08-09.
//
// The route survives as a redirect rather than being deleted: reps have this
// URL bookmarked and it is pasted in internal threads, and a 404 on a page
// someone used yesterday reads as the portal being broken rather than as a
// screen having moved. The source filter on BD Reach is where the social leads
// now live.
export default function SocialReachRedirect() {
  redirect("/bd");
}
