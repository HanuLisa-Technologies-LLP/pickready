/**
 * Synthetic dashboard rows for the component tests.
 *
 * spec-doc6 C14: the Dashboard Specification's sample row uses a real person's
 * name, and it must not survive into code, fixtures, seed data or screenshots.
 * Everything here is obviously not a person.
 *
 * The DEFAULT row is a candidate who applied a moment ago: nothing graded,
 * nothing assessed. That is the common case for this whole phase, so it is
 * what a test has to opt out of rather than into.
 */

import { BAND_PENDING } from "./band";
import type { DashboardControls, DashboardRow } from "./types";

export function row(overrides: Partial<DashboardRow> = {}): DashboardRow {
  return {
    link_id: "00000000-0000-4000-8000-000000000001",
    candidate_id: "00000000-0000-4000-8000-000000000002",
    full_name: "Test Candidate Zero",
    system_id: "JSRS-Y4BN-8HGX",
    job_id: "00000000-0000-4000-8000-000000000003",
    job_title: "Staff Platform Engineer",

    source_type: "applied",
    source_label: "Applied",

    pre_screen_grade: null,
    pre_screen_label:
      "Not pre-screened. This application has not been graded, which is not the same as being graded Hold.",

    ready_pick_score: null,
    band: BAND_PENDING,
    band_label: "Pending Ready Pick Profile",
    band_screen_reader_label:
      "Status: Pending Ready Pick Profile, assessment in progress",
    confidence: null,
    confidence_indicator: "grayed",
    confidence_label: "Insufficient confidence",
    score_range: null,
    score_range_note:
      "No uncertainty interval is published by the evaluator, so no score range is shown.",

    note: "Ready Pick Profile not written yet.",
    note_is_pending: true,

    profile: null,
    profile_pending_reason:
      "The Ready Pick Profile has not been written yet. This says nothing about the PRISM Report, which is a different document.",

    team_review_count: 0,
    own_verdict: null,
    own_verdict_at: null,

    stage: "Applied",
    stage_label: "Applied",
    stage_on_hold: false,
    stored_status: "applied",

    under_integrity_review: false,
    archived: false,
    ...overrides,
  };
}

export function controls(
  overrides: Partial<DashboardControls> = {}
): DashboardControls {
  return {
    can_move_stage: true,
    stage_disabled_reason: null,
    can_team_review: true,
    team_review_disabled_reason: null,
    can_disposition_integrity: false,
    can_view_calibration: false,
    scoped_to_assignments: false,
    ...overrides,
  };
}
