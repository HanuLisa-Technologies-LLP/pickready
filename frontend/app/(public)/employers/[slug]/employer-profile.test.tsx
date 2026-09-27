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
      skill_buckets: [],
    },
  ],
};

describe("open roles", () => {
  it("lists a role's skills by bucket name, and none for a legacy role", async () => {
    http.apiGet.mockResolvedValue(PAGE);
    render(<EmployerProfile slug="acrm" />);

    await screen.findByText("Data Engineer");
    expect(screen.getByText("3 to 6 years experience")).toBeTruthy();
    // Level four: the role title on the card is the level-three heading.
    const mustHave = screen.getByRole("region", { name: "Must-have skills" });
    expect(within(mustHave).getByRole("heading", { level: 4 })).toBeTruthy();
    expect(within(mustHave).getByText("Python")).toBeTruthy();
    expect(screen.getByText("Nice-to-have skills")).toBeTruthy();
    expect(screen.getByText("Behavioural competencies")).toBeTruthy();

    // Two roles, one set of skills: the legacy role renders no section.
    expect(screen.getByText("Legacy Analyst")).toBeTruthy();
    expect(screen.getAllByText("Must-have skills")).toHaveLength(1);
  });
});
