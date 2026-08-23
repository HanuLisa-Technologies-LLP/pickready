"""The COMPANY-JOB-CANDIDATE reference code.

What it has to be, and what each property is protecting:

  * STABLE, or the code on a report disagrees with the code in the list and it
    stops being a way to identify anything.
  * SEGMENTED, so two candidates on one job visibly share a prefix. That is the
    entire reason it is not one hash over the triple.
  * ONE-WAY, because it appears on screens, in exports and potentially in
    forwarded email; it must identify a row without disclosing anything.
  * DOMAIN-SEPARATED, so a value computed for one position cannot be replayed
    into another.
  * READABLE, because it exists to be typed, quoted and read aloud.
"""
import uuid

import pytest

from app.services import reference_code as rc


TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
JOB = uuid.UUID("22222222-2222-2222-2222-222222222222")
CANDIDATE = uuid.UUID("33333333-3333-3333-3333-333333333333")


def test_the_code_is_stable_for_the_same_three_ids():
    first = rc.reference_code(TENANT, JOB, CANDIDATE)
    assert first == rc.reference_code(TENANT, JOB, CANDIDATE)
    # Accepts strings as readily as UUIDs -- a row read out of raw SQL carries
    # strings, and the two must not produce different codes for the same row.
    assert first == rc.reference_code(str(TENANT), str(JOB), str(CANDIDATE))


def test_the_shape_is_three_readable_groups():
    code = rc.reference_code(TENANT, JOB, CANDIDATE)
    assert rc.is_wellformed(code), code
    company, job, candidate = code.split(rc.SEPARATOR)
    assert len({len(company), len(job), len(candidate)}) == 1


def test_the_alphabet_excludes_every_confusable_character():
    """It is read aloud and typed. I/1, L/1, O/0 are the misreads that matter."""
    codes = "".join(
        rc.reference_code(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        for _ in range(200)
    ).replace(rc.SEPARATOR, "")
    assert not (set(codes) & set("ILOU")), sorted(set(codes) & set("ILOU"))


def test_two_candidates_on_one_job_share_their_first_two_segments():
    """The whole point of segmenting: a person can see the rows belong
    together, which one opaque digest over the triple could never show."""
    a = rc.reference_code(TENANT, JOB, uuid.uuid4()).split(rc.SEPARATOR)
    b = rc.reference_code(TENANT, JOB, uuid.uuid4()).split(rc.SEPARATOR)
    assert a[:2] == b[:2]
    assert a[2] != b[2]


def test_a_different_job_changes_only_the_job_segment():
    a = rc.reference_code(TENANT, JOB, CANDIDATE).split(rc.SEPARATOR)
    b = rc.reference_code(TENANT, uuid.uuid4(), CANDIDATE).split(rc.SEPARATOR)
    assert a[0] == b[0]
    assert a[1] != b[1]
    assert a[2] == b[2]


def test_a_different_company_changes_the_company_segment():
    """Two tenants must never render an identical code, or one customer's
    reference could be quoted against another's row."""
    a = rc.reference_code(TENANT, JOB, CANDIDATE)
    b = rc.reference_code(uuid.uuid4(), JOB, CANDIDATE)
    assert a.split(rc.SEPARATOR)[0] != b.split(rc.SEPARATOR)[0]
    assert a != b


def test_positions_are_domain_separated():
    """The SAME uuid in the job slot and the candidate slot must not produce
    the same segment. Without the per-position prefix it would, and a segment
    lifted from one position would be valid in another."""
    same = uuid.uuid4()
    code = rc.reference_code(TENANT, same, same)
    _, job_segment, candidate_segment = code.split(rc.SEPARATOR)
    assert job_segment != candidate_segment


def test_the_code_does_not_contain_the_identifiers_it_describes():
    """One-way by construction: it is an HMAC under the app secret, not a
    truncation of the id and not a bare hash of it."""
    code = rc.reference_code(TENANT, JOB, CANDIDATE).replace(rc.SEPARATOR, "")
    for identifier in (TENANT, JOB, CANDIDATE):
        hexed = identifier.hex.upper()
        assert code not in hexed
        # No four-character run of the id survives into the code either.
        assert not any(hexed[i : i + 4] in code for i in range(len(hexed) - 3))


def test_there_is_no_decode_function():
    """A reverse would defeat the reason it is hashed at all."""
    assert not hasattr(rc, "decode")
    assert not hasattr(rc, "parse")


def test_a_missing_identifier_renders_rather_than_raising():
    """A half-built row still has to draw a name. A display aid that can throw
    would take the whole page with it."""
    code = rc.reference_code(TENANT, JOB, None)
    assert rc.is_wellformed(code)
    assert code.split(rc.SEPARATOR)[2] == "0" * rc.SEGMENT_LENGTH


@pytest.mark.parametrize(
    "value",
    ["", "not-a-code", "AAAA-BBBB", "AAAA-BBBB-CCCC-DDDD", "IIII-BBBB-CCCC"],
)
def test_illformed_codes_are_rejected(value):
    assert not rc.is_wellformed(value)


def test_candidates_on_one_job_do_not_collide():
    """Within a job the candidate segment is the only thing distinguishing two
    rows, so this is the collision that would actually mislead somebody."""
    seen = {
        rc.reference_code(TENANT, JOB, uuid.uuid4()).split(rc.SEPARATOR)[2]
        for _ in range(5000)
    }
    # ~5000 draws from a 32^4 space: a handful of birthday collisions is
    # expected and harmless; a systematic one is not.
    assert len(seen) > 4900, len(seen)


def test_job_reference_is_the_first_two_segments():
    full = rc.reference_code(TENANT, JOB, CANDIDATE)
    assert full.startswith(rc.job_reference(TENANT, JOB) + rc.SEPARATOR)
    assert rc.company_segment(TENANT) == full.split(rc.SEPARATOR)[0]
