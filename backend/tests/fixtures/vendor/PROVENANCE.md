# Vendor contract fixtures

Every file under this directory was **hand-authored from the vendor's published
API schema and has never been checked against a live call.** There is no OpenAI
key and no Voyage key in this phase, so nothing here is a recording of traffic.

The OpenAI set additionally carries a second unproven claim that the previous
set did not: `gpt-5.6-terra` and `gpt-5.6-luna` are the product owner's strings
and have never been resolved against a models endpoint. "Does this id exist" is
unverified here alongside "is this the shape it returns", and a wrong id would
arrive as a 404 or a 403 rather than as a shape disagreement.

The honest framing for everything these fixtures support is:

> built and tested against recorded fixtures and a stub provider; not executed against a live provider.

## What the fixtures prove, and what they do not

They prove that **this codebase's parsing, failure classification, backoff,
timeout and circuit-breaker behaviour are correct with respect to the shapes
declared in `app/services/reliability/vendor_contract.py`.** They prove nothing
whatsoever about whether the vendors actually send those shapes. If the
documentation is wrong, or the schema changes, every test in
`tests/test_vendor_contracts.py` still passes and the product is still broken.

The mechanism that covers that gap is `check_openai_response` and
`check_voyage_response`, which run on the FIRST live response per path and
raise `VendorContractViolation` naming the fixture the response disagreed with.
`scripts/verify_live.py` is the command that exercises them, and it has never
been run.

## File envelope

Each fixture is an envelope rather than a bare body, so a status code, the
headers a classifier reads, and the provenance of that particular payload all
travel with it:

```json
{
  "_provenance": {
    "authored_from": "<the published schema or documented behaviour>",
    "observed": false,
    "notes": "<anything a reader needs, e.g. truncation>"
  },
  "status": 200,
  "headers": {},
  "body": {}
}
```

`observed` is `false` in every file here and the loader asserts it. If someone
eventually records a real response, they must set it to `true` in the same
edit, which is what makes "recorded" and "hand-authored" distinguishable in a
diff rather than a matter of memory.

## Truncated vectors

`voyage/embeddings_response_document.json` carries vectors truncated to eight
components so the file can be read. The real contract is 1024, stated
explicitly at the call site as `output_dimension` because the schema's columns
are `vector(1024)`. Shape and width are therefore two separate assertions:
`check_voyage_response` takes `expected_dimension=None` against the truncated
fixture, and the test expands a fixture row to full width to exercise the width
check.

## The vendor changed on 2026-08-31

The model vendor moved from Anthropic to OpenAI by owner decision, and the
Anthropic fixtures were DELETED rather than kept beside the new ones. Keeping
them would have left a directory in which half the recorded shapes describe an
endpoint nothing calls, and the first person to reach for one would have no way
to tell which half was live. The embedding vendor did not change, so the
`voyage/` set is untouched.

Two fixtures have no successor and that is deliberate:
`error_400_prefill_rejected.json` and `error_400_sampling_rejected.json`
recorded published constraints on a last-assistant-turn prefill and a
non-default temperature. The prefill no longer exists -- JSON mode is
`response_format` now -- and the temperature constraint was a property of that
vendor's models. `error_400_invalid_request.json` replaces both with the one
documented 400 the current request shape can actually provoke.

`error_503_overloaded.json` replaces `error_529_overloaded.json` for the same
reason: 529 was one vendor's overload status and 503 is the other's.

## Error bodies

OpenAI error bodies are documented and are reproduced here. **Voyage error
bodies are not reproduced in detail, deliberately**: the client
(`app/services/embeddings.py`) classifies on the HTTP status and raises
`EmbeddingError` naming the exception class, and it never parses or logs the
body, because an embedding request carries a real candidate's resume text. A
fixture asserting a body shape nothing reads would be a claim about the vendor
with no code behind it.
