import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";

import "./globals.css";
import { ChunkRecovery } from "@/components/chunk-recovery";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-provider";
import { ToastProvider } from "@/components/ui/toast";

/** Inter is the single typeface: display, body and UI (DESIGN_BRIEF Typography). */
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: {
    default: "PickReady, know every candidate before you meet them",
    template: "%s | PickReady",
  },
  description:
    "PickReady ranks every applicant against the role, runs a structured AI assessment, and hands your team one readable PPI Assessment Report per candidate.",
  applicationName: "PickReady",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "32x32" },
      { url: "/icon.png", type: "image/png", sizes: "512x512" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
  openGraph: {
    type: "website",
    siteName: "PickReady",
    title: "PickReady, know every candidate before you meet them",
    description:
      "Rank every applicant against the role, run a structured AI assessment, and read one clear report per candidate.",
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F7F7FB" },
    { media: "(prefers-color-scheme: dark)", color: "#06060F" },
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
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <body className={`${inter.className} min-h-screen antialiased`}>
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
