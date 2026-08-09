// @vitest-environment jsdom
//
// BD Reach: one funnel, and the channel derived from the source (2026-08-09).
//
// Personal Reach and Social Reach merged. `bd_leads.channel` is unchanged and a
// Postgres CHECK still requires a social lead to carry a source and forbids one
// on a personal lead, so the thing that must not break is the DERIVATION: a rep
// now answers "where did this come from" and the channel follows. Getting it
// wrong is a 422 the rep cannot act on, or worse, a lead filed under the wrong
// source with no way to correct it (the channel is immutable after creation).

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  DIRECT_SOURCE,
  EMPTY_LEAD_FORM,
  LeadFormModal,
  channelForSource,
  validateLead,
} from "./lead-form-modal";
import type { BDLead } from "@/lib/bd-types";

afterEach(cleanup);

describe("channel derived from source", () => {
  it("files a directly approached company on the personal channel", () => {
    expect(channelForSource(DIRECT_SOURCE)).toBe("personal");
  });

  it("files every platform on the social channel", () => {
    for (const source of ["linkedin", "google", "facebook", "instagram", "x"]) {
      expect(channelForSource(source)).toBe("social");
    }
  });

  it("does not quietly file an unanswered source as personal at save time", () => {
    // The empty case is rejected by validation before it reaches this, which
    // is the point: defaulting it would file every hurried lead as direct.
    expect(validateLead({ ...EMPTY_LEAD_FORM, company_name: "Acme" }).social_source)
      .toBeTruthy();
  });
});

describe("lead form", () => {
  it("requires a company name and a source", () => {
    const errors = validateLead(EMPTY_LEAD_FORM);
    expect(errors.company_name).toBeTruthy();
    expect(errors.social_source).toBeTruthy();
  });

  it("accepts a directly approached lead with no platform", () => {
    const errors = validateLead({
      ...EMPTY_LEAD_FORM,
      company_name: "Acme",
      social_source: DIRECT_SOURCE,
    });
    expect(errors).toEqual({});
  });

  it("still rejects a malformed contact email", () => {
    const errors = validateLead({
      ...EMPTY_LEAD_FORM,
      company_name: "Acme",
      social_source: DIRECT_SOURCE,
      contact_email: "not-an-email",
    });
    expect(errors.contact_email).toBeTruthy();
  });

  it("asks for the source on one form rather than on one of two screens", () => {
    // The options themselves live in a Radix portal that only mounts on open,
    // so this asserts the field is present and answerable, not the menu.
    render(
      <LeadFormModal saving={false} onCancel={() => {}} onSave={() => {}} />
    );
    expect(screen.getByText("Source")).toBeTruthy();
    expect(
      screen.getByText(/approached directly, or the platform it was found on/i)
    ).toBeTruthy();
    expect(screen.getByText(/where the lead came from/i)).toBeTruthy();
  });

  it("shows a stored personal lead as directly approached, not as unanswered", () => {
    const lead = {
      id: "1",
      channel: "personal",
      company_name: "Acme",
      website: null,
      industry: null,
      location: null,
      contact_name: null,
      contact_email: null,
      contact_phone: null,
      social_source: null,
      progress: [],
      agreement: null,
      agreement_at: null,
      tenant_id: null,
      owner_user_id: null,
      owner_name: null,
      notes: null,
      archived_at: null,
      created_at: null,
      updated_at: null,
    } as unknown as BDLead;
    render(
      <LeadFormModal
        lead={lead}
        saving={false}
        onCancel={() => {}}
        onSave={() => {}}
      />
    );
    // The channel is immutable after creation, so editing must not present the
    // source as something that can be switched.
    expect(screen.getByText(/fixed once a lead is added/i)).toBeTruthy();
  });
});
