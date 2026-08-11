import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { DotPattern } from "@/components/magicui";
import { Pressable, Reveal } from "@/components/motion";
import { Button } from "@/components/ui/button";

export function CallToAction() {
  return (
    <section
      className="mx-auto max-w-6xl px-6 pb-24 lg:px-10"
      aria-labelledby="cta-title"
    >
      <Reveal>
        <div className="relative overflow-hidden rounded-2xl border border-border bg-brand-600 px-6 py-14 text-center shadow-pop sm:px-12">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0"
          >
            <DotPattern
              width={20}
              height={20}
              cr={1}
              className="text-white/25 [mask-image:radial-gradient(40rem_20rem_at_50%_50%,#000,transparent)]"
            />
          </div>

          <div className="relative mx-auto max-w-2xl">
            <h2
              id="cta-title"
              className="text-balance text-2xl font-bold text-white sm:text-3xl"
            >
              Start with one role and see the reports
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-pretty text-base leading-7 text-white/90">
              Create an account, post a job, and read what comes back. Nothing
              is sent to a candidate until someone on your team approves the
              wording.
            </p>
            <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
              <Pressable>
                <Button
                  asChild
                  size="xl"
                  className="group bg-white text-brand-700 shadow-none hover:bg-white/90"
                >
                  <Link href="/register?role=candidate">
                    Get started
                    <ArrowRight
                      className="transition-transform duration-150 group-hover:translate-x-0.5"
                      aria-hidden="true"
                    />
                  </Link>
                </Button>
              </Pressable>
              <Pressable>
                <Button
                  asChild
                  size="xl"
                  variant="outline"
                  className="border-white/40 bg-transparent text-white shadow-none hover:border-white hover:bg-white/10 hover:text-white"
                >
                  <Link href="/login?initial_context=all">Log in</Link>
                </Button>
              </Pressable>
            </div>
          </div>
        </div>
      </Reveal>
    </section>
  );
}
