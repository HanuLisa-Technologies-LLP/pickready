"use client";

// The unified job description document (client change, 2026-07-28).
//
// The JD used to be seven separate text boxes. It is now ONE markdown document:
// the AI drafts it, the recruitment team edits it with an explicit Edit button,
// and only then can the job be published.
//
// This renders a deliberately small subset of markdown, headings, bullets and
// paragraphs, because that is all the JD prompt emits. A full markdown parser
// would be a dependency and an injection surface for no gain.

import * as React from "react";
import { Check, Pencil, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type Block =
  | { kind: "heading"; text: string }
  | { kind: "para"; text: string }
  | { kind: "list"; items: string[] };

/** Parse the JD into blocks. Exported so the render is unit testable. */
export function parseJdBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  let list: string[] = [];
  let para: string[] = [];

  const flushList = () => {
    if (list.length) blocks.push({ kind: "list", items: list });
    list = [];
  };
  const flushPara = () => {
    if (para.length) blocks.push({ kind: "para", text: para.join(" ") });
    para = [];
  };

  for (const raw of (markdown ?? "").split("\n")) {
    const line = raw.trimEnd();
    const heading = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*$/);
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/);

    if (heading) {
      flushList();
      flushPara();
      blocks.push({ kind: "heading", text: heading[1] });
    } else if (bullet) {
      flushPara();
      list.push(bullet[1]);
    } else if (!line.trim()) {
      flushList();
      flushPara();
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushList();
  flushPara();
  return blocks;
}

/** Read-only rendering of the JD document. */
export function JdDocument({ markdown }: { markdown: string }) {
  const blocks = React.useMemo(() => parseJdBlocks(markdown), [markdown]);

  if (blocks.length === 0) {
    return (
      <p className="py-8 text-center text-sm">
        No job description yet. Draft one with AI, or write it yourself.
      </p>
    );
  }

  return (
    <article className="space-y-4">
      {blocks.map((block, i) => {
        if (block.kind === "heading") {
          return (
            <h3 key={i} className="pt-2 text-sm font-semibold">
              {block.text}
            </h3>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={i} className="list-disc space-y-1 pl-5 text-sm leading-6">
              {block.items.map((item, j) => (
                <li key={j}>{item}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i} className="text-sm leading-6">
            {block.text}
          </p>
        );
      })}
    </article>
  );
}

/**
 * The JD with an explicit Edit button, which the client asked for by name so
 * that editing is obviously available rather than implied by a click target.
 *
 * Editing is local until Save, so an accidental keystroke cannot reach the
 * server, and Cancel restores what was there.
 */
export function JdEditor({
  markdown,
  onSave,
  saving = false,
  title = "Job description",
  /** Start in edit mode, used right after an AI draft lands. */
  initiallyEditing = false,
}: {
  markdown: string;
  onSave: (next: string) => void | Promise<void>;
  saving?: boolean;
  title?: string;
  initiallyEditing?: boolean;
}) {
  const [editing, setEditing] = React.useState(initiallyEditing);
  const [draft, setDraft] = React.useState(markdown);

  // Follow the source when it changes underneath us (an AI draft arriving),
  // but never clobber what the user is actively typing.
  React.useEffect(() => {
    if (!editing) setDraft(markdown);
  }, [markdown, editing]);

  return (
    <section className="rounded-lg border">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {editing ? (
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="gap-1.5"
              disabled={saving}
              onClick={() => {
                setDraft(markdown);
                setEditing(false);
              }}
            >
              <X className="h-3.5 w-3.5" />
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              className="gap-1.5"
              disabled={saving || !draft.trim()}
              onClick={async () => {
                await onSave(draft);
                setEditing(false);
              }}
            >
              <Check className="h-3.5 w-3.5" />
              {saving ? "Saving" : "Save"}
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => setEditing(true)}
          >
            <Pencil className="h-3.5 w-3.5" />
            Edit JD
          </Button>
        )}
      </header>

      <div className="px-4 py-4">
        {editing ? (
          <Textarea
            aria-label="Job description"
            className="min-h-[420px] font-mono text-[13px] leading-6"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        ) : (
          <JdDocument markdown={markdown} />
        )}
      </div>
    </section>
  );
}
