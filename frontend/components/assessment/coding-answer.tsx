"use client";

// The coding question (assessment spec 2.5).
//
// A full-height editor with the language stated beside it, and a selector
// ONLY when the question permits more than one language. A selector with one
// entry is a control that does nothing, and on a screen where every control
// is being watched for timing it would also be a click with no meaning.

import * as React from "react";

import { CodeEditor } from "@/components/assessment/code-editor";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AnswerComponentProps, CodingPayloadView } from "@/lib/assessment/contracts";
import { isCodingAnswer, languageLabel } from "@/lib/assessment/answers";

export function CodingAnswer({
  question,
  prompt,
  value,
  onChange,
  disabled,
  fieldHooks,
  onSubmitShortcut,
}: AnswerComponentProps) {
  const payload = question.payload as CodingPayloadView;
  const current = isCodingAnswer(value)
    ? value
    : { language: payload.language, code: payload.starter_code };
  const selectorId = `language-${question.id}`;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {payload.language_options.length > 1 ? (
          <div className="flex items-center gap-2">
            <Label htmlFor={selectorId} className="text-xs">
              Language
            </Label>
            <Select
              value={current.language}
              disabled={disabled}
              onValueChange={(language) => onChange({ ...current, language })}
            >
              <SelectTrigger id={selectorId} className="h-9 w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {payload.language_options.map((language) => (
                  <SelectItem key={language} value={language}>
                    {languageLabel(language)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <p className="text-xs font-medium" data-testid="language-indicator">
            {languageLabel(current.language)}
          </p>
        )}
      </div>

      {payload.constraints ? (
        <div className="border border-border bg-muted p-3 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wide">Constraints</p>
          <p className="mt-1 whitespace-pre-wrap">{payload.constraints}</p>
        </div>
      ) : null}

      <CodeEditor
        value={current.code}
        language={current.language}
        onChange={(code) => onChange({ ...current, code })}
        disabled={disabled}
        fieldHooks={fieldHooks}
        onSubmitShortcut={onSubmitShortcut}
        ariaLabel={`Your code for: ${prompt}`}
        className="[&_.cm-editor]:min-h-[18rem]"
      />
      <p className="text-xs">Press Ctrl+Enter (Cmd+Enter on Mac) to send.</p>
    </div>
  );
}
