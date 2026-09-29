"""Leadership Intelligence (owner spec 2026-09-29, sections 16 to 22).

The rework of Drishti (spec rule 37.2: no second leadership system beside
it). `compiler` turns a leader's words into the bounded artifact, `context`
resolves which leadership input applies to a job and freezes it, `profiles`
is the one writer and reader of a leader's own versions, and `draft` writes
the AI draft a leader reviews and never saves by itself.
"""
