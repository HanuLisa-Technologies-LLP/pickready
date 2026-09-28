import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * The type ROLES `tailwind.config.ts` adds to the font-size scale.
 *
 * tailwind-merge only knows Tailwind's default size names. Any other
 * `text-<name>` it files under TEXT COLOUR, so without this list
 * `cn("text-label", "text-ink")` would keep only `text-ink` and the label
 * would silently lose its size, and `cn("text-body", "text-sm")` would keep
 * both. Keep it equal to the role keys in the config; `lib/utils.test.ts`
 * reads the config and fails when the two drift.
 */
export const TYPE_ROLES = [
  "title",
  "title-sm",
  "heading",
  "subheading",
  "body",
  "body-sm",
  "label",
  "meta",
  "eyebrow",
  "table",
  "table-head",
  "chip",
  "ref",
  "display",
  "display-md",
  "display-sm",
  "section",
  "section-md",
  "section-sm",
  "lead",
  "lead-sm",
  "prose-lg",
] as const;

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: [...TYPE_ROLES] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
