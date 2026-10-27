# Fieldless

**Street surveys without the fieldwork.** Cities still survey their streets on foot, one block at a time. Fieldless does the first pass from street photos that already exist on Mapillary. It records what it finds with an evidence photo for each item, and a surveyor only confirms. The point is speed and cost: how long a street survey takes on foot, and what it costs per kilometer, compared with Fieldless.

Today it surveys sidewalks (tactile paving with missing blocks or cut by a cover, fixed objects standing on it, objects blocking it) and the steps at shop entrances. It is built on Meta's Mapillary imagery, SAM 3.1 and Muse Spark.

This is a work in progress for the Meta Global AI Developer Hackathon. The full README, with measured results, comes with the submission. The repository folder and code keep the earlier working name `threshold`.

## Built during the Meta Global AI Developer Hackathon

All code in this repository was written during the hackathon, starting after kickoff on 2026-10-26 12:00 PM ET. The `spike/` folder contains the day-1 feasibility tests.

## Setup

Needs Python 3.12+ and `ffmpeg`/`ffprobe` on PATH.

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pytest -q                         # offline tests, no API calls
```

Create a file named `.env` with two lines, either in the repo root (it is git-ignored) or in any parent folder. Git worktrees of this repo can share one `.env` placed above them.

```
MODEL_API_KEY=<your Meta Model API key>
MAPILLARY_TOKEN=<your Mapillary client token>
```

## Run the app

The app is one FastAPI server (`server/`) that also serves the built web app (`web/`).

Local development, in two terminals from the repo root:

```bash
.venv/Scripts/python -m uvicorn server.app:create_app --factory --port 8000
npm --prefix web install
npm --prefix web run dev
```

Then open http://localhost:5173. Vite forwards `/api` to port 8000.

With Docker:

```bash
docker build -t threshold .
docker run --rm -p 8000:8000 -v threshold-data:/data \
  -e MODEL_API_KEY -e MAPILLARY_TOKEN threshold
```

Then open http://localhost:8000.

| Variable | Needed | Meaning |
|---|---|---|
| `MODEL_API_KEY` | yes | Meta Model API key |
| `MAPILLARY_TOKEN` | yes | Mapillary client token |
| `THRESHOLD_DATA` | no | data folder (the image uses `/data`) |
| `INVITE_CODE` | no | if set, starting a survey needs this code |
| `IP_HASH_SALT` | in production | random salt for the hashed client address; without it a public default is used |
| `SCANS_PER_IP_PER_DAY` | no | default 3 |
| `DEMO_SCANS` | no | surveys listed first, as `id:Name;id:Name` |
| `THRESHOLD_RUN` | no | run folder under `<data>/runs`, default `server` |
| `HF_DATA_REPO` | no | private Hugging Face dataset that keeps the data on a host without a disk (with `HF_TOKEN`) |
| `PROXY_HOPS` | no | trusted proxies that append to `X-Forwarded-For`, default 1 (Railway); 0 when no proxy |

Keys are read only from the environment and are never logged. In production, mount a persistent volume at `/data`: the database, photo cache and evidence photos live there. The server runs one process (`--workers 1`), because surveys run one at a time on a background thread. The image installs the tested package versions in constraints.txt.

### On a free Hugging Face Space

A free Space (Docker, 2 vCPU, 16 GB) has no disk that survives a restart, so the server keeps its database and evidence photos in a private Hugging Face dataset: it restores them on start and uploads them after each survey and review (`server/store.py`, on when `HF_DATA_REPO` is set).

```bash
hf auth login                      # a token with write access to your repos
python -m deploy.hf_space --space USER/fieldless --data USER/fieldless-data --salt
```

Then set `MODEL_API_KEY`, `MAPILLARY_TOKEN`, `INVITE_CODE` and `HF_TOKEN` (write access to the dataset) as secrets in the Space settings. `--seed data/runs/<run>` uploads a local run's surveys to the dataset first.
