"""Find Mapillary pedestrian photo sequences and turn one into a test clip (one photo per second).

  python -m spike.fetch_clips coverage
  python -m spike.fetch_clips build street_a <sequence_id> [--start 0] [--count 180]
"""
import argparse
import json

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


def coverage() -> None:
    since = mapillary.ms(SINCE)
    sheets = config.OUT / "sheets"
    photos = config.OUT / "tmp" / "sheet_photos"
    sheets.mkdir(exist_ok=True)
    photos.mkdir(parents=True, exist_ok=True)
    report = {"since": SINCE, "areas": {}}
    for name, bbox in AREAS.items():
        images = mapillary.search_images(bbox)
        summary = mapillary.summarize(images, since)
        top = mapillary.rank_sequences(images, since)[:TOP_SEQUENCES]
        report["areas"][name] = {"kr": name in KR_AREAS, "bbox": bbox, "summary": summary, "top_sequences": top}
        print(f"{name}: {summary['recent_on_foot_flat']} recent pedestrian flat photos "
              f"({summary['total']} total{', capped' if summary['capped'] else ''}), {len(top)} candidate sequences")
        if name not in KR_AREAS:
            continue
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
    best_count, best_area = max((v["summary"]["recent_on_foot_flat"], k)
                                for k, v in report["areas"].items() if v["kr"])
    report["kr_best"] = {"area": best_area, "recent_on_foot_flat": best_count}
    (config.OUT / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("best Korean area:", report["kr_best"])


def build(name: str, sequence_id: str, start: int, count: int) -> None:
    frames = mapillary.sequence_frames(sequence_id)[start:start + count]
    if not frames:
        raise SystemExit("no photos in that range")
    folder = config.CLIPS / name
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, frame in enumerate(frames):
        path = folder / f"{i:04d}.jpg"
        if not mapillary.download(frame.get("thumb_2048_url"), path):
            print(f"photo {i} ({frame['id']}) failed to download; it becomes a black frame")
        paths.append(path)
    records = [mapillary.frame_record(f) for f in frames]
    meta = {
        "sequence": sequence_id,
        "start": start,
        "license": "CC BY-SA 4.0, Mapillary contributors",
        "creators": sorted({r["creator"] for r in records if r["creator"]}),
        "km": round(mapillary.track_km(records), 3),
        "frames": records,
    }
    (folder / "frames.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    video.images_to_mp4(paths, config.CLIPS / f"{name}.mp4", fps=config.CLIP_FPS)
    print(f"{name}.mp4: {len(paths)} photos, {meta['km']} km, creators {meta['creators']}")


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
