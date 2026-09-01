"""Project Evidence Intelligence (master brief, 2026-09-01).

Candidate project submissions are processed into structured, decomposed,
provenance-carrying evidence; the ORIGINAL artifacts are temporary and are
deleted once the derived evidence is validated and persisted. The subsystem is
a contextual evidence source for the wider candidate intelligence, never a
second scoring engine.

Module map (clean boundaries, one modular package -- not microservices):

    intake          upload/URL validation, temporary staging, limits
    formats         file classification: family, parser, supported
    archive_safety  zip inspection before any extraction
    parsers         deterministic extraction -> ParsedArtifact
    repository      public-repository ingestion (provider registry)
    evidence        evidence units, dedupe, rank, reduce, the Evidence Record
    ai_reasoning    ONE reasoning call over the reduced pack, validated
    pipeline        lifecycle orchestration, idempotency, deletion
    context         consumption interfaces (recruiter view, AI context block)
"""
