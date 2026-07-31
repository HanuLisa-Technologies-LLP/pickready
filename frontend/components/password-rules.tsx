import * as React from "react";
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The password requirement checklist, shared by Create account and Join a team
 * so both screens state the same rules in the same words.
 *
 * A met rule is a filled tick in the top-band green from the rating ramp; an
 * unmet one is an empty outline. The difference is shape and weight, never a
 * dimmed grey label, and each item carries a screen-reader suffix so the state
 * is not colour-only.
 */

export interface PasswordRuleState {
  length: boolean;
  lower: boolean;
  upper: boolean;
  number: boolean;
}

export function passwordRules(password: string): PasswordRuleState {
  return {
    length: password.length >= 8,
    lower: /[a-z]/.test(password),
    upper: /[A-Z]/.test(password),
    number: /\d/.test(password),
  };
}

export function isPasswordValid(rules: PasswordRuleState) {
  return Object.values(rules).every(Boolean);
}

export function PasswordRules({
  rules,
  id,
  className,
}: {
  rules: PasswordRuleState;
  id?: string;
  className?: string;
}) {
  return (
    <ul id={id} className={cn("grid grid-cols-2 gap-1.5 pt-1 text-xs", className)}>
      <Rule ok={rules.length}>8 or more characters</Rule>
      <Rule ok={rules.upper}>Uppercase</Rule>
      <Rule ok={rules.lower}>Lowercase</Rule>
      <Rule ok={rules.number}>Number</Rule>
    </ul>
  );
}

function Rule({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className={cn(
          "grid h-4 w-4 shrink-0 place-items-center rounded-full border transition-colors duration-150",
          ok
            ? "border-rating-1 bg-rating-1-bg text-rating-1"
            : "border-border bg-transparent"
        )}
      >
        {ok ? <Check className="h-2.5 w-2.5" strokeWidth={3} /> : null}
      </span>
      <span className={ok ? "font-medium" : undefined}>{children}</span>
      <span className="sr-only">{ok ? " met" : " not met yet"}</span>
    </li>
  );
}
