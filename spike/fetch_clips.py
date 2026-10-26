"""Find Mapillary pedestrian photo sequences and turn one into a test clip (one photo per second).

  python -m spike.fetch_clips coverage
  python -m spike.fetch_clips build street_a <sequence_id> [--start 0] [--count 180]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from spike import config, mapillary, video

SINCE = "2020-01-01"
# Busy shopping streets, about 0.9 km x 0.9 km each (bbox = minLon, minLat, maxLon, maxLat).
AREAS = {
    "daejeon_eunhaeng": (127.4225, 36.3250, 127.4325, 36.3330),
    "daejeon_dunsan": (127.3730, 36.3475, 127.3830, 36.3555),
    "seoul_hongdae": (126.9186, 37.5523, 126.9286, 37.5603),
    "seoul_myeongdong": (126.9800, 37.5596, 126.9900, 37.5676),
    "seoul_seongsu": (127.0507, 37.5406, 127.0607, 37.5486),
    "seoul_gangnam": (127.0226, 37.4939, 127.0326, 37.5019),
    "busan_seomyeon": (129.0550, 35.1538, 129.0650, 35.1618),
    # overseas fallbacks (spec section 20): Tokyo for tactile paving, Seattle for ADA
    "tokyo_shibuya": (139.6955, 35.6555, 139.7055, 35.6635),
    "seattle_capitol_hill": (-122.3250, 47.6110, -122.3150, 47.6190),
}
KR_AREAS = {name for name in AREAS if name.split("_")[0] in {"daejeon", "seoul", "busan"}}
TOP_SEQUENCES = 3
SHEET_PHOTOS = 12
MAX_FAILED_SHARE = 0.05  # build exits non-zero when more than this share of photos cannot be downloaded


def contact_sheet(paths, labels, dst, cols=4, tile=(320, 240)) -> None:
    tiles = []
    for path, label in zip(paths, labels):
        img = cv2.imread(str(path))
        img = np.zeros((tile[1], tile[0], 3), np.uint8) if img is None else cv2.resize(img, tile)
        cv2.putText(img, label, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        tiles.append(img)
    while len(tiles) % cols:
        tiles.append(np.zeros((tile[1], tile[0], 3), np.uint8))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(dst), np.vstack(rows))


def _kr_best(report: dict) -> dict | None:
    candidates = [(v["summary"]["recent_on_foot_flat"], k)
                  for k, v in report["areas"].items() if v["kr"] and "summary" in v]
    if not candidates:
        return None
    count, area = max(candidates)
    summary = report["areas"][area]["summary"]
    return {"area": area, "recent_on_foot_flat": count, "recent_flat": summary["recent_flat"],
            "capped": summary["capped"]}


def _write_report(report: dict) -> None:
    report["kr_best"] = _kr_best(report)
    (config.OUT / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _survey_area(name: str, bbox, since: int, sheets: Path, photos: Path) -> dict:
    images, capped = mapillary.search_area(bbox)
    summary = mapillary.summarize(images, since, capped=capped)
    top = mapillary.rank_sequences(images, since)[:TOP_SEQUENCES]
    entry = {"kr": name in KR_AREAS, "bbox": bbox, "summary": summary, "top_sequences": top}
    print(f"{name}: {summary['recent_on_foot_flat']} recent pedestrian flat photos "
          f"({summary['total']} total{', capped' if summary['capped'] else ''}), {len(top)} candidate sequences")
    if name not in KR_AREAS:
        return entry
    for seq in top:
        frames = mapillary.sequence_frames(seq["sequence"])
        seq["frames"] = len(frames)
        seq["km"] = round(mapillary.track_km([mapillary.frame_record(f) for f in frames]), 3)
        if not frames:
            continue
        picks = sorted({round(i * (len(frames) - 1) / (SHEET_PHOTOS - 1)) for i in range(SHEET_PHOTOS)})
        paths = []
        for i in picks:
            path = photos / f"{seq['sequence']}_{i:04d}.jpg"
            mapillary.download(frames[i].get("thumb_1024_url"), path)
            paths.append(path)
        contact_sheet(paths, [str(i) for i in picks], sheets / f"{name}_{seq['sequence']}.jpg")
    return entry


def coverage() -> None:
    since = mapillary.ms(SINCE)
    sheets = config.OUT / "sheets"
    photos = config.OUT / "tmp" / "sheet_photos"
    sheets.mkdir(exist_ok=True)
    photos.mkdir(parents=True, exist_ok=True)
    report = {"since": SINCE, "areas": {}}
    for name, bbox in AREAS.items():
        try:
            report["areas"][name] = _survey_area(name, bbox, since, sheets, photos)
        except Exception as e:  # one failing area must not abort the whole survey
            report["areas"][name] = {"kr": name in KR_AREAS, "bbox": bbox, "error": f"{type(e).__name__}: {e}"}
            print(f"{name}: failed: {type(e).__name__}: {e}")
        _write_report(report)
    print("best Korean area:", report["kr_best"])


def _decodes(path: Path) -> bool:
    return cv2.imread(str(path)) is not None


def _fetch_frame(frame: dict, path: Path) -> bool:
    """Download one photo into path. A stale or undecodable file is removed and counts as a failure."""
    path.unlink(missing_ok=True)
    if mapillary.download(frame.get("thumb_2048_url"), path) and _decodes(path):
        return True
    path.unlink(missing_ok=True)
    return False


def build(name: str, sequence_id: str, start: int, count: int) -> None:
    frames = mapillary.sequence_frames(sequence_id)[start:start + count]
    if not frames:
        raise SystemExit("no photos in that range")
    folder = config.CLIPS / name
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / f"{i:04d}.jpg" for i in range(len(frames))]
    failed = [i for i, frame in enumerate(frames) if not _fetch_frame(frame, paths[i])]
    if failed:
        # thumbnail URLs expire, so look the sequence up again and retry only the failed photos once
        fresh = {str(f["id"]): f for f in mapillary.sequence_frames(sequence_id)}
        for i in failed:
            _fetch_frame(fresh.get(str(frames[i]["id"]), frames[i]), paths[i])
        failed = [i for i in failed if not paths[i].exists()]
    total = len(frames)
    print(f"downloaded {total - len(failed)}/{total}, failed {len(failed)}")
    records = [mapillary.frame_record(f) for f in frames]
    missing_creator = [i for i, r in enumerate(records) if not r["creator"]]
    if missing_creator:
        print(f"warning: {len(missing_creator)} photos have no creator name (indices {missing_creator})")
    meta = {
        "sequence": sequence_id,
        "start": start,
        "license": "CC BY-SA 4.0, Mapillary contributors",
        "creators": sorted({r["creator"] for r in records if r["creator"]}),
        "km": round(mapillary.track_km(records), 3),
        "failed": failed,
        "missing_creator": missing_creator,
        "frames": records,
    }
    (folder / "frames.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    video.images_to_mp4(paths, config.CLIPS / f"{name}.mp4", fps=config.CLIP_FPS)
    print(f"{name}.mp4: {total} photos, {meta['km']} km, creators {meta['creators']}")
    if len(failed) > MAX_FAILED_SHARE * total:
        print(f"error: {len(failed)} of {total} photos missing (over {MAX_FAILED_SHARE:.0%}); "
              f"failed indices are in frames.json")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage")
    b = sub.add_parser("build")
    b.add_argument("name")
    b.add_argument("sequence_id")
    b.add_argument("--start", type=int, default=0)
    b.add_argument("--count", type=int, default=180)
    args = parser.parse_args()
    if args.cmd == "coverage":
        coverage()
    else:
        build(args.name, args.sequence_id, args.start, args.count)


if __name__ == "__main__":
    main()
