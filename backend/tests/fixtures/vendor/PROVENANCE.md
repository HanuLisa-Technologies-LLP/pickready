# Vendor contract fixtures

Every file under this directory was **hand-authored from the vendor's published
API schema and has never been checked against a live call.** There is no
Anthropic key and no Voyage key in this phase (spec-doc6 D6), so nothing here
is a recording of traffic.

The honest framing for everything these fixtures support is:

> built and tested against recorded fixtures and a stub provider; not executed against a live provider.

## What the fixtures prove, and what they do not

They prove that **this codebase's parsing, failure classification, backoff,
timeout and circuit-breaker behaviour are correct with respect to the shapes
declared in `app/services/reliability/vendor_contract.py`.** They prove nothing
whatsoever about whether the vendors actually send those shapes. If the
documentation is wrong, or the schema changes, every test in
`tests/test_vendor_contracts.py` still passes and the product is still broken.

The mechanism that covers that gap is `check_anthropic_response` and
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

## Error bodies

Anthropic error bodies are documented and are reproduced here. **Voyage error
bodies are not reproduced in detail, deliberately**: the client
(`app/services/embeddings.py`) classifies on the HTTP status and raises
`EmbeddingError` naming the exception class, and it never parses or logs the
body, because an embedding request carries a real candidate's resume text. A
fixture asserting a body shape nothing reads would be a claim about the vendor
with no code behind it.
