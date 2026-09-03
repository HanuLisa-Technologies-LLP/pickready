# ReadyPick analysis service

Speaker counting and an AI-text estimate for the proctoring pipeline. A
FastAPI service on `python:3.12-slim`, called only by the backend over the
private network, never by a browser.

Read `claude.md` at the repository root before changing anything here. The
rules that bind this directory most: audio is never written anywhere, the
AI-text number is informational only, no em dash, no placeholder prose, every
threshold from the environment with a documented default.

## Endpoints

| Route | Request | Response |
|---|---|---|
| `POST /diarize` | multipart field `chunk`, `audio/webm` or `audio/wav`, at most `MAX_CHUNK_BYTES` | `{"speaker_count": int, "speech_seconds": float}` |
| `POST /ai-text` | `{"text": str}` | `{"probability_ai": float, "model": str, "note": str}` |
| `GET /health` | | `{"status": "ok" or "degraded", "diarization": ..., "ai_text": ...}` |

`/diarize` answers 503 with the same wording `/health` carries when the
pipeline is not loaded, 415 for any other media type, 413 for a chunk over the
byte or duration ceiling, and 422 for bytes that do not decode. The backend
treats 503 as "audio monitoring unavailable" and says so on the report; it
never reads an unavailable service as "one speaker".

`/ai-text` is disabled unless `AI_TEXT_ENABLED=true`, and every response
carries the sentence in `app/ai_text.py`: the detector is unreliable against
current language models and the probability is informational only. It never
contributes to a warning, a termination, a score or a ranking.

## The audio never touches a disk

A chunk arrives as bytes, is wrapped in an `io.BytesIO`, decoded by torchcodec
from that buffer, and the buffer is closed and deleted before the model runs.
`tests/test_no_disk.py` makes every write path raise during a real request
(`open` in a write mode, `os.open` with a write flag, all of `tempfile`,
`Path.write_*`, `shutil`) and requires the request to still answer, and reads
the service source to refuse those names outright. There is no log line, no
metric and no cache that carries audio either.

## Models

| Model | Revision | Licence | Gated |
|---|---|---|---|
| `pyannote/speaker-diarization-3.1` | `84fd25912480287da0247647c3d2b4853cb3ee5d` | MIT | yes |
| `pyannote/segmentation-3.0` | `e66f3d3b9eb0873085418a7b813d3b369bf160bb` | MIT | yes |
| `pyannote/wespeaker-voxceleb-resnet34-LM` | `837717ddb9ff5507820346191109dc79c958d614` | CC BY 4.0 | no |
| `openai-community/roberta-base-openai-detector` | `6cba99c003b711c7fe94f8a3aa2be35a792cb6fa` | MIT | no |

The 3.1 pipeline's own `config.yaml` names the segmentation and embedding
models by repository id only. `app/diarization.py` rewrites those two entries
to the pinned revisions before the pipeline is built, so nothing resolves to a
branch. The embedding model is not named by the proctoring specification; its
revision is upstream `main` as resolved on 2026-09-02, recorded so it stops
moving.

### Accepting the Hugging Face conditions

Diarization cannot run, or be built into the image, until this is done once
by the account whose token the deployment uses:

1. Sign in to Hugging Face and open
   [hf.co/pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1).
   Fill in the access form and accept the conditions.
2. Do the same at
   [hf.co/pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0).
   Both are required; the pipeline loads the second.
3. Create a READ access token at
   [hf.co/settings/tokens](https://huggingface.co/settings/tokens). A
   fine-grained token needs "Read access to contents of all public gated repos
   you can access".
4. Put it where the runtime reads it: `HUGGINGFACE_TOKEN` in `.env` for the
   compose stack, or the `HUGGINGFACE_TOKEN` secret in AWS Secrets Manager for
   a deployment (`aws secretsmanager put-secret-value --secret-id
   readypick-<env>/HUGGINGFACE_TOKEN --secret-string ...`). For CI image
   builds it is the `HUGGINGFACE_TOKEN` repository secret.

Do not commit the token. Do not pass it as a build ARG or write it into an ENV
in the Dockerfile; both land in an image layer.

Without a token the service starts and `/health` answers
`{"diarization": "unavailable: HUGGINGFACE_TOKEN missing"}`. The token is
required at runtime as well as at build, even though the runtime is offline:
the licence is accepted per account, and a deployment that could run the
gated models with no account attached would be one nobody had accepted it for.

## Running

Tests need no model, no torch and no token; every model call is a fake.

```bash
cd analysis-service
python -m pip install -r requirements-test.txt
python -m pytest -q
```

Local run with the real models:

```bash
python -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
export HUGGINGFACE_TOKEN=hf_...            # after the acceptance steps above
python scripts/download_models.py --cache-dir models
MODEL_CACHE_DIR=models HF_HUB_OFFLINE=1 uvicorn app.main:app --port 8100
curl -s localhost:8100/health
curl -s -F "chunk=@sample.wav;type=audio/wav" localhost:8100/diarize
```

`models/` is gitignored. Weights are never committed.

Image build. The token is a BuildKit secret, read from the environment for
the one step that downloads the gated models and stored in no layer:

```bash
export HUGGINGFACE_TOKEN=hf_...
docker build --secret id=huggingface_token,env=HUGGINGFACE_TOKEN -t readypick-analysis analysis-service
docker run --rm -p 8100:8100 -e HUGGINGFACE_TOKEN readypick-analysis
```

Without a token the build refuses. `--build-arg
ALLOW_MISSING_HUGGINGFACE_TOKEN=true` builds a detector-only image that
reports diarization unavailable; the compose stack sets it so the rest of the
platform comes up on a machine with no token, and CI does not.

Compose (`infra/docker-compose.yml`) builds the `analysis` service on port
8100 and points the backend and worker at `http://analysis:8100`. The
`HUGGINGFACE_TOKEN` in `.env` reaches the container through `env_file`; to
bake the gated models into the local image, export it in the shell before
`docker compose build analysis`.

## Configuration

Every value the service reads, with its default. `tests/test_config.py`
asserts this table matches `app/config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `HUGGINGFACE_TOKEN` | `(empty)` | Empty means diarization is unavailable, stated at `/health`. |
| `AI_TEXT_ENABLED` | `false` | The AI-text detector ships disabled. Informational only when on. |
| `MODEL_CACHE_DIR` | `models` | Where the pre-downloaded weights live. The image sets `/models` and runs the hub client offline against it. |
| `DIARIZATION_MODEL` | `pyannote/speaker-diarization-3.1` | The pipeline repository. |
| `DIARIZATION_REVISION` | `84fd25912480287da0247647c3d2b4853cb3ee5d` | Its pinned commit. |
| `SEGMENTATION_MODEL` | `pyannote/segmentation-3.0` | The segmentation model the pipeline config names. |
| `SEGMENTATION_REVISION` | `e66f3d3b9eb0873085418a7b813d3b369bf160bb` | Its pinned commit. |
| `EMBEDDING_MODEL` | `pyannote/wespeaker-voxceleb-resnet34-LM` | The speaker-embedding model the pipeline config names. |
| `EMBEDDING_REVISION` | `837717ddb9ff5507820346191109dc79c958d614` | Upstream main on 2026-09-02, pinned here. |
| `AI_TEXT_MODEL` | `openai-community/roberta-base-openai-detector` | The detector repository. |
| `AI_TEXT_REVISION` | `6cba99c003b711c7fe94f8a3aa2be35a792cb6fa` | Its pinned commit. |
| `MAX_CHUNK_BYTES` | `2097152` | An upload larger than this is 413 before it is read in full. Matches the backend's `proctoring_audio_max_chunk_bytes`. |
| `MAX_CHUNK_SECONDS` | `30` | Decoded audio longer than this is 413. A chunk is about fifteen seconds. |
| `TARGET_SAMPLE_RATE` | `16000` | What the decoder resamples to; the models were trained at 16 kHz. |
| `AI_TEXT_MAX_CHARS` | `20000` | Text longer than this is 413. |
| `AI_TEXT_MAX_TOKENS` | `512` | RoBERTa's context window; longer text is truncated by the tokenizer. |
| `INFERENCE_CONCURRENCY` | `1` | Model calls in flight at once. CPU inference gains nothing from more, and two diarizations double peak memory. |
| `TORCH_THREADS` | `0` | 0 leaves torch's own thread count in place. |

The listening port is `PORT`, default 8100, read by the image's command.
