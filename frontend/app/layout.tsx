import type { Metadata, Viewport } from "next";
import { Fraunces, Inter_Tight, JetBrains_Mono } from "next/font/google";

import "./globals.css";
import { ChunkRecovery } from "@/components/chunk-recovery";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-provider";
import { ToastProvider } from "@/components/ui/toast";
import { SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "@/lib/site";

/**
 * Three faces, each with one job (DESIGN.md section 3).
 *
 * NOT default Inter. DESIGN.md states the reason plainly: default Inter is the
 * typeface a UI reaches for when nobody chose one, and Impeccable ships a
 * detector that flags it as a generated-interface tell. This file loaded plain
 * `Inter` and cited a DESIGN_BRIEF that DESIGN.md superseded, so the product
 * had been carrying the tell its own design authority forbids.
 *
 *  - Inter Tight is the working face. Tighter and more editorial than Inter,
 *    and it holds a dense recruiter table better at 13px, which is the size the
 *    candidate list actually renders at.
 *  - Fraunces is display only, where the product has one chance to look like
 *    somebody designed it. It is never used below a page title.
 *  - JetBrains Mono carries the COMPANY-JOB-CANDIDATE reference code, which has
 *    to be select-all and transcribable by eye. Twenty-five call sites already
 *    say `font-mono` and were resolving to whatever the browser had.
 *
 * All three are loaded through next/font, so they are self-hosted, preloaded,
 * and subset at build time. No network request reaches Google at runtime, which
 * also keeps them out of the CSP's font-src.
 */
const sans = Inter_Tight({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const display = Fraunces({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
  // Fraunces is variable along an optical-size axis. Pinning it high keeps the
  // lower-contrast, wider-aperture cut that stays legible at display sizes.
  axes: ["SOFT", "WONK", "opsz"],
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  /**
   * The canonical origin, stated once. Every relative `alternates.canonical`
   * and every relative Open Graph URL in the tree is resolved against this, so
   * a page declares its path and never its host. The canonical domain is
   * readypick.ai (RBAC section 15); picready.com and pickready.app are not the
   * product's address and both have appeared in this tree before.
   */
  metadataBase: new URL(SITE_URL),
  title: {
    default: "Vivekium, know every candidate before you meet them",
    template: "%s | Vivekium",
  },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "32x32" },
      { url: "/icon.png", type: "image/png", sizes: "512x512" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    url: "/",
    title: "Vivekium, know every candidate before you meet them",
    description:
      "Rank every applicant against the role, run a structured AI assessment, and read one clear report per candidate.",
    // No `images` entry here on purpose. `app/opengraph-image.tsx` generates
    // the card, and file-based metadata takes precedence over this object in
    // Next, so a path written here would either be ignored or would have to
    // name a file that does not exist in `public/`. There is no shipped og
    // asset, and inventing a path to one is how a card renders blank.
  },
  twitter: {
    card: "summary_large_image",
    title: "Vivekium, know every candidate before you meet them",
    description:
      "Rank every applicant against the role, run a structured AI assessment, and read one clear report per candidate.",
    // `twitter:image` is deliberately absent for the same reason: X falls back
    // to og:image, which the generated card supplies.
  },
};

export const viewport: Viewport = {
  themeColor: [
    // The browser chrome colour must match the canvas token, or the phone's
    // status bar paints a different product above the page. These two were the
    // pre-navy palette and had been stale since the recolour.
    { media: "(prefers-color-scheme: light)", color: "#FBFCFE" },
    { media: "(prefers-color-scheme: dark)", color: "#030C16" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${display.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <body className="min-h-screen font-sans antialiased">
        {/* Recovers a tab whose chunks were invalidated by a server restart. */}
        <ChunkRecovery />
        <ThemeProvider>
          <AuthProvider>
            <ToastProvider>{children}</ToastProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
