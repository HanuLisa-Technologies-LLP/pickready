"""Siddhi's report-writing layer: the PRISM Report, and the rules it cannot break.

    citations   the structural guarantee that no delivered statement is uncited
    evidence    the citable node set a report is written against
    synthesis   the generator, which assembles statements and renders them
    numbers     the serialiser-level number ban (spec-doc6 D8)
    delivery    gate G4, then every export format, then the bytes

Tatva Assessment is the PROCESS. The PRISM Report is the DOCUMENT it produces.
Neither name is ever used for the other, here or in any string this package
writes.

The PRISM Report's structure, header, section order and three-chart rule are
unchanged and live where they always have: `report_pdf.SECTION_ORDER`,
`report_pdf.RENDERED_CHART_KEYS` and `components/functional-skills-report.tsx`.
A report is immutable, so those rules have to hold for a report written a year
ago as well as one written today, which is why they sit at the RENDERER and not
here. This package holds what spec-doc5 §A.3 and spec-doc6 §4.5 add: citation
enforcement implemented "in code, not in a prompt", and a number ban implemented
at the serialiser rather than at the generator.
"""
