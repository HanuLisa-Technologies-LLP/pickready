# syntax=docker/dockerfile:1.7
# The analysis service: speaker counting and an AI-text estimate for the
# proctoring pipeline. Its own image, apart from the backend, because the
# model libraries are several hundred megabytes the API and the worker never
# load, and because the one media type that leaves a candidate's browser is
# handled here and nowhere else.
#
# THE MODELS ARE BAKED AT BUILD, AND THE TOKEN THAT FETCHES THEM IS NOT.
# The two pyannote repositories are gated on Hugging Face. The token arrives
# as a BuildKit secret (`--secret id=huggingface_token,env=HUGGINGFACE_TOKEN`)
# mounted for exactly one RUN and written to no layer: never an ARG, never an
# ENV, because `docker history` prints both. At runtime the hub client is
# OFFLINE against /models, so the container fetches nothing and has no reason
# to reach the internet at all.

# ---------------------------------------------------------------------------
# builder: wheels only. Nothing here reaches the runtime image except /wheels.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt requirements-web.txt requirements-models.txt ./
# The extra index is where the CPU build of torch lives. On x86_64 it is
# ~200 MB against several GB for the CUDA build PyPI serves; on aarch64 both
# indexes carry the same CPU-only wheel.
RUN pip wheel --wheel-dir /wheels \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg supplies the shared libraries torchcodec decodes WebM/Opus and WAV
# with; curl is for the container health probe. No compiler.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

COPY app ./app
COPY scripts ./scripts

# ── Model download, with the token mounted and never stored ──────────────────
#
# ALLOW_MISSING_HUGGINGFACE_TOKEN is for a local compose build with no token,
# which produces an image whose /health says diarization is unavailable. CI
# leaves it false, so an image missing the gated models cannot be pushed.
ARG ALLOW_MISSING_HUGGINGFACE_TOKEN=false
ENV MODEL_CACHE_DIR=/models \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1
RUN --mount=type=secret,id=huggingface_token,required=false \
    if [ "$ALLOW_MISSING_HUGGINGFACE_TOKEN" = "true" ]; then \
      python scripts/download_models.py --cache-dir /models --allow-missing-token; \
    else \
      python scripts/download_models.py --cache-dir /models; \
    fi \
    && chmod -R a+rX /models

# ── Runtime posture ──────────────────────────────────────────────────────────
#
# OFFLINE from here on: every weight is already under /models, and a load that
# finds one missing reports it at /health instead of downloading. pyannote 4
# ships usage telemetry to its vendor; a proctoring service reports nothing to
# anyone. The remaining paths give the libraries' import-time caches somewhere
# other than the source tree, which stays root-owned and read-only to the
# service user.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PYANNOTE_METRICS_ENABLED=false \
    MPLCONFIGDIR=/tmp/matplotlib \
    TORCH_HOME=/tmp/torch \
    XDG_CACHE_HOME=/tmp/cache

RUN useradd --create-home --uid 10001 analysis
USER analysis

# Documentation only. The real port is $PORT, honoured below; compose and the
# ECS task definition both use 8100.
EXPOSE 8100

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8100}"]
