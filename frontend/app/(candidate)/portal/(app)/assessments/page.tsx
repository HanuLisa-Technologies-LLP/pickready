// `/portal/assessments` has no page of its own, and never needs one: an
// assessment is reached from its application on Applied Jobs, where the
// "Start assessment" control appears only once the hiring team has invited
// the candidate.
//
// The bare path still arrives, from Updates entries and emails written before
// feed links named a specific application (`/portal/assessments/<id>` does
// exist, and that route is untouched). A 404 for a link the product itself
// sent would read as the assessment being gone, so the bare path goes to
// the list where the candidate's assessments actually live.

import { redirect } from "next/navigation";

export default function PortalAssessmentsIndex(): never {
  redirect("/portal/applications");
}
