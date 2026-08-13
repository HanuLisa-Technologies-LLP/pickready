"""The mock-data seeder's pure helpers.

The seeder is a dev tool, but it writes the shape the report API reads, so a
drift between the two shows up as a demo that renders blank rather than as a
test failure. These cover the parts that are pure and cheap to call.
"""
from collections import Counter

import pytest

from app.services.functional_assessment import word_count


@pytest.mark.parametrize(
    "skills",
    [
        ["Python", "SQL", "Kafka", "Docker", "Terraform", "Go", "Redis", "gRPC"],
        ["Python", "SQL", "Kafka"],
        ["Excel"],
        [],
    ],
)
def test_a_seeded_framework_always_meets_the_minimum(skills):
    """Five per category is a product contract. A short JD must cycle its own
    skills rather than emit a short framework, and must never loop forever
    doing it."""
    import app.scripts.seed_mock_data as m

    framework = m.seed_framework(skills, "Backend Engineer")
    counts = Counter(row["category"] for row in framework)
    assert counts["must_have"] == 5
    assert counts["nice_to_have"] == 5
    assert counts["behavioural"] == 5
    for category in ("must_have", "nice_to_have"):
        names = [row["name"] for row in framework if row["category"] == category]
        assert len(set(names)) == 5, names


def test_seeded_report_rows_match_the_report_contract():
    import app.scripts.seed_mock_data as m

    framework = m.seed_framework(["Python", "SQL", "Kafka"], "Backend Engineer")
    rows = m.ppi_dimensions(framework, ["Python"], "abc")
    assert len(rows) == 15
    # 45-50 words per PPI remark (spec §10.5).
    assert all(45 <= word_count(row["remark"]) <= 50 for row in rows)
    # Every row carries the job's requirement so the radar can plot both shapes.
    assert all(row["required_level"] for row in rows)
    # Ordinals restart per category, matching the real synthesis path.
    assert [row["ordinal"] for row in rows if row["category"] == "behavioural"] == [1, 2, 3, 4, 5]


def test_seeded_validation_matches_the_application_field_list():
    import app.scripts.seed_mock_data as m
    from app.services.application_validation import MANDATORY_KEYS

    validation = m.build_validation({"current_ctc": "18 LPA"}, "Asha")
    assert validation["captured"] is True
    assert [field["key"] for field in validation["fields"]] == list(MANDATORY_KEYS)
    # Captured, never rated (spec §7).
    assert "score" not in validation and "grade" not in validation
