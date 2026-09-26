"""Yukti: resume evidence matching and ranking (Vivekium release, Phase 2).

The package is the one implementation of how a candidate's RESUME is read
against a job before any assessment:

  config.py          the fixed six-part structure, its weights and limits (data)
  anonymise.py       the name-blind pass every resume takes before a model
  inputs.py          the job context and the candidate input, CTC-free
  judge.py           the ONE model call per batch, with one corrective retry
  grounding.py       deterministic checks that every claim is in the resume
  validation_fit.py  part six, the application answers, deterministic
  scoring.py         arithmetic from grounded verdicts to a stored outcome

Nothing here is imported eagerly by this `__init__`, so importing one module
never drags the model router into a caller that only wanted a constant.
"""
