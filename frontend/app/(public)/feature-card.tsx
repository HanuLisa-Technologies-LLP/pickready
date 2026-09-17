import type { LucideIcon } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface FeatureCardProps {
  title: string;
  description: string;
  icon: LucideIcon;
}

/**
 * One capability, one card.
 *
 * NO ICON TILE ABOVE THE HEADING. DESIGN.md section 4 names the rounded square
 * tile specifically: it adds a shape without adding information, and stacked
 * above a title it pushes the words the card exists to deliver down the page.
 * The icon now sits ON the title line at 20px in `currentColor`, where it is a
 * marker for the heading rather than an ornament over it.
 */
export function FeatureCard({
  title,
  description,
  icon: Icon,
}: FeatureCardProps) {
  return (
    <Card className="h-full border-border shadow-none transition-colors duration-150 hover:border-field-hover">
      <CardHeader className="gap-0 pb-3">
        <CardTitle className="flex items-start gap-3 text-base leading-6">
          <Icon
            className="mt-px h-5 w-5 shrink-0 text-navy-600"
            strokeWidth={1.5}
            aria-hidden="true"
          />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-pretty text-sm leading-6">{description}</p>
      </CardContent>
    </Card>
  );
}
