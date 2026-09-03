"use client";

// One dispatcher, one contract, six implementations (assessment spec 5.1).
//
// The table is the whole of the dispatch. A format is added by writing a
// component against `AnswerComponentProps` and naming it here; nothing else
// in the player knows which formats exist. An unknown type throws rather than
// rendering a text box: a structured question answered in prose would be
// refused by the server, and a candidate typing into a box that will be
// refused is worse than an error the page can report.

import * as React from "react";

import { CodingAnswer } from "@/components/assessment/coding-answer";
import { EvidenceAnswer } from "@/components/assessment/evidence-answer";
import { FillBlankAnswer } from "@/components/assessment/fill-blank-answer";
import { McqMultiAnswer } from "@/components/assessment/mcq-multi-answer";
import { McqSingleAnswer } from "@/components/assessment/mcq-single-answer";
import { ShortAnswer } from "@/components/assessment/short-answer";
import type { AnswerComponentProps, QuestionType } from "@/lib/assessment/contracts";

const COMPONENTS: Record<QuestionType, React.ComponentType<AnswerComponentProps>> = {
  evidence_based: EvidenceAnswer,
  short_answer: ShortAnswer,
  mcq_single: McqSingleAnswer,
  mcq_multi: McqMultiAnswer,
  fill_blank: FillBlankAnswer,
  coding: CodingAnswer,
};

export function QuestionRenderer(props: AnswerComponentProps) {
  const Component = COMPONENTS[props.question.question_type];
  if (!Component) {
    throw new Error(`No answer component for question type ${props.question.question_type}`);
  }
  return (
    <div data-testid="question-renderer" data-question-type={props.question.question_type}>
      <Component {...props} />
    </div>
  );
}
