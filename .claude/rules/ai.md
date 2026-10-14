---
paths:
  - "backend/app/services/{yukti,miti,siddhi,hiring,rag,tools,agents,evidence,assessment_pipeline,assessment_questions,assessment_conversation,assessment_formats,coding_assessment,code_execution,leadership}/**"
  - "backend/app/services/{assessment_contract,evidence_retrieval,agent_loop,generation_sufficiency,llm_router,skills}.py"
  - "backend/app/prompts/**"
  - "backend/app/config/llm_providers.py"
  - "backend/app/evaluation/**"
---
# AI and hiring intelligence (digest; full rules in docs/engineering/ai-runtime.md)

- Assess against the bound snapshot (`assessment_contract`), never live skill rows. Miti is the sole grader; three statuses: graded, unanswered, not_assessed.
- The model returns words; code decides what they are worth. No number reaches a client. A model failure is "not assessed", never a substitute score.
- Evidence RAG only through `services/evidence_retrieval` and the tool layer; every tool is a bounded read. Miti and Siddhi import neither retrieval nor the index.
- Judges live in `app/evaluation/` only. `unavailable` is never a pass.
- Coding questions: static checker plus AI review, no execution or sandbox (S-20): decided and NOT built. Yukti five-part score 45/15/20/15/5 (S-21): decided and NOT built; code is still six-part. A suspicious retrieved chunk is quarantined and recorded (A-20), not yet wired.
- Bump a prompt's version line; register its builder in `tests/test_ctc_never_in_prompt.py`.
