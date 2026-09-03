"""Pre-download the pinned models into the cache the service reads offline.

Run at image build, and by hand for a local checkout:

    python scripts/download_models.py --cache-dir models

WHERE THE TOKEN COMES FROM, AND WHERE IT MUST NOT GO
----------------------------------------------------
The two pyannote repositories are gated on Hugging Face. The token that unlocks
them is read from `HUGGINGFACE_TOKEN` in the environment or, in a Docker build,
from the file BuildKit mounts for `--mount=type=secret,id=huggingface_token`.
It is never a build ARG and never an ENV in the Dockerfile, because both of
those are written into an image layer and `docker history` prints them. A
secret mount exists only for the duration of the one RUN that uses it.

Without a token the script refuses, unless `--allow-missing-token` is passed:
that flag is for a local compose build, where the image then reports
diarization as unavailable at /health. CI never passes it, so an image built
without the secret cannot reach a registry.

The RoBERTa detector is MIT and not gated. Only the files the loader reads are
fetched; the repository also carries TensorFlow, Flax and legacy PyTorch copies
of the same weights, which would triple the image for nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys

from huggingface_hub import snapshot_download

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.config import settings_from_env  # noqa: E402

DEFAULT_TOKEN_FILE = "/run/secrets/huggingface_token"

AI_TEXT_FILES = [
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
]
PYANNOTE_FILES = ["config.yaml", "pytorch_model.bin"]


def read_token(token_file: str) -> str:
    """The token from the environment, else from the secret mount, else empty."""
    from_env = os.environ.get("HUGGINGFACE_TOKEN", "").strip()
    if from_env:
        return from_env
    path = pathlib.Path(token_file)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(repo_id: str, revision: str, files: list[str], cache_dir: str, token: str | None) -> pathlib.Path:
    """Download one repository at one commit and return its snapshot directory."""
    snapshot = pathlib.Path(
        snapshot_download(
            repo_id,
            revision=revision,
            cache_dir=cache_dir,
            token=token,
            allow_patterns=files,
        )
    )
    if snapshot.name != revision:
        raise SystemExit(
            f"{repo_id}: asked for revision {revision} and the hub resolved {snapshot.name}. "
            f"The pin must be a full commit hash."
        )
    for name in files:
        if not (snapshot / name).exists():
            raise SystemExit(f"{repo_id}@{revision}: {name} was not downloaded")
    config = next(snapshot / name for name in files if name.startswith("config."))
    print(f"  {repo_id}@{revision}")
    print(f"    {config.name} sha256 {sha256_of(config)}")
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", default=None, help="defaults to MODEL_CACHE_DIR")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="the BuildKit secret mount")
    parser.add_argument(
        "--allow-missing-token",
        action="store_true",
        help="download the detector only; the image will report diarization unavailable",
    )
    args = parser.parse_args(argv)

    settings = settings_from_env()
    cache_dir = args.cache_dir or settings.model_cache_dir
    pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)
    token = read_token(args.token_file)

    print(f"model cache: {cache_dir}")
    print("AI-text detector (MIT, not gated):")
    fetch(settings.ai_text_model, settings.ai_text_revision, AI_TEXT_FILES, cache_dir, token or None)

    if not token:
        if not args.allow_missing_token:
            print(
                "No Hugging Face token: set HUGGINGFACE_TOKEN or mount the huggingface_token "
                "build secret. The diarization models are gated and cannot be fetched without "
                "one. Pass --allow-missing-token to build a detector-only image.",
                file=sys.stderr,
            )
            return 1
        print("No Hugging Face token: the diarization models were NOT downloaded.")
        print("This image will report diarization as unavailable at /health.")
        return 0

    print("Diarization pipeline (gated; licence accepted on the token's account):")
    fetch(settings.diarization_model, settings.diarization_revision, ["config.yaml"], cache_dir, token)
    fetch(settings.segmentation_model, settings.segmentation_revision, PYANNOTE_FILES, cache_dir, token)
    fetch(settings.embedding_model, settings.embedding_revision, PYANNOTE_FILES, cache_dir, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
