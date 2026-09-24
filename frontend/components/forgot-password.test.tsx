// @vitest-environment jsdom
//
// "Forgot password?" is Firebase's own reset email. What is pinned:
//
// * an address with no account reads EXACTLY like one with an account, so the
//   sign-in screen cannot be used to test which addresses are registered;
// * a failure the person can act on is said, and nothing fails silently;
// * a malformed address never reaches Firebase;
// * the control is inside the sign-in form without submitting it.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const firebaseAuthSdk = vi.hoisted(() => ({ sendPasswordResetEmail: vi.fn() }));
vi.mock("firebase/auth", () => firebaseAuthSdk);
vi.mock("@/lib/firebase", () => ({ firebaseAuth: { name: "test-auth" } }));

import {
  ForgotPassword,
  RESET_SENT_MESSAGE,
  requestPasswordReset,
} from "./forgot-password";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const fail = (code: string) => Object.assign(new Error(code), { code });

describe("requestPasswordReset", () => {
  it("answers the same whether or not the account exists", async () => {
    firebaseAuthSdk.sendPasswordResetEmail.mockResolvedValueOnce(undefined);
    const known = await requestPasswordReset("known@example.com");
    firebaseAuthSdk.sendPasswordResetEmail.mockRejectedValueOnce(fail("auth/user-not-found"));
    const unknown = await requestPasswordReset("nobody@example.com");
    expect(known).toEqual({ sent: true });
    expect(unknown).toEqual(known);
  });

  it("sends the trimmed address to Firebase's own reset", async () => {
    firebaseAuthSdk.sendPasswordResetEmail.mockResolvedValueOnce(undefined);
    await requestPasswordReset("  person@example.com ");
    expect(firebaseAuthSdk.sendPasswordResetEmail).toHaveBeenCalledWith(
      { name: "test-auth" },
      "person@example.com",
    );
  });

  it("never sends a malformed address", async () => {
    const outcome = await requestPasswordReset("not-an-address");
    expect(outcome.sent).toBe(false);
    expect(firebaseAuthSdk.sendPasswordResetEmail).not.toHaveBeenCalled();
  });

  it("says a failure the person can act on, and an unknown one too", async () => {
    firebaseAuthSdk.sendPasswordResetEmail.mockRejectedValueOnce(fail("auth/too-many-requests"));
    expect(await requestPasswordReset("a@example.com")).toEqual({
      sent: false,
      message: expect.stringMatching(/too many attempts/i),
    });
    firebaseAuthSdk.sendPasswordResetEmail.mockRejectedValueOnce(fail("auth/internal-error"));
    expect(await requestPasswordReset("a@example.com")).toEqual({
      sent: false,
      message: expect.stringMatching(/could not send/i),
    });
  });
});

describe("ForgotPassword", () => {
  it("prefills the address, sends inside a sign-in form without submitting it, and states the outcome", async () => {
    firebaseAuthSdk.sendPasswordResetEmail.mockResolvedValue(undefined);
    const outerSubmit = vi.fn((event: React.FormEvent) => event.preventDefault());
    render(
      <form onSubmit={outerSubmit}>
        <ForgotPassword initialEmail="person@example.com" idPrefix="login" />
      </form>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Forgot password?" }));
    const field = screen.getByLabelText(/email address for the reset link/i) as HTMLInputElement;
    expect(field.value).toBe("person@example.com");

    fireEvent.keyDown(field, { key: "Enter" });
    expect(await screen.findByText(RESET_SENT_MESSAGE)).toBeTruthy();
    expect(outerSubmit).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(firebaseAuthSdk.sendPasswordResetEmail).toHaveBeenCalledTimes(1),
    );
  });
});
