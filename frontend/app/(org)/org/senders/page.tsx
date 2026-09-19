import { SenderAuthorizationQueue } from "@/components/sender-authorization-queue";

export const metadata = { title: "Sender Authorization" };

// The client Super Admin's decision surface: which addresses may send email in
// this company's name. A page of its own rather than another settings card,
// because it is a QUEUE somebody returns to, not configuration set once.
//
// The queue hides itself when the server says this account cannot authorize
// senders, and the nav entry is gated on the same capability. So a Recruitment
// Manager is neither shown the link nor handed a dead page if they guess the
// URL: authorization is decided server-side either way.
export default function OrgSenderAuthorizationPage() {
  return <SenderAuthorizationQueue />;
}
