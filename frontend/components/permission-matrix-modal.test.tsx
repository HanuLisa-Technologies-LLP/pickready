// @vitest-environment jsdom
//
// Saving your OWN permission row refreshes your own tab (PR #5 item 2, ported).
//
// The reported symptom: a company's primary contact granted himself a
// capability through this modal, the modal said "Permissions saved", and the
// screen behind it kept rendering its read-only branch. The server only lets a
// manager grant what they hold, so the save proved he held it; what was stale
// was the capability list in his own tab, which nothing asked again.

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { StaffPermissions } from "@/lib/types";

const { apiGet, apiPatch, toast, refresh, auth } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  toast: vi.fn(),
  refresh: vi.fn(),
  auth: { userId: "self-id" },
}));

vi.mock("@/lib/api", () => ({ apiGet, apiPatch }));
// ONE toast function: the modal's fetch effect depends on it, as the real
// provider's stable callback, so a new function per render would re-fetch.
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { id: auth.userId }, refresh }),
}));
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <footer>{children}</footer>,
  DialogHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}));

import { PermissionMatrixModal } from "./permission-matrix-modal";

afterEach(() => {
  cleanup();
  apiGet.mockReset();
  apiPatch.mockReset();
  toast.mockReset();
  refresh.mockReset();
});

function row(userId: string): StaffPermissions {
  return {
    user_id: userId,
    role: "recruiter",
    full_name: "Asha Rao",
    email: "asha@example.com",
    all_capabilities: ["edit_company_profile"],
    role_defaults: [],
    overrides: { edit_company_profile: true },
    effective: ["edit_company_profile"],
    role_label: "Recruiter",
    grantable: ["edit_company_profile"],
  } as StaffPermissions;
}

async function saveFor(userId: string) {
  apiGet.mockResolvedValue(row(userId));
  apiPatch.mockResolvedValue(row(userId));
  const onOpenChange = vi.fn();
  render(<PermissionMatrixModal open userId={userId} onOpenChange={onOpenChange} />);
  await waitFor(() => expect(screen.getByText("Edit company profile")).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: /Save permissions/ }));
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
}

describe("saving the permission matrix", () => {
  it("refreshes the signed-in tab when the row saved is the signed-in person's", async () => {
    auth.userId = "self-id";
    await saveFor("self-id");
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: "Your own access is updated." }),
    );
  });

  it("leaves this tab alone for a colleague and says when they will see it", async () => {
    auth.userId = "self-id";
    await saveFor("colleague-id");
    expect(refresh).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({
        description: "Asha Rao sees this within a minute, on their next page.",
      }),
    );
  });
});
