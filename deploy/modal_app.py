"""Run Fieldless live on Modal: one always-on container serves the API and the web app and runs scans one at a time.

  modal deploy deploy/modal_app.py              # from the repo root
  python deploy/modal_app.py --seed RUN_DIR     # copy a run's threshold.db and evidence/ to the data Volume

Both read MODAL_TOKEN_ID and MODAL_TOKEN_SECRET, and the server's keys, from the shared .env (the nearest one above
the repo). Only the keys the server needs go into the Modal secret, and nothing is printed. IP_HASH_SALT is derived
from MODEL_API_KEY, so it is secret and stays the same across deploys.

SQLite runs on the container's own disk. After every scan and review the database and evidence photos are copied to
the Volume (server/store.py, FolderBackup), which Modal commits, and a new container restores them from there.

Spend limits: the $3 per-scan and $10 per-day caps (pipeline/config.py) and PUBLIC_COST_CAP_USD across all scans
visitors start. There is no per-address limit. Scans started from the CLI (the seeded demo surveys) never count.
The live site judges with Muse Spark only and never starts Muse Glimmer's GPU: a visitor's survey is small, and
Glimmer pays off only on large runs (README, "Survey standards"). A server given GLIMMER_URL would use it, within
GLIMMER_DAILY_USD and GLIMMER_TOTAL_USD.
"""
import argparse
import hashlib
import hmac
import os
import sqlite3
import tempfile
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = "/root/app"
SERVER_KEYS = ("MODEL_API_KEY", "MAPILLARY_TOKEN", "BASETEN_API_KEY")
SETTINGS = {
    "THRESHOLD_DATA": "/root/data",  # the container's own disk; BACKUP_DIR is the Volume
    "THRESHOLD_RUN": "demo",
    "BACKUP_DIR": "/backup",
    "DEMO_SCANS": "17:Seattle (Capitol Hill);1:Seongsu",  # scan 17: the recording survey, scan 4's photos (2026-10-29)
    "SCANS_PER_IP_PER_DAY": "0",
    "PUBLIC_COST_CAP_USD": "40",
    # Three processes decode SAM masks (pipeline/sam_output.py), within the CPU limit below. In place, a live survey
    # took 8.1 s a photo against 2.3 s locally (scan 18, 2026-10-29); three forkserver workers on four CPUs decoded
    # 24 saved answers in 12.7 s against 38.3 s (Linux check). /api/health says which way this host decodes.
    "THRESHOLD_PARSE_PROCESSES": "3",
    "PYTHONUNBUFFERED": "1",
    "PYTHONPATH": APP_DIR,
}


def _env_file() -> Path | None:
    for folder in (ROOT, *ROOT.parents):
        if (folder / ".env").is_file():
            return folder / ".env"
    return None


def _load_env() -> None:
    """Put the shared .env into os.environ (Modal's client reads its token from there), without overriding."""
    from dotenv import load_dotenv
    if (path := _env_file()) is not None:
        load_dotenv(path, override=False)


def _server_secret() -> dict[str, str]:
    keys = {name: os.environ[name] for name in SERVER_KEYS if os.environ.get(name)}
    missing = {"MODEL_API_KEY", "MAPILLARY_TOKEN"} - keys.keys()
    if missing:
        raise SystemExit(f"missing in .env: {', '.join(sorted(missing))}")
    keys["IP_HASH_SALT"] = hmac.new(keys["MODEL_API_KEY"].encode(), b"fieldless-ip-salt", hashlib.sha256).hexdigest()
    return keys


if modal.is_local():
    _load_env()

app = modal.App("fieldless")
volume = modal.Volume.from_name("fieldless-data", create_if_missing=True, version=2)
image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("ffmpeg")
    .add_local_file(ROOT / "requirements.txt", "/tmp/requirements.txt", copy=True)
    .add_local_file(ROOT / "constraints.txt", "/tmp/constraints.txt", copy=True)
    .run_commands("grep -v '^pytest' /tmp/requirements.txt > /tmp/runtime.txt"
                  " && pip install --no-cache-dir -r /tmp/runtime.txt -c /tmp/constraints.txt")
    .env(SETTINGS)
    .add_local_dir(ROOT / "pipeline", f"{APP_DIR}/pipeline", ignore=["**/__pycache__/**"])
    .add_local_dir(ROOT / "server", f"{APP_DIR}/server", ignore=["**/__pycache__/**"])
    .add_local_dir(ROOT / "assist", f"{APP_DIR}/assist", ignore=["**/__pycache__/**"])  # the survey files (report)
    .add_local_dir(ROOT / "eval", f"{APP_DIR}/eval", ignore=["**/__pycache__/**"])  # assist.report reads with it
    .add_local_dir(ROOT / "web" / "dist", f"{APP_DIR}/web/dist")
)
secret = modal.Secret.from_dict(_server_secret() if modal.is_local() else {})


@app.function(image=image, secrets=[secret], volumes={"/backup": volume}, min_containers=1, max_containers=1,
              cpu=(0.25, 4.0), memory=(1024, 4096), timeout=600)  # above 0.25 CPU is billed only while used
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from server.app import create_app
    return create_app()


def seed(run_dir: Path) -> None:
    """Copy a consistent snapshot of run_dir's database (SQLite's backup) and its evidence photos to the Volume."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "threshold.db"
        src, dst = sqlite3.connect(run_dir / "threshold.db"), sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with volume.batch_upload(force=True) as batch:
            batch.put_file(snapshot, "/threshold.db")
            for check in run_dir.glob("city_check_scan*.json"):  # the offline city-record comparisons (eval)
                batch.put_file(check, f"/{check.name}")
            if (run_dir / "evidence").is_dir():
                batch.put_directory(run_dir / "evidence", "/evidence")
    print(f"seeded the Volume from {run_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=Path, required=True, help="run folder with threshold.db and evidence/")
    seed(parser.parse_args().seed)
