# Fieldless

**Street surveys without the fieldwork.** Cities still survey their streets on foot, one block at a time. Fieldless does the first pass from street photos that already exist on Mapillary. It records what it finds with an evidence photo for each item, and a surveyor only confirms. The point is speed: how long a survey takes by hand compared with Fieldless.

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
