import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Pressable, Reveal } from "@/components/motion";
import { Button } from "@/components/ui/button";

/**
 * The closing action.
 *
 * WHAT WAS TAKEN OUT: a masked dot field floating on a `shadow-pop` panel.
 * The hero already carries the one dot lattice on this page, so a second one
 * read as a motif rather than as structure, and a full-width block that is
 * flush with the page has nothing to float above. Navy is the structure
 * colour, so the panel itself is the emphasis and the white button is the one
 * thing on it that has to be seen.
 */
export function CallToAction() {
  return (
    <section
      className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-24"
      aria-labelledby="cta-title"
    >
      <Reveal>
        <div className="border border-navy-700 bg-navy-600 px-6 py-14 text-center sm:px-12">
          <div className="mx-auto max-w-2xl">
            <h2
              id="cta-title"
              className="text-balance text-2xl font-bold tracking-[-0.015em] text-white sm:text-3xl"
            >
              Start with one role and see the reports
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-pretty text-base leading-7 text-white">
              Create an account, post a job, and read what comes back. Nothing
              is sent to a candidate until someone on your team approves the
              wording.
            </p>
            <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
              <Pressable>
                <Button
                  asChild
                  size="xl"
                  className="group bg-white text-navy-700 shadow-none hover:bg-navy-50"
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
                  className="border-white bg-transparent text-white shadow-none hover:bg-white/10 hover:text-white"
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
