"""Settings for the production pipeline: key file, data folder, models, prices and limits."""
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

META_BASE_URL = "https://api.meta.ai/v1"
MAPILLARY_BASE_URL = "https://graph.mapillary.com"
MAPILLARY_PHOTO_LINK = "https://www.mapillary.com/app/?pKey={image_id}"
SPARK_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"

# Prices (dev.meta.ai model pages, checked 2026-10-27)
SPARK_IN_PER_TOKEN = 1.25e-6
SPARK_OUT_PER_TOKEN = 4.25e-6
SAM_PER_FRAME = 0.0002  # video: $0.20 per 1k frames
SAM_MIN_FRAMES = 50  # video: minimum billed frames per request
SAM_PER_IMAGE = 0.0025  # image: $2.50 per 1k images
# Reserved against the cap before a Spark call and replaced by the real cost after it. D1: a 60-photo chunk cost
# about $0.045 and one full-resolution photo about $0.012.
SPARK_VIDEO_ESTIMATE_USD = 0.06
SPARK_IMAGE_ESTIMATE_USD = 0.02

# Limits (spec section 13)
MAX_PHOTOS_PER_SCAN = 400
SCAN_COST_CAP_USD = 3.0
DAILY_COST_CAP_USD = 10.0
EST_USD_PER_100_PHOTOS = 0.73  # spec 7.4 ($0.57), S7 checks on about 10 candidates ($0.12), 2 fixed labels ($0.04)
PARALLEL_REQUESTS = 6


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


def data_dir() -> Path:
    """THRESHOLD_DATA, else the data/ folder next to the .env file. Raises when neither exists, so a run never
    writes into the repo by accident (dev-rules section 3)."""
    env = os.environ.get("THRESHOLD_DATA")
    if env:
        path = Path(env)
    elif ENV_FILE is not None:
        path = ENV_FILE.parent / "data"
    else:
        raise RuntimeError("no data folder: set THRESHOLD_DATA or put a .env file in a parent folder")
    path.mkdir(parents=True, exist_ok=True)
    return path


def photo_cache() -> Path:
    """Photos named by Mapillary image id. Shared by every run and session."""
    path = data_dir() / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir(name: str) -> Path:
    path = data_dir() / "runs" / name
    path.mkdir(parents=True, exist_ok=True)
    return path
