"use client";

import * as React from "react";

import { Button, type ButtonProps } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

/**
 * A button whose action does not happen until a person confirms it.
 *
 * WHY THIS IS ONE COMPONENT AND NOT SIX DIALOGS. The product already guards
 * its most consequential acts well: deleting a BD account makes you type the
 * address, closing a job opens a dialog, rejecting from the ranking table
 * demands a remark. The gap was never a missing pattern, it was that the
 * pattern had to be rebuilt by hand each time, so whichever screen was written
 * in a hurry got a bare `onClick`. `AlertDialog` was already in the repository
 * and used twice. This is the shape that makes reaching for it cheaper than
 * not.
 *
 * `AlertDialog` rather than `Dialog` on purpose: it is `role="alertdialog"`,
 * it focuses CANCEL rather than the destructive action on open, and it does
 * not close on an outside click. A confirmation a stray click dismisses is
 * fine; a confirmation a stray click CONFIRMS would be worse than none, and
 * the cancel-first focus order is what prevents an Enter keypress that was
 * meant for the trigger from landing on the commit.
 *
 * WHAT THIS IS NOT FOR. A reversible toggle does not get one. Archiving a job,
 * disabling an account, unpublishing: the button that undoes them is the same
 * button, still on screen, and interrupting a reversible act teaches people to
 * dismiss confirmations without reading them, which is how the one that
 * mattered gets clicked through too.
 */
export function ConfirmButton({
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  onConfirm,
  destructive = true,
  children,
  ...buttonProps
}: Omit<ButtonProps, "onClick"> & {
  /** A question naming the exact thing, e.g. "Deactivate Priya Nair?". */
  title: string;
  /** What happens, and whether it can be undone. One or two sentences. */
  description: React.ReactNode;
  /** The verb, matching the trigger. Never "OK" or "Yes". */
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  /** Tints the commit button red. Off for a confirm that destroys nothing. */
  destructive?: boolean;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button {...buttonProps}>{children}</Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{cancelLabel}</AlertDialogCancel>
          <AlertDialogAction
            className={
              destructive
                ? "bg-destructive text-destructive-foreground hover:bg-destructive/90"
                : undefined
            }
            onClick={onConfirm}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
