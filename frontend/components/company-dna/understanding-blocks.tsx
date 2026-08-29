// The compiled artifact, in plain language.
//
// One renderer, used by the read-back inside the session and by the completed
// view afterwards. Two renderers would eventually disagree, and the failure
// would be a client confirming one wording and later reading a different one
// for the same artifact.
//
// Teal marks the client's OWN observable-evidence statements and the risk
// probes drawn from their failure modes. Teal is the evidence colour in this
// system and these blocks are the only place on the screen where the words are
// the client's own rather than ours.
//
// A teal-50 FILL, not a left rule. `border-l-4` is a generic tell the design
// gate refuses outside the two places the product has already argued for it,
// and a tinted surface carries the same "these words are not ours" boundary
// without borrowing that pattern.

import { cn } from "@/lib/utils";

import type { UnderstandingBlock } from "./types";

/** The blocks whose lines are the client's own words rather than our summary. */
const CLIENT_VOICE = new Set(["good", "risks", "context"]);

export function UnderstandingBlocks({
  blocks,
  className,
}: {
  blocks: UnderstandingBlock[];
  className?: string;
}) {
  return (
    <div className={cn("space-y-8", className)}>
      {blocks.map((block) => {
        const clientVoice = CLIENT_VOICE.has(block.key);
        return (
          <section key={block.key} aria-labelledby={`dna-${block.key}`}>
            <h3 id={`dna-${block.key}`} className="text-base font-semibold">
              {block.title}
            </h3>
            <ul
              className={cn(
                "mt-3 space-y-2 text-sm leading-6",
                clientVoice && "rounded-lg bg-teal-50 p-4"
              )}
            >
              {block.lines.map((line, index) => (
                <li key={`${block.key}-${index}`}>{line}</li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
