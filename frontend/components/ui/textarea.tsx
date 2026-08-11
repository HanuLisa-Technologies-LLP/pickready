import * as React from "react";

import { cn } from "@/lib/utils";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          // Matches Input state for state. It previously had no hover
          // affordance and a ring offset the inputs beside it did not use, so
          // a form with both in it rendered two different focus treatments.
          "flex min-h-[80px] w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm ring-offset-background transition-[border-color,box-shadow] duration-150 hover:border-field-hover focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:border-border disabled:opacity-50",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";

export { Textarea };
