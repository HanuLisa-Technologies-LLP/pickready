import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Pressable, Reveal } from "@/components/motion";
import { Button } from "@/components/ui/button";
import { REQUEST_ACCESS_HREF } from "@/lib/site";

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
              className="type-section-title text-white"
            >
              Start with one role and see the reports
            </h2>
            {/* It promised nothing reaches a candidate until a person approves
                the wording, which stopped being true when the invitation
                email moved to a worker. What IS held for a person is the job
                itself: nothing is published until the team saves the JD and
                its skills. */}
            <p className="mx-auto mt-4 max-w-xl text-pretty text-base text-white">
              Ask for a workspace, post a job, and read what comes back.
              Nothing is published until someone on your team has saved the JD
              and its skills.
            </p>
            <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
              <Pressable>
                <Button
                  asChild
                  size="xl"
                  className="group bg-white text-navy-700 shadow-none hover:bg-navy-50"
                >
                  <a
                    href={REQUEST_ACCESS_HREF}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Request access
                    <ArrowRight
                      className="transition-transform duration-150 group-hover:translate-x-0.5"
                      aria-hidden="true"
                    />
                  </a>
                </Button>
              </Pressable>
              <Pressable>
                <Button
                  asChild
                  size="xl"
                  variant="outline"
                  className="border-white bg-transparent text-white shadow-none hover:bg-white/10 hover:text-white"
                >
                  <Link href="/login">Log in</Link>
                </Button>
              </Pressable>
            </div>
            <p className="mt-6 text-sm text-white">
              <Link
                href="/pricing"
                className="font-medium underline underline-offset-4 hover:text-navy-50"
              >
                See pricing plans
              </Link>
            </p>
          </div>
        </div>
      </Reveal>
    </section>
  );
}
