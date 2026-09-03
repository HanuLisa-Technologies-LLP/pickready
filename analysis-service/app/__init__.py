"""ReadyPick analysis service: speaker counting and an AI-text estimate.

A separate image from the backend because the model libraries it carries
(torch, pyannote.audio, transformers) are several hundred megabytes the API
and the worker have no use for, and because the audio it handles must never
sit next to code that persists files.
"""
