"""Report Library (Master Directive Part 4).

A catalogue of pre-defined, downloadable reports generated server-side from
stored data. Explicitly NOT a live dashboard: the client receives a completed
file (Part 4 section 1, "What it is NOT").

- `catalogue` holds every report the directive's Part 4 tables define, as
  data, with role access and an `implemented` flag the UI can grey out on.
- `engine` compiles one report on demand and returns the finished bytes.
"""
from app.services.reports.catalogue import (  # noqa: F401
    CATALOGUE,
    CATEGORIES,
    ReportDefinition,
    definition_for,
    visible_to,
)
from app.services.reports.engine import (  # noqa: F401
    ReportComingSoon,
    ReportNotImplemented,
    UnknownReport,
    generate,
)
