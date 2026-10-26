import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")

CLIPS = ROOT / "clips"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

META_BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"
MAPILLARY_BASE_URL = "https://graph.mapillary.com"
CLIP_FPS = 1  # clips built from photo sequences show one photo per second, so t seconds == photo t


def api_key() -> str:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set; put it in threshold/.env")
    return key


def mapillary_token() -> str:
    token = os.environ.get("MAPILLARY_TOKEN")
    if not token:
        raise RuntimeError("MAPILLARY_TOKEN is not set; put it in threshold/.env")
    return token
