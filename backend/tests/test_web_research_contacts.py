"""AI Reach finds the company's own published hiring mailbox, or nothing.

WHAT WAS WRONG (2026-09-11)
----------------------------
Every AI Reach card said there was no public contact, for every company,
including ones whose careers page prints `careers@<company>` in plain text.
Two separate defects wore one symptom, and only one of them is this file's
subject.

The evaluator is rightly FORBIDDEN from inferring a contact, and a search
snippet almost never contains one, so the model had nothing to report and
correctly reported nothing. The pages themselves were already in hand. Reading
them again deterministically can only find what a page actually published,
which meets the never-infer bar by construction rather than by instruction.

(The other defect was that the official-site link WAS being rendered, in
`text-brand-700` on a dark surface, which is dark navy on dark navy. A link
nobody can see and a link that does not exist are the same bug report.)

THE THREE GUARDS, AND WHY EACH IS LOAD-BEARING
------------------------------------------------
  * REGISTRABLE-DOMAIN MATCH. A page about one employer routinely prints a job
    board's own mailbox. Attributing `hr@some-jobsite.example` to the employer
    would be a fabricated contact detail with a real source URL under it, which
    is worse than an empty state because it looks verified.
  * A ROLE-MAILBOX ALLOWLIST. A personal address found on a page is not a
    professional contact, and surfacing it would be a privacy failure wearing a
    feature's clothes. Only the kind of address a company prints FOR applicants
    is ever attached.
  * THE SOURCE URL IS THE PAGE THE ADDRESS WAS ON. A rep can check the claim in
    one click, which is the same bar the evaluator is held to.
"""
from __future__ import annotations

from app.services import web_research


def _hit(url: str, content: str) -> dict:
    return {"url": url, "content": content, "title": "Careers"}


def _company(url: str | None, **extra) -> dict:
    return {"company_name": "Example", "company_url": url, **extra}


# ── The registrable domain ───────────────────────────────────────────────────


def test_a_subdomain_belongs_to_the_same_employer() -> None:
    """`careers.google.com` and `google.com` are one company, not two."""
    assert web_research._registrable("careers.google.com") == "google.com"
    assert web_research._registrable("www.google.com") == "google.com"
    assert web_research._registrable("google.com") == "google.com"


def test_a_two_part_country_suffix_keeps_one_more_label() -> None:
    """India is the product's home market, so `.co.in` is the normal case
    rather than an exotic one. Trimming to two labels would make every
    `example.co.in` company the same company as every other."""
    assert web_research._registrable("jobs.example.co.in") == "example.co.in"
    assert web_research._registrable("example.co.in") == "example.co.in"
    assert web_research._registrable("example.ac.in") == "example.ac.in"


def test_two_different_companies_do_not_collapse() -> None:
    assert web_research._registrable("acme.co.in") != web_research._registrable(
        "globex.co.in"
    )


# ── Harvesting ───────────────────────────────────────────────────────────────


def test_a_published_hiring_mailbox_is_attached_with_its_page() -> None:
    evaluated = [_company("https://www.cybotrix.com/")]
    hits = [
        _hit(
            "https://www.cybotrix.com/careers",
            "Open roles. Write to hr@cybotrix.com with your resume.",
        )
    ]
    web_research.harvest_contacts(evaluated, hits)
    assert evaluated[0]["contact_email"] == "hr@cybotrix.com"
    assert evaluated[0]["contact_source_url"] == "https://www.cybotrix.com/careers"


def test_a_mailbox_on_another_domain_is_never_attributed_to_this_company() -> None:
    """The job board's own address, on a page about this employer. Attaching it
    would produce a fabricated contact with a genuine-looking source."""
    evaluated = [_company("https://www.cybotrix.com/")]
    hits = [
        _hit(
            "https://jobs.example-board.test/cybotrix",
            "Cybotrix is hiring. Questions: hr@example-board.test",
        )
    ]
    web_research.harvest_contacts(evaluated, hits)
    assert "contact_email" not in evaluated[0]


def test_a_personal_address_is_left_alone() -> None:
    """Right domain, wrong kind of address. A named person's mailbox printed on
    a page is not a hiring contact, and publishing it would be scraping."""
    evaluated = [_company("https://www.cybotrix.com/")]
    hits = [
        _hit(
            "https://www.cybotrix.com/team",
            "Reach our CTO directly at john.doe@cybotrix.com",
        )
    ]
    web_research.harvest_contacts(evaluated, hits)
    assert "contact_email" not in evaluated[0]


def test_a_mailbox_on_a_subdomain_of_the_company_counts() -> None:
    evaluated = [_company("https://google.com/about")]
    hits = [_hit("https://careers.google.com/", "Apply: jobs@careers.google.com")]
    web_research.harvest_contacts(evaluated, hits)
    assert evaluated[0]["contact_email"] == "jobs@careers.google.com"


def test_a_contact_the_evaluator_verified_is_never_overwritten() -> None:
    """The model read a page and reported what it said. A regex is not entitled
    to a second opinion about that."""
    evaluated = [
        _company(
            "https://www.cybotrix.com/",
            contact_email="talent@cybotrix.com",
            contact_source_url="https://www.cybotrix.com/contact",
        )
    ]
    hits = [_hit("https://www.cybotrix.com/careers", "hr@cybotrix.com")]
    web_research.harvest_contacts(evaluated, hits)
    assert evaluated[0]["contact_email"] == "talent@cybotrix.com"
    assert evaluated[0]["contact_source_url"] == "https://www.cybotrix.com/contact"


def test_an_address_with_no_page_behind_it_is_not_attached() -> None:
    """`contact_source_url` is not optional. An address a rep cannot check is
    an assertion, and this feature does not make assertions."""
    evaluated = [_company("https://www.cybotrix.com/")]
    hits = [{"url": "", "content": "careers@cybotrix.com", "title": "x"}]
    web_research.harvest_contacts(evaluated, hits)
    assert "contact_email" not in evaluated[0]


def test_a_company_with_no_site_is_skipped_rather_than_guessed_at() -> None:
    """With no `company_url` there is no domain to match against, so there is
    no way to tell whose mailbox an address is."""
    evaluated = [_company(None)]
    hits = [_hit("https://somewhere.test/", "hr@somewhere.test")]
    web_research.harvest_contacts(evaluated, hits)
    assert "contact_email" not in evaluated[0]


def test_no_hits_and_no_candidates_are_both_no_ops() -> None:
    assert web_research.harvest_contacts([], []) == []
    evaluated = [_company("https://www.cybotrix.com/")]
    assert web_research.harvest_contacts(evaluated, []) is evaluated
    assert "contact_email" not in evaluated[0]
