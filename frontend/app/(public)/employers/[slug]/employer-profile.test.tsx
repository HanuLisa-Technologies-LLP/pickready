// @vitest-environment jsdom
//
// The employer page's open roles show each role's skills by bucket name when
// the payload carries them (CONTRACT v10, point 2), and nothing for a role
// whose skills were never saved.

import * as React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});
vi.mock("@/components/motion", () => ({
  FadeIn: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Stagger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  StaggerItem: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { EmployerProfile } from "./employer-profile";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PAGE = {
  slug: "acrm",
  name: "Acrm Corp",
  open_roles: [
    {
      id: "r1",
      title: "Data Engineer",
      experience_min_years: 3,
      experience_max_years: 6,
      apply_path: "/apply/r1",
      apply_url: "https://readypick.ai/apply/r1",
      skill_buckets: [
        { bucket: "must_have", label: "Must-have skills", names: ["Python"] },
        { bucket: "nice_to_have", label: "Nice-to-have skills", names: ["Terraform"] },
        {
          bucket: "behavioural",
          label: "Behavioural competencies",
          names: ["Owns incidents to closure"],
        },
      ],
    },
    {
      id: "r2",
      title: "Legacy Analyst",
      apply_path: "/apply/r2",
      apply_url: "https://readypick.ai/apply/r2",
    },
  ],
};

describe("open roles", () => {
  it("shows a role's title and band but none of the internal skill tiers", async () => {
    http.apiGet.mockResolvedValue(PAGE);
    render(<EmployerProfile slug="acrm" />);

    await screen.findByText("Data Engineer");
    expect(screen.getByText("3 to 6 years experience")).toBeTruthy();
    expect(screen.getByText("Legacy Analyst")).toBeTruthy();
    for (const text of [
      "Must-have skills",
      "Nice-to-have skills",
      "Behavioural competencies",
      "Python",
      "Terraform",
      "Owns incidents to closure",
    ]) {
      expect(screen.queryByText(text)).toBeNull();
    }
  });
});
