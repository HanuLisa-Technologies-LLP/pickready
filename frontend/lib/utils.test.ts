import { describe, expect, it } from "vitest";

import config from "@/tailwind.config";
import { TYPE_ROLES, cn } from "@/lib/utils";

/**
 * The type roles live in two places: `tailwind.config.ts` generates them and
 * `lib/utils.ts` teaches tailwind-merge that they are SIZES. If a role is
 * added to the config and not to the merge list, `cn()` files it as a text
 * colour and the next colour class on the same element deletes it, with no
 * error anywhere. These tests make that drift fail loudly.
 */

const STEPS = ["2xs", "xs", "sm", "base", "lg", "xl", "2xl", "3xl", "4xl", "5xl"];

function configuredRoles(): string[] {
  const sizes = (config.theme?.extend?.fontSize ?? {}) as Record<string, unknown>;
  return Object.keys(sizes).filter((key) => !STEPS.includes(key));
}

describe("type roles", () => {
  it("tailwind-merge knows every role the config generates, and no other", () => {
    expect([...TYPE_ROLES].sort()).toEqual(configuredRoles().sort());
  });

  it("a role survives a colour class on the same element", () => {
    for (const role of TYPE_ROLES) {
      expect(cn(`text-${role}`, "text-ink")).toBe(`text-${role} text-ink`);
      expect(cn(`text-${role}`, "text-teal-700")).toBe(`text-${role} text-teal-700`);
    }
  });

  it("a later size replaces an earlier role, and the other way round", () => {
    expect(cn("text-subheading", "text-base")).toBe("text-base");
    expect(cn("text-sm", "text-body")).toBe("text-body");
    expect(cn("text-table", "text-xs")).toBe("text-xs");
  });

  it("every role states a line height, so no role inherits `leading-normal`", () => {
    const sizes = (config.theme?.extend?.fontSize ?? {}) as Record<
      string,
      [string, { lineHeight?: string }]
    >;
    for (const role of TYPE_ROLES) {
      expect(sizes[role]?.[1]?.lineHeight, role).toBeTruthy();
    }
  });
});
