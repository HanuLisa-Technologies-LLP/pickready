import type { Metadata } from "next";

/**
 * NOINDEX for the whole Provider Portal.
 *
 * The shell one level down (`app/(super-admin)/admin/layout.tsx`) is a Client
 * Component, so it cannot export `metadata` at all. This layout exists only to
 * carry that export: it is a Server Component that renders its children
 * unchanged, so it adds no markup, no wrapper and no behaviour, and the nested
 * client shell is untouched.
 *
 * This is the Vivekium owner's console over every customer. It is the one
 * cross-tenant surface in the product.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function SuperAdminGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
