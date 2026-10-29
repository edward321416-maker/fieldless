"""Serve Muse Glimmer 30B on Modal ourselves: vLLM's OpenAI-compatible server on one H100, scaled to zero.

  python deploy/modal_glimmer.py --download      # once: the weights into the Volume, on CPU (no GPU billed)
  modal deploy deploy/modal_glimmer.py            # the server; prints its URL, which clients take as GLIMMER_URL
  GLIMMER_URL=<url> python -m eval.glimmer_replay --run RUN --scan N --out OUT.json --limit 1   # stage 0

Run the first two with a Python that has the `modal` package installed. MODAL_TOKEN_ID/SECRET and
MODEL_API_KEY are read from the shared .env (never printed, never written). The server's key is the same HMAC of
MODEL_API_KEY as pipeline.second_opinion.glimmer_key(), computed here because this Python need not have the
pipeline's packages; tests/deploy checks that both agree. One container at most and 120 s of idle before it stops
keep the GPU bill bounded: the live site (app "fieldless") runs on the same workspace credit.
"""
import argparse
import hashlib
import hmac
import os
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
MODEL = "meta-models/Muse-Glimmer-30B"  # = pipeline.second_opinion.GLIMMER_MODEL (tests/deploy checks it)
KEY_SALT = b"fieldless-glimmer-key"  # = the salt of pipeline.second_opinion.glimmer_key() (tests/deploy checks it)
VLLM = "vllm==0.31.0"  # the first releases with Muse Glimmer are 0.28; 0.31 has its reasoning parser
WEIGHTS = "/weights"
PORT = 8000
MAX_MODEL_LEN = 16384  # one photo, a check prompt and low-effort reasoning fit with room to spare


def _load_env() -> None:
    from dotenv import load_dotenv
    for folder in (ROOT, *ROOT.parents):
        if (folder / ".env").is_file():
            load_dotenv(folder / ".env", override=False)
            return


def _key() -> str:
    return hmac.new(os.environ["MODEL_API_KEY"].encode(), KEY_SALT, hashlib.sha256).hexdigest()


if modal.is_local():
    _load_env()

app = modal.App("fieldless-glimmer")
volume = modal.Volume.from_name("fieldless-glimmer-weights", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install(VLLM, "huggingface_hub[hf_transfer]")
    .env({"HF_HOME": WEIGHTS, "HF_HUB_ENABLE_HF_TRANSFER": "1"})
)
secret = modal.Secret.from_dict({"GLIMMER_API_KEY": _key()} if modal.is_local() else {})


@app.function(image=image, volumes={WEIGHTS: volume}, cpu=4, memory=8192, timeout=3600)
def download() -> str:
    from huggingface_hub import snapshot_download
    path = snapshot_download(MODEL)
    volume.commit()
    return path


@app.function(image=image, gpu="H100", volumes={WEIGHTS: volume}, secrets=[secret], min_containers=0,
              max_containers=1, scaledown_window=120, timeout=3600)
@modal.concurrent(max_inputs=64)
@modal.web_server(port=PORT, startup_timeout=900)
def serve() -> None:
    import subprocess
    subprocess.Popen(["vllm", "serve", MODEL, "--host", "0.0.0.0", "--port", str(PORT),
                      "--api-key", os.environ["GLIMMER_API_KEY"], "--reasoning-parser", "muse_glimmer",
                      "--max-model-len", str(MAX_MODEL_LEN), "--served-model-name", MODEL])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--download", action="store_true", required=True)
    ap.parse_args()
    with modal.enable_output(), app.run():
        print(download.remote())
