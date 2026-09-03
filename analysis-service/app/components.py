"""The two loaded models and the status word `/health` reports for each.

Loading never raises. A model that cannot be loaded is a status string
beginning with `unavailable:` and naming the reason, because the container
must come up and SAY why diarization is off rather than crash-loop with the
reason in a log nobody is reading. The backend treats an unavailable analysis
service as "audio monitoring unavailable" on the report, which is a true
statement; a service that was silently absent would leave the report claiming
nothing was heard.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai_text import AiTextUnavailable, Detector, load_detector
from app.config import Settings
from app.diarization import DiarizationUnavailable, Pipeline, load_pipeline

__all__ = ["AVAILABLE", "DISABLED", "Components", "load_components"]

AVAILABLE = "available"
DISABLED = "disabled"


@dataclass
class Components:
    diarization_pipeline: Pipeline | None
    diarization_status: str
    ai_text_detector: Detector | None
    ai_text_status: str

    @property
    def healthy(self) -> bool:
        """Every component that is meant to be on, is. Disabled is not a fault."""
        return self.diarization_status == AVAILABLE and self.ai_text_status in (AVAILABLE, DISABLED)


def load_components(settings: Settings) -> Components:
    """Load what the settings ask for and record what happened to each."""
    try:
        pipeline: Pipeline | None = load_pipeline(settings)
        diarization_status = AVAILABLE
    except DiarizationUnavailable as error:
        pipeline = None
        diarization_status = str(error)

    detector: Detector | None = None
    if settings.ai_text_enabled:
        try:
            detector = load_detector(settings)
            ai_text_status = AVAILABLE
        except AiTextUnavailable as error:
            ai_text_status = str(error)
    else:
        ai_text_status = DISABLED

    return Components(
        diarization_pipeline=pipeline,
        diarization_status=diarization_status,
        ai_text_detector=detector,
        ai_text_status=ai_text_status,
    )
