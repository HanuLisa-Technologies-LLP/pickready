import * as React from "react";

import { cn } from "@/lib/utils";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          // Three states, each a step up in saturation: `border-input`
          // resolves to the idle field token (visible at rest, 3.2:1 on the
          // page canvas), hover moves to the deeper token, focus lands on the
          // brand and adds the ring. See globals.css for why this is a token
          // rather than a per-call-site class.
          "flex h-10 w-full rounded-lg border border-input bg-surface px-3 py-2 text-sm ring-offset-background transition-[border-color,box-shadow] duration-150 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground hover:border-field-hover focus-visible:border-brand-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:border-border disabled:opacity-50",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";

export { Input };
