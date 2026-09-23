"use client";

// Delete My Profile on My Profile (VIVEKIUM_SPRINT_FEATURES.md feature 7, and
// India's Digital Personal Data Protection Act, 2023).
//
// THIS COMPONENT AUTHORS NO COPY ABOUT WHAT DELETION DOES. Every warning line,
// the heading, the instruction and the confirmation phrase are fetched from
// `GET /portal/me/deletion-notice` and rendered verbatim. Same rule
// `permission-notice.tsx` follows, and it matters more here than anywhere else
// in the product: a screen that described an irreversible rule in its own words
// would keep describing whatever was true on the day it was written, and the
// person reading it cannot check.
//
// The consequence is deliberate and visible: with no notice loaded there is no
// button. A destructive control whose warning failed to arrive must not be
// clickable, because the warning IS the informed part of informed consent.

import * as React from "react";
import { TriangleAlert } from "lucide-react";

import { api, apiGet } from "@/lib/api";
import { apiErrorMessage } from "@/lib/validation-errors";
import { useToast } from "@/components/ui/toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface DeletionNotice {
  heading: string;
  warnings: string[];
  confirmation_phrase: string;
  instruction: string;
}

export function DeleteProfileCard() {
  const { toast } = useToast();
  const [notice, setNotice] = React.useState<DeletionNotice | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [open, setOpen] = React.useState(false);
  const [typed, setTyped] = React.useState("");
  const [deleting, setDeleting] = React.useState(false);

  React.useEffect(() => {
    apiGet<DeletionNotice>("/portal/me/deletion-notice")
      .then(setNotice)
      .catch((error) => setLoadError(apiErrorMessage(error)));
  }, []);

  // The same comparison the server makes: trim, but do not fold case. Matching
  // it here is a courtesy that disables the button early; the server's check is
  // the one that decides, because this route is reachable by anything holding
  // the session cookie.
  const phraseMatches =
    notice !== null && typed.trim() === notice.confirmation_phrase;

  const confirmDelete = async () => {
    if (!notice || !phraseMatches) return;
    setDeleting(true);
    try {
      await api("/portal/me", {
        method: "DELETE",
        body: { confirmation: typed.trim() },
      });
      // The server has already cleared the session cookies on this response.
      // A hard navigation rather than a client-side route change, so no cached
      // state from the account that no longer exists survives the redirect.
      window.location.href = "/login";
    } catch (error) {
      setDeleting(false);
      toast({
        title: "Profile not deleted",
        description: apiErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  return (
    <Card className="border-destructive">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TriangleAlert className="h-5 w-5 text-destructive" aria-hidden />
          {notice ? notice.heading : "Delete my profile"}
        </CardTitle>
        <CardDescription>
          You can ask for everything held about you to be permanently deleted.
          This is your right, and it cannot be undone.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loadError ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {loadError}
          </p>
        ) : !notice ? (
          <p role="status" className="text-sm">
            Loading what deletion would mean
          </p>
        ) : (
          <Button
            variant="destructive"
            onClick={() => {
              setTyped("");
              setOpen(true);
            }}
          >
            {notice.heading}
          </Button>
        )}
      </CardContent>

      <AlertDialog
        open={open}
        onOpenChange={(next) => {
          // A dialog that kept the typed phrase between openings would let a
          // second, accidental open start already confirmed.
          if (!next) setTyped("");
          if (!deleting) setOpen(next);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{notice?.heading}</AlertDialogTitle>
            <AlertDialogDescription>
              Read this before you confirm.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <ul className="space-y-2 text-sm">
            {(notice?.warnings ?? []).map((line) => (
              <li key={line} className="flex gap-2">
                <TriangleAlert
                  className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
                  aria-hidden
                />
                <span>{line}</span>
              </li>
            ))}
          </ul>

          <div className="space-y-2">
            <Label htmlFor="delete-confirmation">{notice?.instruction}</Label>
            <Input
              id="delete-confirmation"
              value={typed}
              autoComplete="off"
              disabled={deleting}
              onChange={(event) => setTyped(event.target.value)}
              aria-describedby="delete-confirmation-help"
            />
            <p id="delete-confirmation-help" className="text-xs">
              {notice
                ? `The button stays disabled until this reads exactly ${notice.confirmation_phrase}.`
                : null}
            </p>
          </div>

          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>
              Keep my profile
            </AlertDialogCancel>
            {/* Deliberately a plain Button rather than AlertDialogAction: the
                Action primitive closes the dialog on click, which would dismiss
                the surface before the request has answered and leave a failed
                deletion with nowhere to report itself. */}
            <Button
              variant="destructive"
              disabled={!phraseMatches || deleting}
              onClick={() => void confirmDelete()}
            >
              {deleting ? "Deleting" : "Delete permanently"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
