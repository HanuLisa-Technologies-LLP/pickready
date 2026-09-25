"""The candidate's coding Run and final-answer state, as they cross the API.

CANDIDATE-SAFE BY CONSTRUCTION. Nothing here can carry the answer key: no
field is named after a hidden test or the reference solution
(`tests/test_coding_key_confinement.py` sweeps every schema for exactly
that), and the only test content a response holds is a VISIBLE sample's
expected output, which the candidate already reads in the problem.

NO NUMBER. A Run result is a WORD ("Passed", "Wrong answer") and the
program's own output; there is no timing, no memory figure, no count and no
score. The final answer's state is one of three words, never a result: the
hidden tests stay hidden, including how the program did on them.

`extra="forbid"` on the one request body, so a client that starts sending a
field the server does not read learns it at once rather than believing the
field did something.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.coding import CODE_SOURCE_MAX_CHARS

__all__ = [
    "CodingRunIn",
    "CodingRunStartedOut",
    "CodingRunTestOut",
    "CodingRunOut",
    "CodingSubmissionStateOut",
]

#: `coding_runs.client_token` is varchar(64); `runs.CLIENT_TOKEN_MAX_CHARS`.
_CLIENT_TOKEN_MAX = 64
#: `coding_runs.language` is varchar(20).
_LANGUAGE_MAX = 20


class CodingRunIn(BaseModel):
    """One press of Run: the code in the editor, its language, and a token
    the CLIENT mints per click, so a retried request is the same run."""

    model_config = ConfigDict(extra="forbid")

    language: str = Field(min_length=1, max_length=_LANGUAGE_MAX)
    #: Bounded at the parse so an oversized body is refused before it is
    #: held; `runs.start_run` refuses an empty one with its own sentence.
    source: str = Field(max_length=CODE_SOURCE_MAX_CHARS)
    client_token: str = Field(min_length=1, max_length=_CLIENT_TOKEN_MAX)


class CodingRunStartedOut(BaseModel):
    """`POST .../runs`, 202: the run to poll and where it stands."""

    run_id: uuid.UUID
    status: str


class CodingRunTestOut(BaseModel):
    """One VISIBLE sample test's result, in words and the program's output."""

    #: The sample this result is for: the payload's `visible_tests[].id`.
    key: str
    passed: bool
    result_word: str
    stdout: str
    expected_stdout: str
    stderr: str
    compile_output: str


class CodingRunOut(BaseModel):
    """`GET .../runs/{run_id}`."""

    run_id: uuid.UUID
    #: `queued`, `complete`, `unavailable` or `failed` (`models/coding`).
    status: str
    #: Empty until the run completes; one entry per sample, in order.
    tests: list[CodingRunTestOut] = []
    #: The server's sentence when the run could not be completed.
    message: str | None = None


class CodingSubmissionStateOut(BaseModel):
    """`GET .../submission`: what the candidate is told about their own final
    answer. "Submitted", "Being checked" or "Checked"; never a result."""

    state_word: str
