import Link from "next/link";

export function Hero() {
  return (
    <section
      className="grid min-h-[calc(100vh-4rem)] items-stretch border-b lg:grid-cols-2"
      aria-labelledby="landing-title"
    >
      <div
        className="relative min-h-64 overflow-hidden border-b bg-muted lg:min-h-0 lg:border-b-0 lg:border-r"
        aria-hidden="true"
      >
        <div className="absolute inset-0 bg-[linear-gradient(135deg,hsl(var(--foreground)/0.08)_1px,transparent_1px),linear-gradient(45deg,hsl(var(--foreground)/0.05)_1px,transparent_1px)] bg-[size:2rem_2rem]" />
        <div className="absolute inset-[15%] border border-foreground/20" />
        <div className="absolute inset-[28%] border border-foreground/30" />
        <div className="absolute inset-[41%] bg-foreground" />
      </div>
      <div className="flex items-center px-4 py-16 sm:px-8 lg:px-16 xl:px-24">
        <div className="max-w-xl">
          <p className="mb-4 text-sm font-medium uppercase tracking-[0.2em] text-muted-foreground">
            Recruitment operations, clarified
          </p>
          <h1 id="landing-title" className="text-4xl font-bold tracking-tight sm:text-5xl">
            PickReady — Recruitment Redefined
          </h1>
          <p className="mt-6 text-lg leading-8 text-muted-foreground">
            Match the right people from your databank, validate candidate details,
            and move every role through accountable approvals in one workspace.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/register?role=candidate"
              className="inline-flex h-11 items-center justify-center rounded-md bg-primary px-8 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              Sign Up as Candidate
            </Link>
            <Link
              href="/login?initial_context=all"
              className="inline-flex h-11 items-center justify-center rounded-md border border-input bg-background px-8 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              Log In
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
