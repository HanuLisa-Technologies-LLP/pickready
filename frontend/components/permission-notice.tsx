"use client";

/**
 * The one component that renders a read-only restriction, and the reason
 * there is only one.
 *
 * The spec's rule (section 7) is narrow and absolute: this sentence appears
 * when, and only when, the user can view a resource and genuinely cannot edit
 * it. Every other combination is a lie the product tells about itself. A user
 * who holds the edit capability and is simply not mid-edit must be offered the
 * Edit control instead, and a user who cannot even view the resource is not
 * looking at this screen at all.
 *
 * Making it a component rather than a paragraph each page writes is what makes
 * that rule checkable: `<ReadOnlyNotice canEdit={...}>` renders NOTHING when
 * `canEdit` is true, so the failure mode that shipped on the company profile
 * page (an editable surface showing the read-only sentence because the user
 * had not clicked Edit yet) cannot be written here by accident.
 */

import { Lock } from "lucide-react";

import { cn } from "@/lib/utils";
import { READ_ONLY_TITLE, readOnlyMessage } from "@/lib/permissions";

export function ReadOnlyNotice({
  canEdit,
  resource,
  className,
  message,
}: {
  /** The effective answer. When true this component renders nothing. */
  canEdit: boolean;
  /** What the user is looking at, e.g. "the company profile". */
  resource: string;
  className?: string;
  /** Overrides the default sentence where a surface needs its own wording. */
  message?: string;
}) {
  // The whole enforcement, in one line: an authorized surface has no
  // restriction to explain.
  if (canEdit) return null;

  return (
    <div
      role="note"
      data-testid="read-only-notice"
      className={cn(
        "flex items-start gap-3 rounded-xl border bg-secondary/35 p-4 text-sm",
        className
      )}
    >
      <Lock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-medium">{READ_ONLY_TITLE}</p>
        <p className="mt-1">{message ?? readOnlyMessage(resource)}</p>
      </div>
    </div>
  );
}
