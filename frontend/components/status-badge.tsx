import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, string> = {
  // pipeline
  shortlisted: "bg-foreground text-background border-transparent",
  offered: "bg-muted-foreground/80 text-background border-transparent",
  joined: "bg-foreground text-background border-transparent",
  hold: "bg-muted text-foreground border-border",
  rejected: "bg-transparent text-muted-foreground border-border line-through",
  pending: "bg-transparent text-muted-foreground border-border border-dashed",
  // job approval FSM
  draft: "bg-transparent text-muted-foreground border-border border-dashed",
  requested: "bg-muted text-foreground border-border",
  recommended: "bg-muted text-foreground border-border",
  approved: "bg-muted-foreground/80 text-background border-transparent",
  ratified: "bg-foreground text-background border-transparent",
  // verification
  sent: "bg-muted text-foreground border-border",
  completed: "bg-foreground text-background border-transparent",
  overridden: "bg-muted-foreground/80 text-background border-transparent",
  failed: "bg-transparent text-muted-foreground border-border",
};

export function StatusBadge({
  status,
  className,
}: {
  status: string | null | undefined;
  className?: string;
}) {
  const s = (status ?? "pending").toLowerCase();
  const style = STATUS_STYLES[s] ?? "bg-muted text-foreground border-border";
  const label = s
    .split(/[_-]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
  return <Badge className={cn(style, className)}>{label}</Badge>;
}
