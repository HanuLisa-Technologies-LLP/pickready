import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * `label` names the table for assistive tech AND turns the scroll box into a
 * keyboard-reachable region.
 *
 * Both halves are one decision. A table wider than its container scrolls
 * sideways, and a mouse user drags it while a keyboard user cannot reach the
 * columns at all: a scrollable box has to be focusable (WCAG 2.1.1), and a
 * focusable box has to be a named region or it is an unexplained tab stop.
 * So the region is OPT IN through this one prop rather than applied to every
 * table, which would have added a silent tab stop to each of the fifteen.
 */
const Table = React.forwardRef<
  HTMLTableElement,
  React.HTMLAttributes<HTMLTableElement> & { label?: string }
>(({ className, label, ...props }, ref) => (
  <div
    className="relative w-full overflow-x-auto rounded-xl border border-border bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    {...(label
      ? { role: "region", "aria-label": label, tabIndex: 0 }
      : {})}
  >
    <table
      ref={ref}
      aria-label={label}
      className={cn(
        "w-full caption-bottom text-sm [font-variant-numeric:tabular-nums]",
        className
      )}
      {...props}
    />
  </div>
));
Table.displayName = "Table";

const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead
    ref={ref}
    className={cn(
      // Sticky so a long candidate list keeps its column names in view. The
      // wrapper scrolls, so the header pins against the top of the viewport.
      "sticky top-0 z-10 bg-secondary/80 backdrop-blur-sm [&_tr]:border-b",
      className
    )}
    {...props}
  />
));
TableHeader.displayName = "TableHeader";

const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody
    ref={ref}
    className={cn("[&_tr:last-child]:border-0", className)}
    {...props}
  />
));
TableBody.displayName = "TableBody";

const TableFooter = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tfoot
    ref={ref}
    className={cn(
      "border-t bg-muted/50 font-medium [&>tr]:last:border-b-0",
      className
    )}
    {...props}
  />
));
TableFooter.displayName = "TableFooter";

const TableRow = React.forwardRef<
  HTMLTableRowElement,
  React.HTMLAttributes<HTMLTableRowElement>
>(({ className, ...props }, ref) => (
  <tr
    ref={ref}
    className={cn(
      "border-b border-border transition-colors last:border-0 hover:bg-brand-100/50 data-[state=selected]:bg-brand-100",
      className
    )}
    {...props}
  />
));
TableRow.displayName = "TableRow";

const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
  // `scope="col"` is the DEFAULT, not something fifteen call sites have to
  // remember. Without it a `<th>` in a wide table is ambiguous, and a screen
  // reader reading a cell cannot reliably say which column it belongs to. A
  // caller that needs `scope="row"` still overrides it through `...props`.
>(({ className, scope = "col", ...props }, ref) => (
  <th
    ref={ref}
    scope={scope}
    className={cn(
      "h-11 px-4 text-left align-middle text-xs font-semibold uppercase tracking-wide text-muted-foreground [&:has([role=checkbox])]:pr-0",
      className
    )}
    {...props}
  />
));
TableHead.displayName = "TableHead";

const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td
    ref={ref}
    className={cn(
      "px-4 py-3.5 align-middle [&:has([role=checkbox])]:pr-0",
      className
    )}
    {...props}
  />
));
TableCell.displayName = "TableCell";

const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
  <caption
    ref={ref}
    className={cn("mt-4 text-sm text-muted-foreground", className)}
    {...props}
  />
));
TableCaption.displayName = "TableCaption";

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
};
