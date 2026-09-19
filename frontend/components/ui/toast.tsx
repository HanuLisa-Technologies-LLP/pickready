"use client";

// Lightweight sonner-style toast: context provider + hook + viewport.

import * as React from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export type ToastVariant = "default" | "destructive";

export interface ToastItem {
  id: number;
  title: string;
  description?: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (opts: {
    title: string;
    description?: string;
    variant?: ToastVariant;
  }) => void;
  dismiss: (id: number) => void;
  toasts: ToastItem[];
}

const ToastContext = React.createContext<ToastContextValue>({
  toast: () => {},
  dismiss: () => {},
  toasts: [],
});

let nextId = 1;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<ToastItem[]>([]);

  const dismiss = React.useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = React.useCallback(
    (opts: { title: string; description?: string; variant?: ToastVariant }) => {
      const id = nextId++;
      setToasts((prev) => [
        ...prev,
        {
          id,
          title: opts.title,
          description: opts.description,
          variant: opts.variant ?? "default",
        },
      ]);
      window.setTimeout(() => dismiss(id), 5000);
    },
    [dismiss]
  );

  return (
    <ToastContext.Provider value={{ toast, dismiss, toasts }}>
      {children}
      <ToastViewport />
    </ToastContext.Provider>
  );
}

export function useToast() {
  return React.useContext(ToastContext);
}

function ToastViewport() {
  const { toasts, dismiss } = React.useContext(ToastContext);
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          // A failure interrupts, a confirmation does not. `status` is polite
          // and waits for the reader to finish whatever it is saying, which is
          // right for "Profile saved" and wrong for "Copy failed": by the time
          // it is read the user has moved on believing the action worked.
          role={t.variant === "destructive" ? "alert" : "status"}
          className={cn(
            "pointer-events-auto relative flex w-full items-start gap-3 rounded-md border p-4 shadow-modal animate-in slide-in-from-bottom-2",
            t.variant === "destructive"
              ? "border-destructive bg-destructive text-destructive-foreground"
              : "border-border bg-background text-foreground"
          )}
        >
          <div className="flex-1 space-y-1">
            <p className="text-sm font-semibold leading-none">{t.title}</p>
            {/* House rule: hierarchy from weight, never from a dimmed text
                colour. The title above is semibold, so this needs no opacity
                to read as the secondary line. */}
            {t.description ? <p className="text-sm">{t.description}</p> : null}
          </div>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            // This was the one control in the product a keyboard user could
            // reach and not see: no ring, and the only affordance was an
            // opacity change on hover, which a Tab never triggers.
            // `ring-current` rather than `ring-ring`: this button sits on the
            // page surface on a normal toast and on solid red on a destructive
            // one, and the current text colour is the one value already chosen
            // to contrast with whichever it is.
            className="rounded-sm transition-opacity hover:opacity-70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"
            aria-label="Dismiss notification"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ))}
    </div>
  );
}
