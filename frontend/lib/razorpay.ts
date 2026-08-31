"use client";

/**
 * Razorpay Checkout, loaded on demand (killer-spec §2.4).
 *
 * The script is NOT in the document head. It is ~90 KB of third-party
 * JavaScript that only matters to someone who has decided to subscribe, so
 * putting it on every page load would tax the landing page for everybody to
 * serve the few who click Subscribe. `loadCheckout()` injects it on first use
 * and every later call reuses the same promise.
 *
 * The Key ID comes from GET /billing/config at runtime, never from a build-time
 * NEXT_PUBLIC_ variable, so there is one source of truth and the frontend never
 * needs the .env file. The Key SECRET has no path into this file: the signature
 * that proves a payment is verified server-side by POST /billing/checkout/verify.
 */

const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

let loader: Promise<boolean> | null = null;

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void };
  }
}

/** Inject the Checkout script once. Resolves false if it cannot be loaded. */
export function loadCheckout(): Promise<boolean> {
  if (typeof window === "undefined") return Promise.resolve(false);
  if (window.Razorpay) return Promise.resolve(true);
  if (loader) return loader;

  loader = new Promise<boolean>((resolve) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${CHECKOUT_SRC}"]`
    );
    if (existing) {
      existing.addEventListener("load", () => resolve(Boolean(window.Razorpay)));
      existing.addEventListener("error", () => resolve(false));
      return;
    }
    const script = document.createElement("script");
    script.src = CHECKOUT_SRC;
    script.async = true;
    script.onload = () => resolve(Boolean(window.Razorpay));
    script.onerror = () => {
      // Reset so a later attempt can retry: an ad blocker or a flaky network
      // must not permanently disable checkout for the session.
      loader = null;
      resolve(false);
    };
    document.head.appendChild(script);
  });
  return loader;
}

export interface CheckoutHandlerPayload {
  razorpay_payment_id: string;
  razorpay_subscription_id: string;
  razorpay_signature: string;
}

export interface OpenCheckoutOptions {
  keyId: string;
  subscriptionId: string;
  planName: string;
  /** Prefills the Checkout form. All optional. */
  prefill?: { name?: string; email?: string; contact?: string };
  onSuccess: (payload: CheckoutHandlerPayload) => void;
  onDismiss?: () => void;
}

/**
 * Open Razorpay Checkout for a subscription.
 *
 * Returns false when the script could not be loaded, so the caller can fall
 * back to the subscription's hosted `short_url` rather than leaving the user
 * looking at a button that silently does nothing.
 */
export async function openCheckout(options: OpenCheckoutOptions): Promise<boolean> {
  const ready = await loadCheckout();
  if (!ready || !window.Razorpay) return false;

  const checkout = new window.Razorpay({
    key: options.keyId,
    subscription_id: options.subscriptionId,
    name: "ReadyPick",
    description: `${options.planName} plan, billed monthly`,
    prefill: options.prefill ?? {},
    theme: { color: "#0A2540" },
    handler: (response: CheckoutHandlerPayload) => options.onSuccess(response),
    modal: { ondismiss: () => options.onDismiss?.() },
  });
  checkout.open();
  return true;
}

/** What Razorpay hands back for a one-time ORDER payment. The field names are
 *  Razorpay's own and travel unchanged into POST /billing/purchase/verify,
 *  where the server recomputes the signature; nothing here proves payment. */
export interface OrderCheckoutHandlerPayload {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

export interface OpenOrderCheckoutOptions {
  keyId: string;
  orderId: string;
  /** Total payable in RUPEES; converted to paise at the Razorpay boundary. */
  amountInr: number;
  name: string;
  description: string;
  /** Prefills the Checkout form. All optional. */
  prefill?: { name?: string; email?: string; contact?: string };
  onSuccess: (payload: OrderCheckoutHandlerPayload) => void;
  onDismiss?: () => void;
}

/**
 * Open Razorpay Checkout for a one-time ORDER: a credit pack purchase
 * (directive Part 5 section 3.3), as opposed to the recurring subscription
 * `openCheckout` above starts. Same script loader, same contract: resolves
 * false when Checkout could not load, so the caller can tell the user instead
 * of leaving a button that silently does nothing.
 */
export async function openOrderCheckout(
  options: OpenOrderCheckoutOptions
): Promise<boolean> {
  const ready = await loadCheckout();
  if (!ready || !window.Razorpay) return false;

  const checkout = new window.Razorpay({
    key: options.keyId,
    order_id: options.orderId,
    // Razorpay speaks paise; the billing API contract speaks whole rupees.
    amount: Math.round(options.amountInr * 100),
    currency: "INR",
    name: options.name,
    description: options.description,
    prefill: options.prefill ?? {},
    theme: { color: "#0A2540" },
    handler: (response: OrderCheckoutHandlerPayload) => options.onSuccess(response),
    modal: { ondismiss: () => options.onDismiss?.() },
  });
  checkout.open();
  return true;
}
