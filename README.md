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

## Survey items from a rule (Muse Spark agent)

A survey item is two sentences: what SAM 3.1 should find, and the yes/no question Muse Spark answers about it. The
`assist` agent writes them from the rule itself:

1. Muse Spark reads a rule excerpt (PDF) and drafts the requirements a pedestrian photo can settle, each with its
   section, page and an exact quote. It also lists the ones a photo cannot settle (slopes, widths) and why.
2. Code drops any draft whose section or quote is not in the rule.
3. Muse Spark then tests each item with two tools on labelled photos: SAM 3.1 tries the `find` phrase, Muse Spark
   answers the `ask`, and code scores both. It revises the phrase or the question and tests again, within 20 tool
   calls and a spend cap.

```
.venv/Scripts/python -m assist.run_author --rule ada2010 --testset seattle_ramps --run author-ada
```

The result is a TOML file of `[[check]]` items with their source and measured scores, and a report. A person adds the
items they accept to `pipeline/standards/us.toml`. The excerpt in `assist/rules/` is from the 2010 ADA Standards for
Accessible Design (U.S. Department of Justice, public domain). The test set `assist/testsets/seattle_ramps.json` lists
Mapillary image ids with labels made from the photos.

Inventory items record where something is (streetlight poles, street trees, litter cans), not a defect, so they need no
rule: Muse Spark drafts one from the target's name, and the agent picks the SAM 3.1 phrase and tests that Muse Spark
confirms what SAM found ("yes" is an asset point, "no" drops a false find) on photos labelled only by whether they show
the target, within 12 tool calls. The TOML item has `inventory = true` and no `report_when`.

```
.venv/Scripts/python -m assist.run_inventory --target "streetlight pole" --testset seattle_poles --run inv-poles
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
| `SCANS_PER_IP_PER_DAY` | no | default 3; 0 turns the per-address limit off |
| `PUBLIC_COST_CAP_USD` | no | total spend of the surveys visitors start; past it, new surveys stop |
| `DEMO_SCANS` | no | surveys listed first, as `id:Name;id:Name` |
| `THRESHOLD_RUN` | no | run folder under `<data>/runs`, default `server` |
| `HF_DATA_REPO` | no | private Hugging Face dataset that keeps the data on a host without a disk (with `HF_TOKEN`) |
| `BACKUP_DIR` | no | or a backup folder (a mounted Modal Volume) that keeps it |
| `PROXY_HOPS` | no | trusted proxies that append to `X-Forwarded-For`, default 1 (Railway); 0 when no proxy |

Keys are read only from the environment and are never logged. In production, mount a persistent volume at `/data`: the database, photo cache and evidence photos live there. The server runs one process (`--workers 1`), because surveys run one at a time on a background thread. The image installs the tested package versions in constraints.txt.

### Survey standards

What a scan looks for is written in sentences, one file per standard in `pipeline/standards/` (`us.toml` for the
United States, `kr.toml` for Korea). A scan uses KR when its area is in Korea and US elsewhere (`--standard` overrides).
Each `[[check]]` names what SAM 3.1 finds in the photos (`find`), the noun the survey is about (`target`, `find` by
default) and the question Muse Spark answers about it:

```toml
[[check]]
id = "curb_ramp_warning"
find = "curb cut"
target = "curb ramp"
ask = "A curb ramp is the short sloped section where a pedestrian crossing meets the sidewalk. Driveways, alley entrances and plain raised curbs are not curb ramps; if the photo shows only those, set visible to false. If there is a curb ramp, does it have a detectable warning surface (a panel of raised truncated domes) at its bottom edge? If its bottom edge is too far away or too small to see, answer unclear."
report_when = "no"
label_yes = "Curb ramp with detectable warning"
label_no = "Curb ramp without detectable warning"
```

To survey something new, add a `[[check]]` and run the checks again on a finished scan (no training data is needed):

```bash
python -m pipeline.run_scan --resume SCAN_ID --run RUN --rerun defects --check NEW_ID
```

`--check` keeps the other items' rows and reviews.

A check with `inventory = true` maps assets instead of defects: every confirmed object is a point. Use it for things a
city keeps an inventory of (poles, trees, bins). It has no `report_when`; a "no" answer drops the pick, and the labels
default from `target`:

```toml
[[check]]
id = "utility_pole"
inventory = true
find = "street light pole"
target = "utility or streetlight pole"
ask = "Look at the tall pole closest to the camera along the sidewalk. Is it a pole for overhead lines or street lighting: a wooden or metal utility pole, a streetlight pole, or a traffic-signal pole with a light arm? Answer no for a tree, a sign post, a parking meter, a flagpole or a pole on private land. If the pole is too far away or mostly hidden to tell, answer unclear."
label_yes = "Utility or streetlight pole"
```

`group_m = 15` gives things that stand in rows (poles, trees) one row per 15 m of walk instead of one per run of photos.

### Export a survey

```bash
python -m assist.report --run RUN --scan SCAN_ID [--area "Capitol Hill"] [--narrate]
```

This writes `report.md`, `report.html`, `survey_scan<id>.geojson` and one CSV per survey item into
`<run>/report_scan<id>/`. Every finding sits at its photo's location with the photo's creator, Mapillary link and
licence; rows a person rejected are left out. `--narrate` adds a short Muse Spark summary written only from the
report's counts (code drops it if it names an unknown item or a number the counts do not hold); without it no API key
is needed.

### Public demo (static)

The public demo is a static site: the web app plus the server's answers for a few surveys already run, read-only. `deploy/static_site.py` asks the real server app for every answer, so the demo shows what the live server shows.

```bash
hf auth login                      # a token with write access to your repos
python -m deploy.static_site --run <run> --scan <id>:<Name> --out data/site --space USER/fieldless
```

### Live on Modal

`deploy/modal_app.py` runs the server on Modal: one always-on container serves the web app and the API and runs surveys one at a time. SQLite runs on the container's disk; after every survey and review the database and evidence photos are copied to a Modal Volume (`BACKUP_DIR`), and a new container restores them from there.

```bash
python deploy/modal_app.py --seed data/runs/<run>   # once: the demo surveys
modal deploy deploy/modal_app.py
```

Both read `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` and the server's keys from the shared `.env`; only the server's keys go into the Modal secret. Anyone can start a survey, with no per-address limit (`SCANS_PER_IP_PER_DAY=0`). Spend stays inside the $3 per-survey and $10 per-day caps and `PUBLIC_COST_CAP_USD` ($40) across all surveys visitors start; past it, new surveys stop and finished ones stay open.

### On a Hugging Face Docker Space

A Docker Space (PRO on Hugging Face since 2026) has no disk that survives a restart, so the server can keep its database and evidence photos in a private Hugging Face dataset: it restores them on start and uploads them after each survey and review (`server/store.py`, on when `HF_DATA_REPO` is set). `python -m deploy.hf_space --space USER/fieldless --data USER/fieldless-data --salt` publishes it; then set `MODEL_API_KEY`, `MAPILLARY_TOKEN`, `INVITE_CODE` and `HF_TOKEN` as Space secrets.
