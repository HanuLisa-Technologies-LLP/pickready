"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export function OtpInput({
  length = 6,
  value,
  onChange,
  disabled,
  invalid,
  autoFocus,
  className,
}: {
  length?: number;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  /** Render the boxes in an error state (red border) — invalid/expired code. */
  invalid?: boolean;
  /** Focus the first empty box on mount. */
  autoFocus?: boolean;
  className?: string;
}) {
  const refs = React.useRef<(HTMLInputElement | null)[]>([]);

  const digits = Array.from({ length }, (_, i) => value[i] ?? "");

  // Auto-focus the first empty box once the component mounts.
  React.useEffect(() => {
    if (!autoFocus || disabled) return;
    const firstEmpty = Math.min(value.length, length - 1);
    refs.current[firstEmpty]?.focus();
    // Run only on mount — later focus moves are driven by typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setDigit = (index: number, digit: string) => {
    const next = digits.slice();
    next[index] = digit;
    onChange(next.join("").slice(0, length));
  };

  return (
    <div
      className={cn("flex gap-2", className)}
      role="group"
      aria-label={`${length}-digit one-time code`}
    >
      {digits.map((d, i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el;
          }}
          inputMode="numeric"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          pattern="[0-9]*"
          maxLength={1}
          disabled={disabled}
          value={d}
          aria-label={`Digit ${i + 1} of ${length}`}
          aria-invalid={invalid || undefined}
          className={cn(
            "h-12 w-10 rounded-md border bg-background text-center text-lg font-semibold ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
            invalid
              ? "border-red-500 focus-visible:ring-red-500"
              : "border-input"
          )}
          onChange={(e) => {
            const raw = e.target.value.replace(/\D/g, "");
            if (!raw) {
              setDigit(i, "");
              return;
            }
            if (raw.length > 1) {
              // Multiple digits landed in one box (autofill / paste into a box).
              const next = (value.slice(0, i) + raw).slice(0, length);
              onChange(next);
              const focusIdx = Math.min(next.length, length - 1);
              refs.current[focusIdx]?.focus();
              return;
            }
            setDigit(i, raw);
            if (i < length - 1) {
              refs.current[i + 1]?.focus();
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Backspace") {
              if (digits[i]) {
                // Clear the current box in place.
                setDigit(i, "");
              } else if (i > 0) {
                // Already empty — clear and step back to the previous box.
                setDigit(i - 1, "");
                refs.current[i - 1]?.focus();
              }
            } else if (e.key === "ArrowLeft" && i > 0) {
              e.preventDefault();
              refs.current[i - 1]?.focus();
            } else if (e.key === "ArrowRight" && i < length - 1) {
              e.preventDefault();
              refs.current[i + 1]?.focus();
            }
          }}
          onPaste={(e) => {
            e.preventDefault();
            const pasted = e.clipboardData
              .getData("text")
              .replace(/\D/g, "")
              .slice(0, length);
            if (pasted) {
              onChange(pasted);
              refs.current[Math.min(pasted.length, length - 1)]?.focus();
            }
          }}
        />
      ))}
    </div>
  );
}
