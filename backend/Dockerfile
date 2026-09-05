# Shared image for the backend API, the Celery worker, beat and migrations
# (ESD 15). One image, four entrypoints, so dependency drift between them is
# impossible. The role is chosen by the container args: see
# docker-entrypoint.sh.

# ---------------------------------------------------------------------------
# builder: compiles wheels. build-essential and libpq-dev are ~300 MB of
# toolchain that production must never ship, so they stay in this stage and
# only the built wheels cross into the runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libpq5 is the runtime half of libpq-dev; curl is kept for container-level
# health probing. Neither pulls in a compiler.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

# NO MODEL WEIGHTS ARE BAKED IN ANY MORE (spec-doc5 Part B).
#
# This step used to fetch `BAAI/bge-small-en-v1.5` at build time, because a
# runtime download made the first AI Reach request slow and failed outright on
# an egress-restricted revision. Both problems belonged to running a model
# locally; every embedding now goes to voyage-context-4 over HTTPS, so there is
# nothing to cache. The image is correspondingly smaller and the build no longer
# depends on a model host being reachable from CI.

COPY . .

# Normalise the entrypoint's line endings before it is ever executed. The repo
# is developed on Windows, and a CRLF shebang fails as "/bin/sh\r: not found",
# which reads as a missing file rather than a line-ending problem. .gitattributes
# pins LF at checkout; this makes the image correct even from a stray context.
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh \
    && chmod +x /app/docker-entrypoint.sh

# A task that handles anything derived from a job description or an uploaded
# resume should not do it as uid 0. Neither ECS nor Lambda reads a compose file
# to select a user, so the image selects one itself.
RUN useradd --create-home --uid 10001 pickready \
    && chown -R pickready:pickready /app

USER pickready

# Documentation only. The real port is $PORT, injected by Cloud Run and honoured
# by the entrypoint; compose keeps publishing 8000.
EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
