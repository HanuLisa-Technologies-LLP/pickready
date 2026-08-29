"use client";

// The activity trail for one customer (RBAC_SPECIFICATION.md 31).
//
// WHY THIS IS A SECTION AND NOT A NAV ITEM
// ----------------------------------------
// The Provider Portal nav is Customers + Business Development + Billing +
// Settings, and CLAUDE.md records that list as fixed. Activity is also not a
// cross-customer surface: every question 31 asks ("who changed this", "which
// job", "which candidate") is asked about one company, so it belongs on that
// company's detail view rather than on a page that would have to ask which
// company first.
//
// WHY IT LOADS ON DEMAND
// ----------------------
// The trail is unbounded and the detail dialog is opened to check a phone
// number as often as to investigate something. Fetching a page of fifty rows
// every time the dialog opens would spend the request on the common case.
//
// RBAC 31's closing sentence is the load-bearing one: the audit trail MUST NOT
// depend exclusively on dashboard rendering. Nothing here writes, derives or
// summarises; every field shown is stored on the row, and the trail is
// complete whether or not anybody opens this section.

import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { ApiError, apiGet } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const PAGE_SIZE = 50;

// Actions the API returns, in the wording a person reads. An unmapped action
// falls back to its own identifier rather than to a blank: an unfamiliar
// string is information, and an empty cell is not.
const ACTION_LABELS: Record<string, string> = {
  job_created: "Created the JD",
  job_jd_edited: "Edited the JD",
  job_sent_to_hiring_manager: "Sent the JD to the Hiring Manager",
  job_criteria_edited: "Edited the hiring criteria",
  job_finalized: "Finalised the role definition",
  job_published: "Published the job",
  candidate_applied: "Applied",
  candidate_shortlisted: "Shortlisted a candidate",
  candidate_rejected: "Rejected a candidate",
  candidate_stage_moved: "Moved a candidate to another stage",
  integrity_flag_raised: "Raised an integrity finding",
  integrity_disposition_recorded: "Recorded an integrity disposition",
  team_review_remark_added: "Added a Team Review remark",
  authorization_refused: "Was refused an action",
  staff_created: "Added a team member",
  superadmin_access: "Opened a Provider Portal view",
};

const ROLE_LABELS: Record<string, string> = {
  client: "Super Admin",
  recruitment_manager: "Recruitment Manager",
  hr_manager: "HR Manager",
  recruiter: "Recruiter",
  hiring_manager: "Hiring Manager",
  interview_manager: "Interview Manager",
  candidate: "Candidate",
  unknown: "Role not recorded",
};

export type ActivityRow = {
  id: string;
  at: string;
  actor_user_id: string | null;
  actor_role: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  job_id: string | null;
  application_id: string | null;
  candidate_id: string | null;
  previous_state: Record<string, unknown> | null;
  new_state: Record<string, unknown> | null;
  agent_name: string | null;
  exceptional: boolean;
};

type ActivityResponse = {
  customer_id: string;
  items: ActivityRow[];
  limit: number;
  offset: number;
};

function formatWhen(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// A state snapshot is whatever the writer stored for the fields that changed,
// so it is rendered as key/value rather than interpreted. Interpreting it here
// would make this view decide what an auditor is allowed to see.
function StateSummary({
  label,
  state,
}: {
  label: string;
  state: Record<string, unknown> | null;
}) {
  if (!state || Object.keys(state).length === 0) {
    return (
      <span className="text-xs">
        {label}: <span className="italic">none recorded</span>
      </span>
    );
  }
  return (
    <span className="text-xs">
      {label}:{" "}
      {Object.entries(state)
        .map(([key, value]) => `${key} = ${String(value)}`)
        .join(", ")}
    </span>
  );
}

function Row({ row }: { row: ActivityRow }) {
  return (
    <li className="border-b py-3 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-sm font-medium">
          {ACTION_LABELS[row.action] ?? row.action}
        </span>
        <span className="text-xs">{formatWhen(row.at)}</span>
        {/* RBAC 30: the role AT THE TIME of the action, not the role now. */}
        <Badge variant="outline">
          {ROLE_LABELS[row.actor_role ?? "unknown"] ?? row.actor_role}
        </Badge>
        {/* RBAC 34: an agent action names the agent beside its human. */}
        {row.agent_name ? (
          <Badge variant="outline">via {row.agent_name}</Badge>
        ) : null}
        {/* 7.5 override, spec-doc6 C13 exception. Marked, never hidden. */}
        {row.exceptional ? <Badge variant="outline">Exception</Badge> : null}
      </div>
      <div className="mt-1 flex flex-col gap-0.5">
        <StateSummary label="Was" state={row.previous_state} />
        <StateSummary label="Now" state={row.new_state} />
      </div>
      {row.job_id || row.candidate_id ? (
        <p className="mt-1 font-mono text-xs">
          {row.job_id ? `job ${row.job_id}` : null}
          {row.job_id && row.candidate_id ? " . " : null}
          {row.candidate_id ? `candidate ${row.candidate_id}` : null}
        </p>
      ) : null}
    </li>
  );
}

export function CustomerActivitySection({ customerId }: { customerId: string }) {
  const [open, setOpen] = React.useState(false);
  const [rows, setRows] = React.useState<ActivityRow[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [offset, setOffset] = React.useState(0);

  const load = React.useCallback(
    async (nextOffset: number) => {
      setLoading(true);
      setError(null);
      try {
        const data = await apiGet<ActivityResponse>(
          `/provider/customers/${customerId}/activity?limit=${PAGE_SIZE}&offset=${nextOffset}`,
        );
        setRows((current) =>
          nextOffset === 0 ? data.items : [...(current ?? []), ...data.items],
        );
        setOffset(nextOffset);
      } catch (caught) {
        // Surfaced, never swallowed. An activity list that silently rendered
        // empty on a failed request would read as "nothing happened", which is
        // the one thing an audit view must never say by accident.
        setError(
          caught instanceof ApiError
            ? caught.message
            : "Could not load this customer's activity",
        );
      } finally {
        setLoading(false);
      }
    },
    [customerId],
  );

  // Reset when the dialog is pointed at a different customer, so an open
  // section never shows one company's trail under another's name.
  React.useEffect(() => {
    setOpen(false);
    setRows(null);
    setError(null);
    setOffset(0);
  }, [customerId]);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && rows === null && !loading) void load(0);
  }

  return (
    <section>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 text-left"
      >
        {open ? (
          <ChevronDown className="h-4 w-4" aria-hidden />
        ) : (
          <ChevronRight className="h-4 w-4" aria-hidden />
        )}
        <h3 className="text-base font-semibold">Activity</h3>
      </button>
      <p className="ml-6 text-xs">
        Who changed what, when, and what it was before.
      </p>

      {open ? (
        <div className="ml-6 mt-3">
          {error ? (
            <div className="rounded-md border p-3">
              <p className="text-sm">{error}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={() => void load(0)}
              >
                Try again
              </Button>
            </div>
          ) : null}

          {rows !== null && rows.length === 0 && !error ? (
            <p className="text-sm">
              No recorded activity for this customer yet.
            </p>
          ) : null}

          {rows && rows.length > 0 ? (
            <ul className="max-h-80 overflow-y-auto">
              {rows.map((row) => (
                <Row key={row.id} row={row} />
              ))}
            </ul>
          ) : null}

          {loading ? <p className="mt-2 text-sm">Loading</p> : null}

          {rows && rows.length > 0 && rows.length % PAGE_SIZE === 0 && !loading ? (
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => void load(offset + PAGE_SIZE)}
            >
              Load more
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
