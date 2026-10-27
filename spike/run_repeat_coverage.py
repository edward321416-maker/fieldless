"""Spike: in the demo areas, how many sidewalk spots have flat Mapillary photos from different dates?

Metadata only (free). Writes spike/out/repeat_coverage.json and prints a summary per area.

    python -m spike.run_repeat_coverage
"""
import json
import statistics

from spike import config, mapillary
from spike.fetch_clips import AREAS
from spike.repeat_spots import cell_key, multi_date_cells

FIELDS = "id,sequence,captured_at,is_pano,on_foot,geometry,computed_geometry"
SINCE = mapillary.ms("2020-01-01")
CELL_M = 15
GAPS_DAYS = (30, 180, 365)


def fetch(bbox: tuple[float, float, float, float]) -> tuple[list[dict], bool]:
    """Images with geometry; a full response is split once into quadrants and merged by id."""
    mapillary.check_bbox(bbox)

    def one(b):
        params = {"bbox": ",".join(f"{v:.6f}" for v in b), "fields": FIELDS, "limit": mapillary.MAX_RESULTS}
        data = mapillary._get("images", params).get("data", [])
        return data, len(data) >= mapillary.MAX_RESULTS

    images, capped = one(bbox)
    if not capped:
        return images, False
    min_lng, min_lat, max_lng, max_lat = bbox
    mid_lng, mid_lat = (min_lng + max_lng) / 2, (min_lat + max_lat) / 2
    merged, still = {}, False
    for q in [(min_lng, min_lat, mid_lng, mid_lat), (mid_lng, min_lat, max_lng, mid_lat),
              (min_lng, mid_lat, mid_lng, max_lat), (mid_lng, mid_lat, max_lng, max_lat)]:
        found, c = one(q)
        still = still or c
        for im in found:
            merged.setdefault(str(im["id"]), im)
    return list(merged.values()), still


def summarize(images: list[dict]) -> dict:
    flat = [im for im in images if not im.get("is_pano") and (im.get("computed_geometry") or im.get("geometry"))]
    recent = [im for im in flat if im.get("captured_at", 0) >= SINCE]
    covered = {cell_key(*reversed((im.get("computed_geometry") or im["geometry"])["coordinates"]), CELL_M)
               for im in recent}
    out = {"flat_photos": len(flat), "recent_flat_photos": len(recent), "recent_cells": len(covered)}
    for gap in GAPS_DAYS:
        cells = multi_date_cells(recent, CELL_M, gap)
        gaps = sorted(c["gap_days"] for c in cells.values())
        out[f"cells_gap_{gap}d"] = len(cells)
        out[f"share_gap_{gap}d"] = round(len(cells) / len(covered), 3) if covered else 0.0
        if gaps:
            out[f"gap_days_median_{gap}d"] = statistics.median(gaps)
            out[f"gap_days_max_{gap}d"] = gaps[-1]
    on_foot = [im for im in recent if im.get("on_foot")]
    out["recent_on_foot_photos"] = len(on_foot)
    out["on_foot_cells_gap_180d"] = len(multi_date_cells(on_foot, CELL_M, 180))
    return out


def main() -> None:
    report = {"cell_m": CELL_M, "since": "2020-01-01", "areas": {}}
    for name in ("seoul_seongsu", "seoul_myeongdong"):
        images, capped = fetch(AREAS[name])
        summary = summarize(images)
        summary["capped"] = capped
        cells = multi_date_cells([im for im in images if im.get("captured_at", 0) >= SINCE], CELL_M, 180)
        top = sorted(cells.values(), key=lambda c: (-c["days"], -c["gap_days"]))[:15]
        report["areas"][name] = {"summary": summary, "top_cells_gap_180d": top}
        print(name, json.dumps(summary, ensure_ascii=False))
    (config.OUT / "repeat_coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
