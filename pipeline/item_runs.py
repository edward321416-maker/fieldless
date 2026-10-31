"""A survey item the agent wrote (assist.custom_item, plan 12), run on a finished scan's photos.

The scan itself never changes: the item's rows and evidence photos belong to its item run (db.item_run), so the
scan's own rows, reviews, report and files stay as they were. One run is capped at ITEM_COST_CAP_USD and counts
toward the day's cap and the visitors' cap like a scan. The rows are made the way a scan makes a check's rows
(survey_checks.run_check: SAM finds the item, Muse Spark confirms it on the best photo), so the app draws them as it
draws the scan's own."""
from dataclasses import dataclass
from pathlib import Path

from pipeline import collect, config, db, evidence, survey_checks
from pipeline.cost import CostCapReached, CostMeter
from pipeline.meta_api import error_text
from pipeline.parallel import run_parallel
from pipeline.runner import evidence_name, refuse_in_worker
from pipeline.survey_items import Check

ITEM_COST_CAP_USD = 1.0  # one item run: writing, testing and surveying (docs/specs/2026-10-29-agent-items-design.md 3)
GIVE_UP_AFTER = 20  # photos in a row that could not be had


@dataclass(frozen=True)
class ScanPhotos:
    """A finished scan's photos that are on this disk: its frames (db.frames rows) and their files by photo idx."""
    scan_id: int
    bbox: list[float]
    standard: str
    frames: list[dict]
    paths: dict[int, Path]

    def by_idx(self) -> dict[int, dict]:
        return {f["idx"]: f for f in self.frames}

    def clips(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for f in sorted(self.frames, key=lambda f: f["idx"]):
            out.setdefault(f["clip"], []).append(f["idx"])
        return out


def scan_photos(conn, scan_id: int, workers: int = config.PARALLEL_REQUESTS, fetch=collect.fetch_photo) -> ScanPhotos:
    """The scan's photos, downloaded from Mapillary into the photo cache when they are not there (a scan seeded onto
    the live server brings its database and evidence photos, not its photos). A photo that cannot be had is left
    out; after GIVE_UP_AFTER in a row Mapillary is taken to be down and the scan has no photos, since a stalled photo
    costs minutes of retries and a part of the scan would be reported as all of it."""
    scan = db.get_scan(conn, scan_id)
    frames = db.frames(conn, scan_id)
    kept, missing = [], 0
    for start in range(0, len(frames), workers):
        batch = frames[start:start + workers]
        have = run_parallel(lambda f: fetch({"id": f["image_id"]}) is not None, batch, workers)
        for frame, ok in zip(batch, have):
            missing = 0 if ok else missing + 1
            if ok:
                kept.append(frame)
        if missing >= GIVE_UP_AFTER:
            kept = []
            break
    return ScanPhotos(scan_id, scan["bbox"], scan["jurisdiction"], kept,
                      {f["idx"]: collect.photo_path(f["image_id"]) for f in kept})


def meter_for_item(conn, run: dict, public_cap_usd: float | None = None) -> CostMeter:
    """The run's cap: ITEM_COST_CAP_USD, no more than what other scans and runs left of today's DAILY_COST_CAP_USD and,
    for a run a visitor started, of public_cap_usd. Spend before a pause for a person's answers already counts."""
    today = db.now()[:10]
    others = db.daily_cost(conn, today) - (run["cost_usd"] if run["created_at"].startswith(today) else 0.0)
    cap = min(ITEM_COST_CAP_USD, config.DAILY_COST_CAP_USD - others)
    if public_cap_usd is not None and run.get("client_ip_hash"):
        cap = min(cap, public_cap_usd - (db.public_cost(conn) - run["cost_usd"]))
    return CostMeter(cap_usd=max(0.0, cap), spent_usd=run["cost_usd"])


def run_item_on_scan(api, photos: ScanPhotos, check: Check, run_dir: Path, run_id: int,
                     workers: int = config.PARALLEL_REQUESTS, on_progress=lambda line: None) -> list[dict]:
    """The item's rows on every photo of the scan, each with a blurred, attributed evidence photo. A row is a
    sidewalk_issue row as the scan's own checks make it, plus id "item<run>-<n>", scan_id and item_run. A row whose
    evidence photo failed keeps an error record, as a scan's row does."""
    refuse_in_worker()
    if check.find_by != "photos":
        raise ValueError(f"an agent item is found on photos, not by {check.find_by}")
    on_progress(f"Surveying all {len(photos.paths)} photos of the scan for {check.target}")
    by_idx = photos.by_idx()
    rows, missing = survey_checks.run_check(api, check, photos.clips(), photos.paths, by_idx, workers)
    if missing.get("stopped"):  # part of the photos is not the survey the run reports: it fails, its spend saved
        raise missing["stopped"]
    picks = len(rows) + len(missing.get("not_target", []))
    on_progress(f"SAM 3.1 found {picks} possible {check.target}; Muse Spark kept {len(rows)}")
    unavailable = len(set(missing.get("find", [])) | set(missing.get("ask", [])))
    if unavailable:
        on_progress(f"{unavailable} photos could not be checked")
    out = Path(run_dir) / "evidence"

    def one(numbered):
        n, row = numbered
        frame = by_idx[row["frame_idx"]]
        try:
            return evidence.make_evidence(api, photos.paths[row["frame_idx"]], frame,
                                          out / evidence_name(photos.scan_id, f"item{run_id}-", n, frame["image_id"]))
        except CostCapReached:
            raise
        except Exception as exc:  # no photo is stored without blurring
            return {"error": error_text(exc)}

    records = run_parallel(one, list(enumerate(rows, 1)), workers)
    return [{**row, "id": f"item{run_id}-{n}", "scan_id": photos.scan_id, "item_run": run_id, "evidence": record}
            for n, (row, record) in enumerate(zip(rows, records), 1)]
