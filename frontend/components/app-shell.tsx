"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Building2, LogOut, Menu, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";
import { Logo } from "@/components/brand";
import { WorkspaceContentBoundary } from "@/components/workspace-boundary";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

export interface NavItem {
  href: string;
  label: string;
  icon?: LucideIcon;
  exact?: boolean;
}

function isActive(pathname: string, item: NavItem) {
  return item.exact
    ? pathname === item.href
    : pathname === item.href || pathname.startsWith(item.href + "/");
}

/**
 * One navigation link, shared by the desktop rail and the mobile sheet.
 *
 * The active item is a solid brand pill; every other item is plain ink with a
 * brand-tinted hover. Nothing here is grey: an inactive item differs from an
 * active one by background and weight, never by a dimmed text colour.
 */
function NavLink({
  item,
  active,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        active
          ? "bg-brand-600 font-semibold text-white shadow-brand"
          : "font-medium hover:bg-brand-100/70 hover:text-accent-foreground"
      )}
    >
      {Icon ? <Icon className="h-4 w-4 shrink-0" aria-hidden="true" /> : null}
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

function AccountBlock({ compact = false }: { compact?: boolean }) {
  const { user, logout } = useAuth();
  return (
    <div className={cn("space-y-3", compact ? "p-4" : "p-4")}>
      {user ? (
        <>
          <div className="flex items-center gap-3 rounded-lg bg-secondary px-3 py-2.5">
            <span
              aria-hidden="true"
              className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white"
            >
              {(user.full_name || user.email || "?").slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold">
                {user.full_name || user.email}
              </p>
              <p className="truncate text-xs opacity-80">{user.email}</p>
            </div>
          </div>
          <WorkspaceSwitcher />
        </>
      ) : null}
      <Button
        variant="outline"
        size="sm"
        className="w-full justify-start gap-2"
        onClick={() => void logout()}
      >
        <LogOut className="h-4 w-4" aria-hidden="true" /> Sign out
      </Button>
    </div>
  );
}

/**
 * The signed-in application shell: Customer Portal, Candidate Portal and the
 * Provider Portal all render inside this.
 *
 * Layout is a fixed 264px rail on `md` and up, and a slide-over sheet below it,
 * so the shell is usable from 360px without the content ever scrolling
 * sideways. Glass is used on the mobile top bar only, per DESIGN_BRIEF rule 7,
 * and never behind a form or a table.
 */
export function AppShell({
  title,
  nav,
  children,
}: {
  title: string;
  nav: NavItem[];
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    if (!loading && !user) {
      // Remember where they were. This used to be a bare `/login`, which is
      // what made an emailed assessment link land the candidate on the jobs
      // board after signing in: they had been sent to the right page, the
      // shell bounced them, and the destination was thrown away on the way
      // out. `finish()` in the login flow only honours a same-origin path, so
      // this cannot be turned into an open redirect.
      const here =
        typeof window === "undefined"
          ? pathname
          : window.location.pathname + window.location.search;
      router.replace(`/login?next=${encodeURIComponent(here)}`);
    }
  }, [loading, user, router, pathname]);

  // Close the mobile sheet whenever the route changes, otherwise it stays open
  // over the page the user just navigated to.
  React.useEffect(() => {
    setOpen(false);
  }, [pathname]);

  const railNav = (onNavigate?: () => void) => (
    <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label={title}>
      {nav.map((item) => (
        <NavLink
          key={item.href}
          item={item}
          active={isActive(pathname, item)}
          onNavigate={onNavigate}
        />
      ))}
    </nav>
  );

  return (
    <div className="flex min-h-screen bg-canvas text-foreground">
      <aside className="fixed inset-y-0 left-0 hidden w-[264px] shrink-0 flex-col border-r border-border bg-surface md:flex">
        <div className="px-5 py-6">
          <Logo variant="full" height={34} href="/" />
          <p className="mt-3 text-xs font-medium uppercase tracking-[0.12em] opacity-70">
            {title}
          </p>
          {user ? (
            <div
              className="mt-3 flex items-center gap-2 rounded-lg border border-brand-600/20 bg-brand-100/60 px-3 py-2"
              data-active-workspace={user.workspace_name}
            >
              <Building2 className="h-4 w-4 shrink-0 text-brand-600" aria-hidden="true" />
              <div className="min-w-0">
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em]">
                  Active workspace
                </p>
                <p className="truncate text-sm font-bold">{user.workspace_name}</p>
              </div>
            </div>
          ) : null}
        </div>
        <Separator />
        {railNav()}
        <Separator />
        <AccountBlock />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col md:pl-[264px]">
        <header className="glass sticky top-0 z-40 flex h-16 items-center gap-3 border-b border-border px-4 md:hidden">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon" aria-label="Open menu">
                <Menu className="h-5 w-5" aria-hidden="true" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="flex w-[280px] flex-col p-0">
              <SheetTitle className="sr-only">{title}</SheetTitle>
              <div className="px-5 py-6">
                <Logo variant="full" height={32} />
                <p className="mt-3 text-xs font-medium uppercase tracking-[0.12em] opacity-70">
                  {title}
                </p>
              </div>
              <Separator />
              {railNav(() => setOpen(false))}
              <Separator />
              <AccountBlock compact />
            </SheetContent>
          </Sheet>
          <Logo variant="mark" height={32} href="/" />
          <span className="min-w-0 truncate text-sm font-semibold">
            {user?.workspace_name ?? title}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto"
            aria-label="Sign out"
            onClick={() => void logout()}
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
          </Button>
        </header>

        <main className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-6 md:px-10 md:py-8">
          <WorkspaceContentBoundary user={user}>
            {loading && !user ? <ShellSkeleton /> : children}
          </WorkspaceContentBoundary>
        </main>
      </div>
    </div>
  );
}

function ShellSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className="space-y-6">
      <Skeleton className="h-9 w-56 rounded-lg" />
      <Skeleton className="h-4 w-80 rounded" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
        <Skeleton className="h-28 rounded-xl" />
      </div>
      <Skeleton className="h-64 w-full rounded-xl" />
      <span className="sr-only">Loading</span>
    </div>
  );
}

/**
 * The one page title block. Every screen in every portal opens with this, so
 * the eye lands in the same place on every route.
 *
 * `eyebrow` is the small brand-coloured kicker above the title (it is the only
 * place a non-ink text colour is allowed, and it carries no information a
 * colour-blind reader would lose). `description` is one short line about the
 * task, never a paragraph explaining how the feature is implemented.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  className,
}: {
  eyebrow?: string;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mb-8 flex flex-wrap items-start justify-between gap-4 border-b border-border pb-6",
        className
      )}
    >
      <div className="min-w-0">
        {eyebrow ? (
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brand-600">
            {eyebrow}
          </p>
        ) : null}
        <h1
          className={cn(
            "text-balance text-xl font-bold tracking-tight sm:text-2xl",
            eyebrow && "mt-2"
          )}
        >
          {title}
        </h1>
        {description ? (
          <p className="mt-2 max-w-2xl text-pretty text-sm leading-6">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
