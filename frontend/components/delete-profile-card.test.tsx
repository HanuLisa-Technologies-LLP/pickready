// @vitest-environment jsdom
//
// Delete My Profile, after the server has answered. What is pinned:
//
// * the Firebase client is signed out BEFORE the page leaves, so no screen can
//   offer to continue as an account that no longer exists;
// * when the server KEPT the sign-in identity (the address is also a staff
//   sign-in), its sentence is shown and nothing leaves until the person has
//   read it.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const firebase = vi.hoisted(() => ({ signOut: vi.fn() }));
vi.mock("@/lib/firebase", () => ({ firebaseAuth: firebase }));
const http = vi.hoisted(() => ({ api: vi.fn(), apiGet: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});
const toast = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ toast }) }));

import { DeleteProfileCard } from "./delete-profile-card";

const NOTICE = {
  heading: "Delete my profile",
  warnings: ["Everything is removed."],
  confirmation_phrase: "DELETE",
  instruction: "Type DELETE to confirm",
};

const location = { href: "/portal/profile" };
const realLocation = window.location;

beforeEach(() => {
  Object.defineProperty(window, "location", { value: location, writable: true });
  location.href = "/portal/profile";
  http.apiGet.mockResolvedValue(NOTICE);
  firebase.signOut.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  Object.defineProperty(window, "location", { value: realLocation, writable: true });
});

async function confirmDeletion() {
  render(<DeleteProfileCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Delete my profile" }));
  fireEvent.change(await screen.findByLabelText("Type DELETE to confirm"), {
    target: { value: "DELETE" },
  });
  fireEvent.click(screen.getByRole("button", { name: /delete permanently/i }));
}

describe("after the server deletes the profile", () => {
  it("signs the Firebase client out, then leaves", async () => {
    http.api.mockResolvedValue({
      deleted: true,
      sign_in_identity_deleted: true,
      sign_in_identity_note: null,
    });
    await confirmDeletion();
    await waitFor(() => expect(location.href).toBe("/login"));
    expect(firebase.signOut).toHaveBeenCalledTimes(1);
    expect(http.api).toHaveBeenCalledWith("/portal/me", {
      method: "DELETE",
      body: { confirmation: "DELETE" },
    });
  });

  it("shows the server's sentence when it kept the sign-in, and leaves only on Continue", async () => {
    http.api.mockResolvedValue({
      deleted: true,
      sign_in_identity_deleted: false,
      sign_in_identity_note:
        "Your profile is deleted. This email address also signs in to a company account, so that sign-in was kept.",
    });
    await confirmDeletion();
    expect(await screen.findByText(/that sign-in was kept/i)).toBeTruthy();
    expect(location.href).toBe("/portal/profile");
    expect(firebase.signOut).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(location.href).toBe("/login"));
    expect(firebase.signOut).toHaveBeenCalledTimes(1);
  });
});
