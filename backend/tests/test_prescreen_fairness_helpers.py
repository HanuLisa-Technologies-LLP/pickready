"""Yukti's fairness-critical helpers, arm by arm.

Three of these carry a rule the Runbook argues for explicitly, and each rule
has an arm that only fires on the unusual input:

  DECAY MUST NOT BECOME AN AGE PROXY (section 6.3). An undated claim is not
  decayed, because guessing a date and then decaying it is exactly how decay
  turns into an age penalty. The `None` arm is that rule.

  THE STRENGTH FLOOR MATTERS MORE THAN THE CEILING (section 6.1). A
  zero-strength claim is removed from the ledger by arithmetic rather than
  recorded as weak, and a weak claim that is visible can be probed while one
  arithmetic deleted cannot.

  ANONYMISATION IS AN EXACT GUARANTEE (section 52.2), not a general PII
  scrub: the same document under a different name grades identically. The
  shapes that carry identity whatever the name is -- email, phone, profile
  link -- are removed alongside the name itself.

`employment_gaps` is here for the opposite reason: it is RECORDED AND NEVER
SCORED (sections 8.6 and 40.2), so what is worth pinning is that it reports a
break at all and stays out of the score.

Pure functions over Runbook data. No database, no network, no model.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.hiring import prescreen


# ── The section 6.1 clamp ────────────────────────────────────────────────────


def test_strength_never_exceeds_certainty() -> None:
    """Above 1.00 would mean one piece of evidence counted for more than
    certainty."""
    assert prescreen.clamp_tier_strength(1.9) == 1.00


def test_strength_never_reaches_zero() -> None:
    """The floor is the load-bearing end: a zero-strength claim is deleted from
    the ledger by arithmetic instead of recorded as weak."""
    assert prescreen.clamp_tier_strength(0.0) > 0
    assert prescreen.clamp_tier_strength(-5.0) == prescreen.clamp_tier_strength(0.0)


def test_a_value_already_in_range_is_returned_unchanged() -> None:
    assert prescreen.clamp_tier_strength(0.4) == pytest.approx(0.4)


def test_an_unknown_tier_is_refused_rather_than_defaulted() -> None:
    """A silent default here would score a claim against a tier the Runbook
    does not define, and nothing downstream could tell."""
    with pytest.raises(prescreen.PreScreenUnavailable):
        prescreen.tier_strength("E99")


# ── Section 6.3 decay ────────────────────────────────────────────────────────


def test_an_undated_claim_is_not_decayed() -> None:
    """The anti-age-proxy rule. Guessing a date for an undated line and then
    decaying it is precisely what section 6.3 warns against."""
    assert prescreen.decay_multiplier(None, prescreen.CLOCK_FAST) == 1.0
    assert prescreen.decay_multiplier(None, prescreen.CLOCK_STABLE) == 1.0


def test_a_fresh_claim_is_not_discounted() -> None:
    assert prescreen.decay_multiplier(0.0, prescreen.CLOCK_STABLE) == pytest.approx(1.0)


def test_the_fast_clock_never_discounts_less_than_the_stable_one() -> None:
    """The clocks are the point of having two: a ten-year-old claim in a
    fast-moving domain is worth less than the same claim in a stable one."""
    for age in (3.0, 6.0, 10.0):
        fast = prescreen.decay_multiplier(age, prescreen.CLOCK_FAST)
        stable = prescreen.decay_multiplier(age, prescreen.CLOCK_STABLE)
        assert fast <= stable, age


def test_decay_is_monotonic_with_age() -> None:
    """Older evidence is never worth MORE, on either clock."""
    for clock in (prescreen.CLOCK_FAST, prescreen.CLOCK_STABLE):
        values = [prescreen.decay_multiplier(age, clock) for age in (0.0, 2.0, 5.0, 12.0)]
        assert values == sorted(values, reverse=True), (clock, values)


def test_an_unrecognised_clock_is_treated_as_the_stable_one() -> None:
    """The gentler direction, deliberately: applying the fast clock to a domain
    nobody classified would cost older candidates score on a guess."""
    unknown = prescreen.decay_multiplier(8.0, "not-a-clock")
    stable = prescreen.decay_multiplier(8.0, prescreen.CLOCK_STABLE)
    assert unknown == stable


# ── Section 6.3 clock selection ──────────────────────────────────────────────


def test_no_words_at_all_resolves_to_the_stable_clock() -> None:
    assert prescreen.domain_clock() == prescreen.CLOCK_STABLE
    assert prescreen.domain_clock(None, "") == prescreen.CLOCK_STABLE


def test_an_unclassified_domain_resolves_to_the_stable_clock() -> None:
    assert prescreen.domain_clock("underwater basket weaving") == prescreen.CLOCK_STABLE


# ── Section 52.2 anonymisation ───────────────────────────────────────────────


def test_the_named_identity_is_removed_case_insensitively() -> None:
    cleaned = prescreen.anonymise("ADA Lovelace built it", identities=["Ada Lovelace"])
    assert "ada" not in cleaned.lower()
    assert "built it" in cleaned


def test_the_shapes_that_carry_identity_go_whatever_the_name_is() -> None:
    raw = "Reach me at ada@example.com or +44 7700 900123, linkedin.com/in/ada"
    cleaned = prescreen.anonymise(raw)
    assert "@" not in cleaned
    assert "900123" not in cleaned
    assert "linkedin" not in cleaned.lower()


def test_a_one_character_identity_is_ignored() -> None:
    """Stripping every "A" would gut the document and change the grade, which
    is the opposite of what the guarantee is for."""
    cleaned = prescreen.anonymise("A candidate who shipped an API", identities=["A"])
    assert "candidate who shipped an API" in cleaned


def test_none_and_empty_text_anonymise_to_a_string() -> None:
    assert prescreen.anonymise(None) == ""
    assert prescreen.anonymise("") == ""


def test_the_same_document_under_two_names_anonymises_identically() -> None:
    """The exact property the fairness test rests on."""
    template = "{name} led the migration. Contact {email}."
    first = prescreen.anonymise(
        template.format(name="Ada Lovelace", email="ada@example.com"),
        identities=["Ada Lovelace"],
    )
    second = prescreen.anonymise(
        template.format(name="Grace Hopper", email="grace@example.com"),
        identities=["Grace Hopper"],
    )
    assert first == second


# ── Sections 8.6 and 40.2: recorded, never scored ────────────────────────────


def _span(start: date | None, end: date | None) -> prescreen.EmploymentSpan:
    # `title` is required and is read only to tell a career changer from a
    # domain switcher (section 8.3). It never moves a number, so one constant
    # value keeps these cases about the dates.
    return prescreen.EmploymentSpan(start=start, end=end, title="Engineer")


def test_a_break_between_two_roles_is_reported_in_months() -> None:
    gaps = prescreen.employment_gaps(
        [
            _span(date(2020, 1, 1), date(2021, 1, 1)),
            _span(date(2021, 7, 1), date(2023, 1, 1)),
        ]
    )
    assert gaps == (6,)


def test_continuous_employment_reports_no_gap() -> None:
    gaps = prescreen.employment_gaps(
        [
            _span(date(2020, 1, 1), date(2021, 1, 1)),
            _span(date(2021, 1, 1), date(2023, 1, 1)),
        ]
    )
    assert gaps == ()


def test_undated_spans_are_ignored_rather_than_guessed_at() -> None:
    """A span with no dates cannot evidence a break, and inventing one would
    put an unfounded question in the dossier."""
    gaps = prescreen.employment_gaps(
        [_span(None, None), _span(date(2020, 1, 1), None), _span(None, date(2021, 1, 1))]
    )
    assert gaps == ()


def test_spans_given_out_of_order_are_sorted_before_measuring() -> None:
    """A resume rarely lists roles oldest-first, and reading them in file order
    would report a negative gap as a positive one."""
    gaps = prescreen.employment_gaps(
        [
            _span(date(2022, 1, 1), date(2023, 1, 1)),
            _span(date(2020, 1, 1), date(2021, 1, 1)),
        ]
    )
    assert gaps == (12,)
