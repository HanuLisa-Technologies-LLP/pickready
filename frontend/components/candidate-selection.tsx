"use client";

// Candidate pool with real multi-select (checkbox per candidate, select
// all/none, live "N selected" badge) and the "Send Email to Selected" action.
//
// Selection state is OWNED BY THE PARENT and keyed by `link_id`, so it survives
// re-renders and list reloads: re-fetching the pool never clears the ticks.
// Candidates without an email address cannot be selected, the reason is shown
// inline rather than failing later at send time.

import * as React from "react";
import { Mails } from "lucide-react";

import type { CandidateLink } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TierBadge } from "@/components/tier-badge";

export interface CandidateSelectionProps {
  links: CandidateLink[];
  /** Selected `link_id`s, owned by the parent so it survives re-renders. */
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onSend: () => void;
  /** False when the user lacks `send_outreach`, the button is then hidden. */
  canSend?: boolean;
  sending?: boolean;
}

const hasEmail = (link: CandidateLink) => Boolean(link.candidate?.email);

export function CandidateSelection({
  links,
  selected,
  onSelectedChange,
  onSend,
  canSend = true,
  sending = false,
}: CandidateSelectionProps) {
  const emailable = React.useMemo(() => links.filter(hasEmail), [links]);
  const selectedCount = selected.size;
  const allSelected =
    emailable.length > 0 && emailable.every((l) => selected.has(l.link_id));
  const someSelected = selectedCount > 0 && !allSelected;

  const headerRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (headerRef.current) headerRef.current.indeterminate = someSelected;
  }, [someSelected]);

  const toggleOne = (linkId: string) => {
    const next = new Set(selected);
    if (next.has(linkId)) next.delete(linkId);
    else next.add(linkId);
    onSelectedChange(next);
  };

  const toggleAll = () => {
    if (allSelected) {
      onSelectedChange(new Set());
      return;
    }
    onSelectedChange(new Set(emailable.map((l) => l.link_id)));
  };

  const sendDisabled = selectedCount === 0 || sending;
  const sendTooltip =
    selectedCount === 0
      ? "Tick at least one candidate to enable this"
      : `Compose an email to ${selectedCount} selected candidate${
          selectedCount === 1 ? "" : "s"
        }`;

  return (
    <Card>
      <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2">
            Candidate pool
            <Badge variant={selectedCount > 0 ? "default" : "secondary"}>
              {selectedCount} selected
            </Badge>
          </CardTitle>
          <CardDescription>
            Tick the candidates you want to contact, then compose the email, 
            AI-personalized or written by you.
          </CardDescription>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={emailable.length === 0}
            onClick={toggleAll}
          >
            {allSelected ? "Select none" : "Select all"}
          </Button>
          {canSend ? (
            <span title={sendTooltip} className="inline-flex">
              <Button
                type="button"
                className="gap-2"
                disabled={sendDisabled}
                aria-disabled={sendDisabled}
                onClick={onSend}
              >
                <Mails className="h-4 w-4" />
                {sending
                  ? "Sending"
                  : `Send Email to Selected (${selectedCount})`}
              </Button>
            </span>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <input
                  ref={headerRef}
                  type="checkbox"
                  className="h-4 w-4 cursor-pointer accent-foreground"
                  aria-label="Select all candidates"
                  checked={allSelected}
                  disabled={emailable.length === 0}
                  onChange={toggleAll}
                />
              </TableHead>
              <TableHead>Candidate</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Tier</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {links.length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="text-center">
                  No candidates linked to this job yet. Upload resumes or run
                  matching to populate the pool.
                </TableCell>
              </TableRow>
            ) : (
              links.map((link) => {
                const selectable = hasEmail(link);
                const checked = selected.has(link.link_id);
                return (
                  <TableRow
                    key={link.link_id}
                    data-state={checked ? "selected" : undefined}
                  >
                    <TableCell>
                      <input
                        type="checkbox"
                        className="h-4 w-4 cursor-pointer accent-foreground disabled:cursor-not-allowed"
                        aria-label={`Select ${
                          link.candidate?.full_name ||
                          link.candidate?.email ||
                          "candidate"
                        }`}
                        checked={checked}
                        disabled={!selectable}
                        title={
                          selectable
                            ? undefined
                            : "No email address on file for this candidate"
                        }
                        onChange={() => toggleOne(link.link_id)}
                      />
                    </TableCell>
                    <TableCell className="font-medium">
                      {link.candidate?.full_name ||
                        link.candidate?.email ||
                        "Unnamed candidate"}
                    </TableCell>
                    <TableCell>
                      {link.candidate?.email || (
                        <span className="italic">no email on file</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <TierBadge tier={link.tier} />
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
