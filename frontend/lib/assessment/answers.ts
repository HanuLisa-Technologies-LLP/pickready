// The answer payload, by question type: its empty shape, whether it is
// complete enough to send, and how it reads as one line of transcript.
//
// These are pure functions over the contracts so the six answer components,
// the conversation player and the tests all agree on what "nothing answered"
// means. A component that decided emptiness for itself would let one format
// send a blank answer that another refused.

import {
  isTextType,
  type AnswerPayload,
  type CodingPayloadView,
  type FillBlankPayloadView,
  type McqPayloadView,
  type QuestionOut,
  type QuestionType,
} from "@/lib/assessment/contracts";

export type TextAnswer = { text: string };
export type McqSingleAnswer = { selected_option_id: string };
export type McqMultiAnswer = { selected_option_ids: string[] };
export type FillBlankAnswer = { values: string[] };
export type CodingAnswer = { language: string; code: string };

export function isTextAnswer(value: AnswerPayload | null): value is TextAnswer {
  return value !== null && "text" in value;
}

export function isMcqSingleAnswer(value: AnswerPayload | null): value is McqSingleAnswer {
  return value !== null && "selected_option_id" in value;
}

export function isMcqMultiAnswer(value: AnswerPayload | null): value is McqMultiAnswer {
  return value !== null && "selected_option_ids" in value;
}

export function isFillBlankAnswer(value: AnswerPayload | null): value is FillBlankAnswer {
  return value !== null && "values" in value;
}

export function isCodingAnswer(value: AnswerPayload | null): value is CodingAnswer {
  return value !== null && "code" in value;
}

/** The prose of a text answer, or "" for anything else. */
export function textOf(value: AnswerPayload | null): string {
  return isTextAnswer(value) ? value.text : "";
}

/** The shape a fresh, unanswered question starts from. */
export function emptyAnswerFor(question: QuestionOut): AnswerPayload {
  switch (question.question_type) {
    case "mcq_single":
      return { selected_option_id: "" };
    case "mcq_multi":
      return { selected_option_ids: [] };
    case "fill_blank":
      return {
        values: (question.payload as FillBlankPayloadView).blanks.map(() => ""),
      };
    case "coding": {
      const payload = question.payload as CodingPayloadView;
      return { language: payload.language, code: payload.starter_code };
    }
    default:
      return { text: "" };
  }
}

/**
 * Whether the answer carries anything worth sending.
 *
 * A fill-blank counts as answered when ANY blank is filled: the server accepts
 * an empty string for an unanswered blank and scores it as such, and a
 * candidate who knows two of three should not be held on the third. A coding
 * answer that still equals its starter code is NOT an answer, which is why the
 * starter is passed in rather than compared against an empty string.
 */
export function isAnswerComplete(
  value: AnswerPayload | null,
  starterCode = ""
): boolean {
  if (value === null) return false;
  if (isTextAnswer(value)) return value.text.trim().length > 0;
  if (isMcqSingleAnswer(value)) return value.selected_option_id.length > 0;
  if (isMcqMultiAnswer(value)) return value.selected_option_ids.length > 0;
  if (isFillBlankAnswer(value)) return value.values.some((item) => item.trim().length > 0);
  if (isCodingAnswer(value)) {
    const code = value.code.trim();
    return code.length > 0 && code !== starterCode.trim();
  }
  return false;
}

/** True when nothing has been entered, in the sense a draft need not be kept. */
export function isAnswerEmpty(value: AnswerPayload | null, starterCode = ""): boolean {
  return !isAnswerComplete(value, starterCode);
}

/**
 * The answer as one readable line, for the candidate's own transcript bubble
 * the moment they press Send. The server renders the authoritative line
 * (`ConversationTurn.answer_line`) and the bubble adopts it on arrival; this
 * one exists so the optimistic bubble is not blank for the seconds in
 * between. Chosen options are quoted by their TEXT, never by their id, for the
 * same reason the recruiter's view quotes them: an id is not evidence of what
 * somebody chose.
 */
export function answerLine(question: QuestionOut | null, value: AnswerPayload | null): string {
  if (value === null) return "";
  if (isTextAnswer(value)) return value.text.trim();
  if (question === null) return "";
  if (isMcqSingleAnswer(value)) {
    const options = (question.payload as McqPayloadView).options;
    const chosen = options.find((option) => option.id === value.selected_option_id);
    return chosen ? chosen.text : "";
  }
  if (isMcqMultiAnswer(value)) {
    const options = (question.payload as McqPayloadView).options;
    return options
      .filter((option) => value.selected_option_ids.includes(option.id))
      .map((option) => option.text)
      .join("; ");
  }
  if (isFillBlankAnswer(value)) {
    return fillTemplate((question.payload as FillBlankPayloadView).template, value.values);
  }
  if (isCodingAnswer(value)) {
    const lines = value.code.split("\n").length;
    return `Code submitted in ${languageLabel(value.language)}, ${lines === 1 ? "one line" : `${lines} lines`}.`;
  }
  return "";
}

/** The blank marker, as `services/assessment_formats/types.BLANK_MARKER`
 *  writes it. Each occurrence is one blank, in order. */
export const BLANK_MARKER = "___";

/** The template with the candidate's values in place of the markers. An
 *  unanswered blank is shown as the marker so the reader sees a gap rather
 *  than a sentence that silently closed over it. */
export function fillTemplate(template: string, values: string[]): string {
  const parts = template.split(BLANK_MARKER);
  return parts
    .map((part, index) => {
      if (index === parts.length - 1) return part;
      const value = (values[index] ?? "").trim();
      return part + (value.length > 0 ? value : BLANK_MARKER);
    })
    .join("");
}

/** How each permitted coding language is written for a person. Mirrors
 *  `services/assessment_formats/types.CODING_LANGUAGES`. */
export const CODING_LANGUAGE_LABELS: Record<string, string> = {
  python: "Python",
  javascript: "JavaScript",
  typescript: "TypeScript",
  java: "Java",
  go: "Go",
  csharp: "C#",
  cpp: "C++",
  sql: "SQL",
  plaintext: "Plain text",
};

export function languageLabel(language: string): string {
  return CODING_LANGUAGE_LABELS[language] ?? language;
}

/** The key under which a turn's answer is drafted and its behaviour captured.
 *
 *  A base question has an id. A follow-up or a re-ask is prose with no
 *  question row of its own, so its key is derived from where it sits in the
 *  conversation: at most one follow-up and one re-ask can be pending on one
 *  base question, so the answered count plus the re-ask flag names it. */
export function turnKeyFor(
  question: QuestionOut | null | undefined,
  answeredQuestions: number,
  isReask: boolean
): string {
  if (question) return question.id;
  return `prose:${answeredQuestions}:${isReask ? "reask" : "follow-up"}`;
}

/** Whether a turn is answered in prose: a text-type base question, or any
 *  follow-up or re-ask, which the server always words as a question. */
export function turnIsProse(question: QuestionOut | null | undefined): boolean {
  return !question || isTextType(question.question_type as QuestionType);
}
