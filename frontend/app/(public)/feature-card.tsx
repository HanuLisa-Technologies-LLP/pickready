import type { LucideIcon } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface FeatureCardProps {
  title: string;
  description: string;
  icon: LucideIcon;
}

export function FeatureCard({
  title,
  description,
  icon: Icon,
}: FeatureCardProps) {
  return (
    <Card className="h-full transition-shadow duration-150 hover:shadow-card-hover">
      <CardHeader className="gap-4">
        <span className="grid h-10 w-10 place-items-center rounded-lg bg-brand-100 text-accent-foreground">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </span>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-pretty text-sm leading-6">{description}</p>
      </CardContent>
    </Card>
  );
}
