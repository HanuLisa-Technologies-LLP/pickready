"""Siddhi's report-writing layer: the PRISM Report, and the rules it cannot break.

    citations     the structural guarantee: nothing uncited is rendered as cited
    evidence      the citable node set a report is written against, with locators
    synthesis     assembles every statement (unrendered) from the rated rows
    report        THE composer: render the cited, withhold the rest, check support
    support       does the cited text support the statement, not merely exist
    remarks       the 45-50 word prose beside every grade, and how it was written
    quality_gate  the composed report against the grades Miti decided
    ai_score      Yukti's pre-assessment snapshot, checked and frozen
    trail         the citation trail's read model, served behind the transcript
    inputs        Siddhi's inputs from what the pipeline already read
    provenance    the two facts Siddhi records about how text was made
    numbers       the serialiser-level number ban (spec-doc6 D8)
    delivery      gate G4, then the PDF
    claim_evidence, validation_points
                  the two 0107 sections, built for the composer

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

The pipeline runs one way: Siddhi may read Miti's result TYPES, and nothing in
this package imports the scoring orchestrator or the persistence stage.
"""
