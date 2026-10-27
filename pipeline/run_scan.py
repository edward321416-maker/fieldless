"""Command line for one scan (spec 7).

  python -m pipeline.run_scan --preview --bbox MIN_LNG,MIN_LAT,MAX_LNG,MAX_LAT [--max-photos N]
  python -m pipeline.run_scan --bbox MIN_LNG,MIN_LAT,MAX_LNG,MAX_LAT [--run NAME] [--max-photos N] [--tactile-every N]
  python -m pipeline.run_scan --resume SCAN_ID [--run NAME]

Results: <data>/runs/<run>/threshold.db, calls with costs in <data>/runs/<run>/calls.jsonl, blurred evidence photos
in <data>/runs/<run>/evidence/. Exit code 0 when the scan is done, 1 otherwise.
"""
import argparse
import json
import sys

from pipeline import collect, config, db, sidewalk
from pipeline.meta_api import MetaApi
from pipeline.runner import Scan, meter_for, run_scan


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


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # D1: Korean names crashed a cp949 console
    parser = argparse.ArgumentParser(description="Scan an area's Mapillary photos for tactile paving defects, "
                                                 "obstacles on tactile paving and entrance steps.")
    parser.add_argument("--bbox", type=parse_bbox)
    parser.add_argument("--resume", type=positive_int)
    parser.add_argument("--preview", action="store_true", help="count photos and estimate cost; no model calls")
    parser.add_argument("--run", default="dev", help="run folder under <data>/runs")
    parser.add_argument("--max-photos", type=positive_int, default=config.MAX_PHOTOS_PER_SCAN)
    parser.add_argument("--tactile-every", type=positive_int, default=sidewalk.TACTILE_EVERY)
    args = parser.parse_args(argv)
    if args.preview:
        if not args.bbox:
            parser.error("--preview needs --bbox")
        preview = collect.preview(args.bbox, config.photo_budget(args.max_photos))
        print(json.dumps(preview, ensure_ascii=False, indent=1))
        return 0
    if (args.bbox is None) == (args.resume is None):
        parser.error("give --bbox for a new scan or --resume SCAN_ID")
    run_dir = config.run_dir(args.run)
    conn = db.connect(run_dir / "threshold.db")
    if args.resume is not None:
        try:
            db.get_scan(conn, args.resume)
        except KeyError:
            parser.error(f"no scan {args.resume} in run {args.run}")
        scan_id = args.resume
    else:
        scan_id = db.create_scan(conn, "mapillary", args.bbox)
    scan = db.get_scan(conn, scan_id)
    api = MetaApi(run_dir / "calls.jsonl", meter_for(conn, scan))
    result = run_scan(Scan(conn, scan_id, api, run_dir, scan["bbox"], max_photos=args.max_photos,
                           tactile_every=args.tactile_every))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
