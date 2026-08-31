import { cn } from "@/lib/utils";

/**
 * The STEM / Non-STEM role type badge (Master Directive Part 3 §7.1).
 *
 * READ-ONLY, always. The classification is system-determined from the raw
 * AI-generated JD and the client can see it but never edit it (Rule 2, Rule
 * 5), so this component deliberately renders no control, takes no onChange,
 * and offers nothing to click. The colours are the directive's own: teal
 * ground for STEM, navy for Non-STEM, white text on both.
 *
 * Sharp rectangle, no pill, Part 1 §7.
 */
export function RoleTypeBadge({
  classification,
  creditCost,
  className,
}: {
  classification?: string | null;
  creditCost?: number | null;
  className?: string;
}) {
  const stem = classification === "STEM";
  const cost = creditCost ?? (stem ? 1.5 : 1.0);
  const costLine = `${cost.toFixed(1)} credit${cost === 1 ? "" : "s"} per ReadyPick Intelligence Report`;
  return (
    <div className={cn("flex flex-col items-end gap-1", className)}>
      <span
        title={
          stem
            ? "This role requires technical AI assessment. Credit consumption is 1.5 per completed report."
            : "Standard assessment. Credit consumption is 1.0 per completed report."
        }
        // Pinned hexes, not theme tokens: the ramps invert in dark mode and a
        // light-teal chip with white text would fail AA there. The navy is the
        // directive's own anchor; the teal is the brand ramp's text-safe step
        // (#0D9488 itself measures 3.65:1 against white, below AA for text
        // this size, so the chip uses the darker step of the same hue).
        className={cn(
          "inline-flex items-center rounded-none px-2.5 py-0.5 text-xs font-semibold text-white",
          stem ? "bg-[#096C62]" : "bg-[#0A2540]",
        )}
      >
        {stem ? "STEM Role" : "Non-STEM Role"}
      </span>
      <span className="text-2xs opacity-70">{costLine}</span>
    </div>
  );
}
