"""AI-generated-text estimate over one answer. INFORMATIONAL ONLY.

READ THIS BEFORE USING THE NUMBER (proctoring spec section 3.5)
--------------------------------------------------------------
`openai-community/roberta-base-openai-detector` was trained to tell GPT-2
output from human text. Against current-generation language models it is
UNRELIABLE, and the specification says what that means for the product: the
probability this module returns "must never contribute to a warning, a
termination, a score, or a ranking". It is a soft observation for the report,
worded with hedges, and nothing else. The response carries that sentence so a
future caller reading the JSON is told the same thing the code is.

The endpoint is behind `AI_TEXT_ENABLED`, default false. The interface exists
whether or not the model is loaded, which is the shape the specification asks
for: "implement the interface and disable the feature behind a flag rather
than shipping a bad one".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings

__all__ = [
    "AI_TEXT_NOTE",
    "AiTextResult",
    "AiTextUnavailable",
    "Detector",
    "TransformersDetector",
    "ai_label_index",
    "detect",
    "load_detector",
]

#: Travels with every /ai-text response. The wording is the specification's.
AI_TEXT_NOTE = (
    "Informational only. This detector is unreliable against current language "
    "models; the probability must never contribute to a warning, a termination, "
    "a score or a ranking."
)


class AiTextUnavailable(RuntimeError):
    """The detector cannot be loaded. The message is what `/health` reports."""


@dataclass(frozen=True)
class AiTextResult:
    probability_ai: float
    model: str


class Detector(Protocol):
    """What `detect` needs: a probability in [0, 1] that `text` was machine-written."""

    def __call__(self, text: str) -> float: ...


def ai_label_index(id2label: dict[Any, str]) -> int:
    """Which output index means "machine-written", read from the model config.

    The detector labels its classes `Fake` and `Real`. Read by name rather than
    assumed to be index 0, because a config that ordered them the other way
    would otherwise invert every probability while looking perfectly healthy.
    """
    matches = [int(index) for index, label in id2label.items() if label.strip().lower() == "fake"]
    if len(matches) != 1:
        raise AiTextUnavailable(
            f"unavailable: the model config does not name exactly one 'Fake' label "
            f"(id2label={id2label!r})"
        )
    return matches[0]


class TransformersDetector:
    """A sequence classifier and its tokenizer, called on one string at a time."""

    def __init__(self, tokenizer: Any, model: Any, ai_index: int, max_tokens: int) -> None:
        self._tokenizer = tokenizer
        self._model = model
        self._ai_index = ai_index
        self._max_tokens = max_tokens

    def __call__(self, text: str) -> float:
        import torch

        encoded = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=self._max_tokens,
        )
        with torch.inference_mode():
            logits = self._model(**encoded).logits
        probabilities = torch.softmax(logits, dim=-1)[0]
        return float(probabilities[self._ai_index].item())


def load_detector(settings: Settings) -> Detector:
    """Load the pinned detector from the offline cache, or raise `AiTextUnavailable`."""
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as error:
        raise AiTextUnavailable(f"unavailable: {error}") from error

    if settings.torch_threads:
        torch.set_num_threads(settings.torch_threads)

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            settings.ai_text_model,
            revision=settings.ai_text_revision,
            cache_dir=settings.model_cache_dir,
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            settings.ai_text_model,
            revision=settings.ai_text_revision,
            cache_dir=settings.model_cache_dir,
        )
    except Exception as error:
        raise AiTextUnavailable(
            f"unavailable: {settings.ai_text_model_id} is not in the model cache "
            f"({settings.model_cache_dir}); run scripts/download_models.py "
            f"({type(error).__name__}: {error})"
        ) from error

    model.eval()
    return TransformersDetector(
        tokenizer=tokenizer,
        model=model,
        ai_index=ai_label_index(model.config.id2label),
        max_tokens=settings.ai_text_max_tokens,
    )


def detect(text: str, detector: Detector, settings: Settings) -> AiTextResult:
    """The estimate for `text`, clamped to [0, 1] and labelled with the model id."""
    probability = min(1.0, max(0.0, detector(text)))
    return AiTextResult(probability_ai=probability, model=settings.ai_text_model_id)
