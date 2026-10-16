/**
 * Synthetic dashboard rows for the component tests.
 *
 * spec-doc6 C14: the Dashboard Specification's sample row uses a real person's
 * name, and it must not survive into code, fixtures, seed data or screenshots.
 * Everything here is obviously not a person.
 *
 * The DEFAULT row is a candidate who applied a moment ago: AI Matching has not
 * read the resume and nothing is assessed. That is the common case, so it is
 * what a test has to opt out of rather than into. The words are the server's
 * own (`services/dashboard.py`), copied so a test reads what a browser gets.
 */

import { STATE_NOT_CHECKED } from "./grade";
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

    ai_match_state: STATE_NOT_CHECKED,
    ai_match_label: "Not checked yet",
    ai_match_screen_reader_label:
      "Not checked yet. AI Matching has not read this resume yet.",
    ai_match_note: "AI Matching has not read this resume yet.",

    ranking_state: STATE_NOT_CHECKED,
    ranking_label: "Not checked yet",
    ranking_screen_reader_label:
      "Not checked yet. AI Matching has not read this resume yet.",
    ranking_note: "AI Matching has not read this resume yet.",
    confidence: null,
    confidence_indicator: "grayed",
    confidence_label: "No assessment yet",

    note: "Vivekium Profile not written yet.",
    note_is_pending: true,

    profile: null,
    profile_pending_reason:
      "The Vivekium Profile has not been written yet. This says nothing about the PRISM Report, which is a different document.",

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
    scoped_to_assignments: false,
    ...overrides,
  };
}
