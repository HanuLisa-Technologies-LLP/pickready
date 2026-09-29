// @vitest-environment jsdom
//
// Register your company (owner spec 2026-09-29, sections 2.2 and 5). Pinned:
// the page resumes at the step the SERVER reports; the details step spends a
// `company_register` security check proof and offers no Google; the first
// purchase goes through the onboarding routes (never a billing or
// subscription route) and success is only claimed after the server verifies
// the payment; the password step opens the workspace, and an address that
// already has a sign-in finishes with its existing password.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...http };
});
const firebaseSdk = vi.hoisted(() => ({ signInWithEmailAndPassword: vi.fn() }));
vi.mock("firebase/auth", () => firebaseSdk);
vi.mock("@/lib/firebase", () => ({ firebaseAuth: { name: "test-auth" } }));
const navigation = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: navigation.replace }) }));
const auth = vi.hoisted(() => ({ setSession: vi.fn() }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ setSession: auth.setSession }) }));
const checkout = vi.hoisted(() => ({ openOrderCheckout: vi.fn() }));
vi.mock("@/lib/razorpay", () => checkout);

import { ApiError } from "@/lib/api";
import { CompanyRegisterFlow } from "./company-register-flow";

const PACKS = {
  packs: [
    {
      slug: "standard_50", credits: 50, bonus_credits: 0, subtotal_inr: 30000,
      setup_fee_inr: 0, setup_fee_waived: true, gst_inr: 5400, total_inr: 35400,
      available: true, trial: false,
    },
  ],
  price_per_credit_inr: 600,
  gst_rate_percent: 18,
  min_custom_credits: 50,
  trial_used: false,
};

let stage: Record<string, unknown> = { stage: "details" };

function serve(overrides: Record<string, (body: unknown) => unknown> = {}) {
  http.apiGet.mockImplementation(async (path: string) => {
    if (path === "/company-onboarding/state") return stage;
    if (path === "/company-onboarding/pricing") return PACKS;
    throw new Error(`unexpected GET ${path}`);
  });
  http.apiPost.mockImplementation(async (path: string, body: unknown) => {
    if (overrides[path]) return overrides[path](body);
    if (path === "/auth/captcha/challenge") {
      return { challenge_id: "c1", image: "data:image/svg+xml;base64,PHN2Zy8+", expires_in: 300 };
    }
    if (path === "/auth/captcha/verify") return { captcha_proof: "proof-1" };
    if (path === "/company-onboarding/register") return { message: "sent" };
    if (path === "/company-onboarding/code/verify") {
      return { stage: "choose_pack", email: "founder@circa.com", company_name: "Circa Corp" };
    }
    if (path === "/company-onboarding/purchase") {
      return {
        purchase_id: "p1", razorpay_order_id: "order_1", razorpay_key_id: "rzp_test",
        total_inr: 35400, credits: 50, bonus_credits: 0, subtotal_inr: 30000,
        setup_fee_inr: 0, gst_inr: 5400,
      };
    }
    if (path === "/company-onboarding/purchase/verify") {
      return { stage: "set_password", email: "founder@circa.com", company_name: "Circa Corp" };
    }
    if (path === "/company-onboarding/activate") {
      return { user: { id: "u1", role: "client" }, capabilities: ["manage_billing"] };
    }
    throw new Error(`unexpected POST ${path}`);
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  stage = { stage: "details" };
});

function posted(): string[] {
  return http.apiPost.mock.calls.map((call) => call[0] as string);
}

describe("register your company", () => {
  it("sends the details with a company_register proof and offers no Google", async () => {
    serve();
    render(<CompanyRegisterFlow />);
    await screen.findByAltText(/security check characters/i);
    expect(screen.queryByRole("button", { name: /google/i })).toBeNull();
    expect(http.apiPost).toHaveBeenCalledWith("/auth/captcha/challenge", { purpose: "company_register" });

    fireEvent.change(screen.getByLabelText("First name"), { target: { value: "Saravan" } });
    fireEvent.change(screen.getByLabelText("Last name"), { target: { value: "Kumar" } });
    fireEvent.change(screen.getByLabelText("Company email"), { target: { value: "founder@circa.com" } });
    fireEvent.change(screen.getByLabelText("Mobile number"), { target: { value: "98765 43210" } });
    fireEvent.change(screen.getByLabelText("Company name"), { target: { value: "Circa Corp" } });
    fireEvent.change(screen.getByLabelText("Industry"), { target: { value: "Technology" } });
    fireEvent.change(screen.getByLabelText("Security check"), { target: { value: "k7m2px" } });
    fireEvent.click(screen.getByRole("button", { name: "Send security code" }));

    await screen.findByLabelText("Security code");
    expect(http.apiPost).toHaveBeenCalledWith("/company-onboarding/register", {
      first_name: "Saravan",
      last_name: "Kumar",
      email: "founder@circa.com",
      phone: "98765 43210",
      company_name: "Circa Corp",
      industry: "Technology",
      industry_other: null,
      captcha_proof: "proof-1",
    });
  });

  it("resumes at the step the server reports and verifies the code", async () => {
    stage = { stage: "verify_email", email: "founder@circa.com" };
    serve();
    render(<CompanyRegisterFlow />);
    fireEvent.change(await screen.findByLabelText("Security code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
    await screen.findByText("Choose your first credit pack");
    expect(http.apiPost).toHaveBeenCalledWith("/company-onboarding/code/verify", {
      email: "founder@circa.com",
      code: "123456",
    });
  });

  it("buys the first pack through the onboarding routes and waits for the server", async () => {
    stage = { stage: "choose_pack", email: "founder@circa.com", company_name: "Circa Corp" };
    serve();
    checkout.openOrderCheckout.mockImplementation(async (options) => {
      options.onSuccess({
        razorpay_order_id: "order_1",
        razorpay_payment_id: "pay_1",
        razorpay_signature: "sig",
      });
      return true;
    });
    render(<CompanyRegisterFlow />);
    fireEvent.click(await screen.findByRole("button", { name: /50\s*credits/i }));
    fireEvent.click(screen.getByRole("button", { name: "Proceed to Payment" }));
    await screen.findByText("Set your password");
    expect(http.apiPost).toHaveBeenCalledWith("/company-onboarding/purchase", { pack_slug: "standard_50" });
    expect(http.apiPost).toHaveBeenCalledWith("/company-onboarding/purchase/verify", {
      razorpay_order_id: "order_1",
      razorpay_payment_id: "pay_1",
      razorpay_signature: "sig",
    });
    expect(posted().filter((path) => path.startsWith("/billing") || /subscri/i.test(path))).toEqual([]);
  });

  it("sets the password and opens the workspace", async () => {
    stage = { stage: "set_password", email: "founder@circa.com" };
    serve();
    render(<CompanyRegisterFlow />);
    fireEvent.change(await screen.findByLabelText("Create a password"), { target: { value: "Circa-Corp-2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Set password and open workspace" }));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/org"));
    expect(http.apiPost).toHaveBeenCalledWith("/company-onboarding/activate", { password: "Circa-Corp-2026" });
    expect(auth.setSession).toHaveBeenCalled();
  });

  it("finishes with an existing sign-in when the address already has one", async () => {
    stage = { stage: "set_password", email: "founder@circa.com" };
    let activations = 0;
    serve({
      "/company-onboarding/activate": (body) => {
        activations += 1;
        if (activations === 1) {
          throw new ApiError(409, { detail: "This email address already has a ReadyPick sign-in." });
        }
        expect(body).toEqual({ id_token: "id-token" });
        return { user: { id: "u1", role: "client" }, capabilities: [] };
      },
    });
    firebaseSdk.signInWithEmailAndPassword.mockResolvedValue({
      user: { getIdToken: async () => "id-token" },
    });
    render(<CompanyRegisterFlow />);
    fireEvent.change(await screen.findByLabelText("Create a password"), { target: { value: "Circa-Corp-2026" } });
    fireEvent.click(screen.getByRole("button", { name: "Set password and open workspace" }));
    const existing = await screen.findByLabelText("Your existing password");
    fireEvent.change(existing, { target: { value: "Old-Password-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in and open workspace" }));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/org"));
    expect(firebaseSdk.signInWithEmailAndPassword).toHaveBeenCalledWith(
      { name: "test-auth" }, "founder@circa.com", "Old-Password-1",
    );
  });
});
