import { getApp, getApps, initializeApp } from "firebase/app";
import {
  inMemoryPersistence,
  browserPopupRedirectResolver,
  getAuth,
  GoogleAuthProvider,
  initializeAuth,
  type Auth,
} from "firebase/auth";

// The Web API key below is a PUBLIC client identifier, not a secret: Firebase
// requires it in the browser and shows it in the auth helper URL
// (`<authDomain>/__/auth/handler?apiKey=...`) inside the sign-in POPUP. Do not
// move it server-side; abuse protection is the authorized-domains list and API
// key referrer restrictions, both console-managed. To take the vendor domain
// out of that popup URL, set NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN to the site's
// own host: next.config.js proxies /__/auth/* to Firebase for exactly that.
const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

export const firebaseApp = getApps().length ? getApp() : initializeApp(config);

// Initialize Auth ONCE with the popup/redirect resolver explicitly bound. Using
// getAuth() (lazy resolver) together with React StrictMode + Next.js Fast Refresh
// is the known trigger for the SDK assertion
// "INTERNAL ASSERTION FAILED: Pending promise was never set" during
// signInWithPopup, the popup event arrives before a resolver is registered.
// Binding browserPopupRedirectResolver at init fixes it.
//
// SSR-safe: on the server we must not touch window/indexedDB, so fall back to the
// lazy getAuth there (never used for a popup on the server). initializeAuth throws
// if called twice (HMR re-eval), reuse the existing instance in that case.
function resolveAuth(): Auth {
  if (typeof window === "undefined") {
    return getAuth(firebaseApp);
  }
  try {
    return initializeAuth(firebaseApp, {
      persistence: inMemoryPersistence,
      popupRedirectResolver: browserPopupRedirectResolver,
    });
  } catch {
    // Already initialized on a previous module evaluation (Fast Refresh / a
    // second import), reuse the single existing instance.
    return getAuth(firebaseApp);
  }
}

export const firebaseAuth = resolveAuth();

/** Always show the account picker for a candidate Google sign-in. */
export function createCandidateGoogleProvider(): GoogleAuthProvider {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  return provider;
}
