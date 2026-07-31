"""The candidate's structured profile form — the 40 validation aspects, moved
out of the per-job assessment conversation and onto the candidate's own profile
(client decision, 2026-07-27).

Why this exists
---------------
The 40 validation questions are the same question-and-answer data for a given
candidate regardless of which job they apply to, so asking them inside every
job's assessment conversation re-asked identical questions once per application.
They now live on the candidate profile as an advanced form, filled in once and
editable at any time, and every application snapshots the answers.

This module is the single source of truth for that form. It is a FIXED Python
constant — never LLM-generated, never client-editable — exactly as
`services/pfi_bank.py` is for the behavioural bank. The wording, numbering,
option lists and section order mirror the supplied candidate questionnaire.

Numbering note: the source questionnaire numbers its items 1 and 20-39, with
the education table occupying 2-19 and no item 36. `display_no` preserves the
questionnaire's own numbers so a candidate reading the form sees what they were
given; `key` is the stable machine name and is what everything else keys on.
"""
from __future__ import annotations

from typing import Any, Literal

FieldType = Literal[
    "text", "textarea", "date", "radio", "checkbox_group", "checkbox", "education_table"
]


class FormField:
    """One answerable item on the profile form."""

    __slots__ = ("key", "label", "type", "display_no", "hint", "options", "required", "rows", "columns")

    def __init__(
        self,
        key: str,
        label: str,
        type: FieldType = "text",
        *,
        display_no: int | None = None,
        hint: str | None = None,
        options: tuple[str, ...] = (),
        required: bool = False,
        rows: tuple[tuple[str, str], ...] = (),
        columns: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.key = key
        self.label = label
        self.type = type
        self.display_no = display_no
        self.hint = hint
        self.options = options
        self.required = required
        self.rows = rows
        self.columns = columns

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
        }
        if self.display_no is not None:
            payload["display_no"] = self.display_no
        if self.hint:
            payload["hint"] = self.hint
        if self.options:
            payload["options"] = list(self.options)
        if self.rows:
            payload["rows"] = [{"key": k, "label": v} for k, v in self.rows]
        if self.columns:
            payload["columns"] = [{"key": k, "label": v} for k, v in self.columns]
        return payload


class FormSection:
    __slots__ = ("key", "title", "description", "fields")

    def __init__(self, key: str, title: str, fields: list[FormField], description: str | None = None) -> None:
        self.key = key
        self.title = title
        self.description = description
        self.fields = fields

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "title": self.title,
            "fields": [f.as_dict() for f in self.fields],
        }
        if self.description:
            payload["description"] = self.description
        return payload


FORM_INSTRUCTIONS: tuple[str, ...] = (
    "All sections are mandatory unless marked Optional.",
    "Mention NA if any field is not applicable.",
    "Date format: DD / MM / YYYY.",
    "CTC format: Annual, in Indian Rupees.",
)

EDUCATION_ROWS: tuple[tuple[str, str], ...] = (
    ("class_x", "Class X"),
    ("class_xii", "Intermediate (Class XII)"),
    ("graduation", "Graduation"),
    ("post_graduation", "Post Graduation"),
    ("others", "Others"),
    ("certifications", "Certification(s)"),
    ("diploma", "Diploma / Vocational"),
)

EDUCATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("course", "Course / Specialisation"),
    ("year_of_passing", "Year of Passing"),
    ("institute", "Institute, Location"),
    ("score", "Score / Grade / CGPA"),
)

NOTICE_PERIOD_OPTIONS: tuple[str, ...] = (
    "Immediately Available (0 days)",
    "Maximum of 15 Days",
    "Maximum of 30 Days",
    "Maximum of 45 Days",
    "Maximum of 60 Days",
    "Maximum of 90 Days",
    "Other",
)

_AVAILABLE_FOR_VERIFICATION = ("Available for Verification", "Not Available")
_YES_NO = ("Yes", "No")


FORM_SECTIONS: list[FormSection] = [
    FormSection(
        "personal",
        "Personal Details",
        [
            FormField("current_city", "Current City of Residence", display_no=1, required=True),
        ],
    ),
    FormSection(
        "education",
        "Educational Qualifications",
        [
            FormField(
                "education",
                "Education",
                "education_table",
                hint="Complete all rows that apply and leave the rest blank.",
                rows=EDUCATION_ROWS,
                columns=EDUCATION_COLUMNS,
            ),
        ],
        description="Complete all fields and leave blank if not applicable.",
    ),
    FormSection(
        "experience",
        "Work Experience",
        [
            FormField(
                "total_experience",
                "Total Years of Experience",
                display_no=20,
                hint="For example: 3 Years 4 Months, or Fresher.",
                required=True,
            ),
            FormField("last_company_name", "Last / Current Company Name", display_no=21),
            FormField("last_company_location", "Last / Current Company Location", display_no=22),
            FormField("last_designation", "Last / Current Designation or Job Title", display_no=23),
            FormField("date_of_joining", "Date of Joining at Last / Current Company", "date", display_no=24),
            FormField("date_of_leaving", "Date of Leaving / Last Working Day", "date", display_no=25,
                      hint="Leave blank if you are currently employed."),
            FormField("currently_employed", "I am currently employed", "checkbox"),
        ],
    ),
    FormSection(
        "compensation",
        "Compensation & Availability",
        [
            FormField("current_ctc", "Present or Last Drawn Annual CTC", display_no=26),
            FormField("expected_ctc", "Expected Annual CTC", display_no=27),
            FormField("notice_period", "Notice Period", "radio", display_no=28,
                      options=NOTICE_PERIOD_OPTIONS),
            FormField("notice_period_other", "If Other, please specify",
                      hint="Only needed when Notice Period is set to Other."),
            FormField("shift_preference", "Shift Preference", "checkbox_group", display_no=29,
                      options=("Day Shift", "Night Shift", "Rotational Shift", "As per Requirement")),
            FormField("work_mode", "Preferred Work Mode", "checkbox_group", display_no=30,
                      options=("Work from Office (WFO)", "Work from Home (WFH)",
                               "Hybrid (combination of WFO and WFH)", "As per Requirement")),
            FormField("job_seeking_status", "Current Job-Seeking Status", "checkbox_group", display_no=31,
                      options=("Actively Looking for a New Role", "Open to the Right Opportunity",
                               "Not Currently Looking, Applying Speculatively")),
            FormField("bgv_consent", "Willingness to Undergo Background Verification (BGV)", "radio",
                      display_no=32,
                      hint="A BGV check may be conducted by the employer prior to onboarding.",
                      options=("Yes, I consent to a Background Verification check", "No")),
        ],
    ),
    FormSection(
        "documents",
        "Document Availability & Onboarding Readiness",
        [
            FormField("doc_aadhaar", "Original Aadhaar Card", "radio", display_no=33,
                      options=_AVAILABLE_FOR_VERIFICATION),
            FormField("doc_pf_account", "Provident Fund (PF) Account Number", "radio", display_no=34,
                      options=("Available", "Not Available")),
            FormField("doc_pay_slip", "Most Recent Pay Slip", "radio", display_no=35,
                      options=_AVAILABLE_FOR_VERIFICATION),
            FormField("doc_academic_certificates", "Original Academic Certificates (All levels)", "radio",
                      display_no=37, options=_AVAILABLE_FOR_VERIFICATION),
            FormField("resignation_acceptance",
                      "Can you submit your resignation letter acceptance copy or email upon issuing the offer letter?",
                      "radio", display_no=38, options=_YES_NO),
        ],
        description="These responses indicate how quickly you can be onboarded once selected. Please answer accurately.",
    ),
    FormSection(
        "resume",
        "Curriculum Vitae (CV) / Resume",
        [
            FormField("cv_updated_recently", "I confirm that my main resume is updated within the last 30 days",
                      "checkbox", display_no=39),
        ],
        description="Your main resume is managed above and is reused every time you apply.",
    ),
    FormSection(
        "declaration",
        "Candidate Declaration",
        [
            FormField(
                "declaration_accepted",
                "I confirm that all information provided is accurate, complete, and true to the best of my "
                "knowledge, and I consent to my profile being shared with prospective employers for job matching.",
                "checkbox",
                required=True,
            ),
            FormField("declaration_full_name", "Full Name", required=True),
        ],
    ),
]


def form_definition() -> dict[str, Any]:
    """The whole form as a JSON-serializable definition for the portal UI."""
    return {
        "instructions": list(FORM_INSTRUCTIONS),
        "sections": [section.as_dict() for section in FORM_SECTIONS],
    }


ALL_FIELDS: dict[str, FormField] = {
    field.key: field for section in FORM_SECTIONS for field in section.fields
}

REQUIRED_FIELD_KEYS: tuple[str, ...] = tuple(
    key for key, field in ALL_FIELDS.items() if field.required
)

#: Every key the form may legitimately store. Anything else submitted is dropped
#: rather than persisted, so a stale or hostile client cannot grow the blob.
ALLOWED_KEYS: frozenset[str] = frozenset(ALL_FIELDS)


def _clean_education(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    row_keys = {key for key, _ in EDUCATION_ROWS}
    column_keys = {key for key, _ in EDUCATION_COLUMNS}
    cleaned: dict[str, dict[str, str]] = {}
    for row_key, row in value.items():
        if row_key not in row_keys or not isinstance(row, dict):
            continue
        cells = {
            column: str(row[column]).strip()[:255]
            for column in column_keys
            if isinstance(row.get(column), (str, int, float)) and str(row[column]).strip()
        }
        if cells:
            cleaned[row_key] = cells
    return cleaned


def clean_answers(raw: Any) -> dict[str, Any]:
    """Normalise a submitted form payload into what we are willing to store.

    Unknown keys are dropped, scalars are coerced and length-capped, and
    checkbox groups keep only options the form actually offers. This runs before
    persistence so `candidates.profile_form_json` always matches the definition
    above — the report and the matching pipeline read it directly.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        field = ALL_FIELDS.get(key)
        if field is None:
            continue
        if field.type == "education_table":
            education = _clean_education(value)
            if education:
                cleaned[key] = education
        elif field.type == "checkbox":
            cleaned[key] = bool(value)
        elif field.type == "checkbox_group":
            if isinstance(value, list):
                chosen = [str(item) for item in value if str(item) in field.options]
                if chosen:
                    cleaned[key] = chosen
        elif field.type == "radio":
            text = str(value).strip() if value is not None else ""
            if text in field.options:
                cleaned[key] = text
        else:
            text = str(value).strip() if value is not None else ""
            if text:
                cleaned[key] = text[:2000]
    return cleaned


def missing_required(answers: dict[str, Any]) -> list[str]:
    """Required keys the candidate has not answered yet."""
    missing: list[str] = []
    for key in REQUIRED_FIELD_KEYS:
        value = answers.get(key)
        if value in (None, "", [], {}) or value is False:
            missing.append(key)
    return missing


def is_complete(answers: dict[str, Any] | None) -> bool:
    return bool(answers) and not missing_required(answers)


def searchable_text(answers: dict[str, Any] | None) -> str:
    """Flatten the form into one string for keyword relevance ranking.

    Only free-text and selected-option values contribute; booleans and the
    declaration are noise for matching purposes.
    """
    if not answers:
        return ""
    parts: list[str] = []
    for key, value in answers.items():
        if key.startswith("declaration") or key.startswith("doc_"):
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):  # education table
            for row in value.values():
                if isinstance(row, dict):
                    parts.extend(str(cell) for cell in row.values())
    return " ".join(part for part in parts if part).strip()


# Import-time integrity checks — these are a product contract.
assert len(ALL_FIELDS) == sum(len(section.fields) for section in FORM_SECTIONS), (
    "duplicate field key across profile-form sections"
)
assert "current_ctc" in ALL_FIELDS and "expected_ctc" in ALL_FIELDS
assert "notice_period" in ALL_FIELDS
