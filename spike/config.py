import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def find_env_file(start: Path) -> Path | None:
    """Nearest .env file at or above start, so git worktrees can share one .env in a parent folder."""
    for folder in (start, *start.parents):
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
    return None


ENV_FILE = find_env_file(ROOT.parent)
if ENV_FILE is not None:
    load_dotenv(ENV_FILE)

CLIPS = ROOT / "clips"
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

META_BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"
MAPILLARY_BASE_URL = "https://graph.mapillary.com"
CLIP_FPS = 1  # clips built from photo sequences show one photo per second, so t seconds == photo t
SPARK_HOLD = 2  # D1 finding: Muse Spark samples uploaded video at one frame every 2 s, so Spark copies show each photo for 2 s


def api_key() -> str:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set; put it in a .env file in the repo folder or a parent folder")
    return key


def mapillary_token() -> str:
    token = os.environ.get("MAPILLARY_TOKEN")
    if not token:
        raise RuntimeError("MAPILLARY_TOKEN is not set; put it in a .env file in the repo folder or a parent folder")
    return token
