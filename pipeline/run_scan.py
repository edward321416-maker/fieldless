"""Command line for one scan (spec 7).

  python -m pipeline.run_scan --preview --bbox MIN_LNG,MIN_LAT,MAX_LNG,MAX_LAT [--max-photos N]
  python -m pipeline.run_scan --bbox MIN_LNG,MIN_LAT,MAX_LNG,MAX_LAT [--run NAME] [--max-photos N] [--tactile-every N]
  python -m pipeline.run_scan --resume SCAN_ID [--run NAME]
  python -m pipeline.run_scan --bbox ... --standard US       # the survey standard (default: by the area)
  python -m pipeline.run_scan --resume SCAN_ID --rerun defects   # run one stage again, e.g. after editing a standard
  python -m pipeline.run_scan --resume SCAN_ID --rerun defects --check ID   # only that check; others keep their rows
  python -m pipeline.run_scan --bbox ... --cap-usd 6          # local runs only: this scan's cost cap (default $3)
  python -m pipeline.run_scan --bbox ... --glimmer            # large runs: Muse Glimmer judges the checks that name it

Results: <data>/runs/<run>/threshold.db, calls with costs in <data>/runs/<run>/calls.jsonl, blurred evidence photos
in <data>/runs/<run>/evidence/. Exit code 0 when the scan is done, 1 otherwise.
"""
import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from pipeline import collect, config, db, second_opinion, sidewalk, survey_items
from pipeline.meta_api import MetaApi
from pipeline.runner import STAGES, Scan, meter_for, refuse_in_worker, run_scan, spend_saver


def parse_bbox(text: str) -> list[float]:
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) != 4:
        raise argparse.ArgumentTypeError("bbox needs four numbers: min_lng,min_lat,max_lng,max_lat")
    return values


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError("must be a whole number of 1 or more")
    return value


def positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = 0.0
    if not value > 0:  # also refuses nan
        raise argparse.ArgumentTypeError("must be a number above 0")
    return value


@contextmanager
def one_scan_at_a_time(run_dir: Path):
    """Hold run_dir/scan.lock while a scan runs from the command line; a second scan in the same run folder stops at
    once instead of spending beside it (each scan reads the day's spend only when it starts). The operating system
    lets go of the lock when the process ends, however it ends."""
    path = Path(run_dir) / "scan.lock"
    handle = open(path, "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ScanAlreadyRunning(f"another scan is running in {run_dir}; wait for it to finish") from None
        yield
    finally:
        handle.close()


class ScanAlreadyRunning(RuntimeError):
    """A command-line scan is already running in this run folder."""


def main(argv: list[str] | None = None) -> int:
    refuse_in_worker()  # before anything is created or spent
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # D1: Korean names crashed a cp949 console
    parser = argparse.ArgumentParser(description="Scan an area's Mapillary photos for the items of a survey standard "
                                                 "(pipeline/standards).")
    parser.add_argument("--bbox", type=parse_bbox)
    parser.add_argument("--resume", type=positive_int)
    parser.add_argument("--photos-of", type=positive_int, metavar="SCAN_ID",
                        help="a new scan of the same photos as this scan (its area, standard and walks): the collect "
                             "stage is taken from it, so two code versions can be compared on one set of photos")
    parser.add_argument("--preview", action="store_true", help="count photos and estimate cost; no model calls")
    parser.add_argument("--standard", choices=["KR", "US"],
                        help="survey standard for a new scan (default: KR when the area is in Korea, else US)")
    parser.add_argument("--rerun", choices=STAGES, help="with --resume: run this stage again, then the evidence stages")
    parser.add_argument("--check", action="append", metavar="ID",
                        help="with --rerun defects: run only these checks, keeping the other checks' rows and reviews")
    parser.add_argument("--cap-usd", type=positive_float,
                        help="local runs only: this scan's cost cap in dollars (default: the $3 scan cap); "
                             "the daily cap still applies")
    parser.add_argument("--daily-cap-usd", type=positive_float,
                        help="local runs only, with the user's approval: today's cap for this run (default: "
                             f"${config.DAILY_COST_CAP_USD:.0f} per UTC day per run folder)")
    parser.add_argument("--run", default="dev", help="run folder under <data>/runs")
    parser.add_argument("--glimmer", action="store_true",
                        help="Muse Glimmer (GLIMMER_URL) judges the checks with judge = \"glimmer\". For large runs: its "
                             "GPU pays off only while it stays busy (a small scan is cheaper and faster on Muse Spark)")
    parser.add_argument("--max-photos", type=positive_int, default=config.MAX_PHOTOS_PER_SCAN)
    parser.add_argument("--tactile-every", type=positive_int, default=sidewalk.TACTILE_EVERY)
    args = parser.parse_args(argv)
    if args.preview:
        if not args.bbox:
            parser.error("--preview needs --bbox")
        checks = len(survey_items.load(args.standard or survey_items.standard_for(args.bbox)).checks)
        budget = config.photo_budget(args.max_photos, checks=checks, cap_usd=args.cap_usd or config.SCAN_COST_CAP_USD)
        preview = collect.preview(args.bbox, budget, checks=checks)
        print(json.dumps(preview, ensure_ascii=False, indent=1))
        return 0
    if sum(x is not None for x in (args.bbox, args.resume, args.photos_of)) != 1:
        parser.error("give one of --bbox for a new scan, --photos-of SCAN_ID or --resume SCAN_ID")
    if args.rerun and args.resume is None:
        parser.error("--rerun needs --resume")
    if args.check and args.rerun != "defects":
        parser.error("--check needs --rerun defects")
    if args.glimmer and not os.environ.get("GLIMMER_URL"):
        parser.error("--glimmer needs GLIMMER_URL, the deployed fieldless-glimmer address")
    run_dir = config.run_dir(args.run)
    with one_scan_at_a_time(run_dir):
        return _scan(parser, args, run_dir)


def _scan(parser, args, run_dir: Path) -> int:
    conn = db.connect(run_dir / "threshold.db")
    if args.resume is not None:
        try:
            db.get_scan(conn, args.resume)
        except KeyError:
            parser.error(f"no scan {args.resume} in run {args.run}")
        scan_id = args.resume
        known = survey_items.load(db.get_scan(conn, scan_id)["jurisdiction"])
        for check_id in args.check or ():
            if known.check(check_id) is None:
                parser.error(f"{check_id!r} is not a check of the {known.code} standard "
                             f"({', '.join(c.id for c in known.checks) or 'none'})")
    elif args.photos_of is not None:
        try:
            source = db.get_scan(conn, args.photos_of)
        except KeyError:
            parser.error(f"no scan {args.photos_of} in run {args.run}")
        frames = db.frames(conn, args.photos_of)
        if not frames:
            parser.error(f"scan {args.photos_of} has no photos to reuse")
        scan_id = db.create_scan(conn, "mapillary", source["bbox"], args.standard or source["jurisdiction"])
        db.add_frames(conn, scan_id, frames)
        db.update_scan(conn, scan_id, image_count=len(frames), captured_from=source["captured_from"],
                       captured_to=source["captured_to"], sequence_ids=source["sequence_ids"])
        db.set_stage(conn, scan_id, "collect", "done")  # the photos are those of scan photos_of, already downloaded
    else:
        scan_id = db.create_scan(conn, "mapillary", args.bbox, args.standard or survey_items.standard_for(args.bbox))
    if args.rerun:
        db.set_stage(conn, scan_id, args.rerun, "pending")
    scan = db.get_scan(conn, scan_id)
    meter = meter_for(conn, scan, args.cap_usd, daily_cap_usd=args.daily_cap_usd)
    meter.on_spend = spend_saver(run_dir / "threshold.db", scan_id, scan["cost_usd"])  # a killed run still counts
    api = MetaApi(run_dir / "calls.jsonl", meter)
    result = run_scan(Scan(conn, scan_id, api, run_dir, scan["bbox"], max_photos=args.max_photos,
                           tactile_every=args.tactile_every, second_opinion=second_opinion.from_env(run_dir),
                           judge=second_opinion.glimmer_from_env(run_dir) if args.glimmer else None,
                           only_checks=tuple(args.check or ())))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
