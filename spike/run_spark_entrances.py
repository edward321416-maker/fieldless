"""Gate test 1: can Muse Spark list entrances with timestamps read from a burned-in timer?"""
import json
from pathlib import Path

from spike import config, video
from spike import meta_client as mc
from spike.truth import load_truth, match_times

CHUNK_S = 60.0  # 60 photos per request at 1 fps
TOLERANCE_S = 2.0  # ±2 photos

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entrances", "obstacle_types"],
    "properties": {
        "entrances": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["t", "side", "name", "category", "has_visible_steps"],
                "properties": {
                    "t": {"type": "number"},
                    "side": {"type": "string", "enum": ["left", "right", "center"]},
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "has_visible_steps": {"type": "boolean"},
                },
            },
        },
        "obstacle_types": {"type": "array", "items": {"type": "string"}},
    },
}

PROMPT = """You are surveying street accessibility from street-level photos taken by a pedestrian, shown as a video at one photo per second.
A timer in seconds is burned into the top-left corner of every frame; it equals the photo number.
1) List every shop or building entrance (door) the photos pass. For each, set t to the timer value shown when that entrance is most clearly and frontally visible. Read t from the timer; do not estimate it.
   Also give the side of the frame (left, right, center), the sign name as written (keep the original language), a short English business category, and whether steps in front of the door are visible.
2) List the kinds of objects standing on the sidewalk that could block a pedestrian or tactile paving, as short English noun phrases such as "electric scooter", "bicycle", "sandwich board sign", "stacked boxes". Empty list if none.
Return JSON only."""


def run_clip(path: Path) -> dict:
    tmp = config.OUT / "tmp"
    tmp.mkdir(exist_ok=True)
    info = video.probe(path)
    entrances: list[dict] = []
    obstacle_types: set[str] = set()
    start = 0.0
    while start < info.duration_s - 0.5:
        end = min(start + CHUNK_S, info.duration_s)
        chunk = video.cut(path, tmp / f"{path.stem}_{int(start)}.mp4", start, end)
        stamped = video.burn_timecode(chunk, tmp / f"{path.stem}_{int(start)}_tc.mp4", offset_s=start)
        file_id = mc.upload(stamped)
        try:
            result = mc.spark_json([mc.text_part(PROMPT), mc.file_part(file_id)], "street_scan", SCHEMA)
        finally:
            mc.delete_file(file_id)
        entrances += result["entrances"]
        obstacle_types |= {o.strip().lower() for o in result["obstacle_types"] if o.strip()}
        start = end
    return {"duration_s": info.duration_s, "entrances": entrances, "obstacle_types": sorted(obstacle_types)}


def main() -> None:
    report = {}
    for clip in load_truth():
        result = run_clip(config.CLIPS / clip.file)
        hits, misses, false_pos = match_times(
            [e["t"] for e in result["entrances"]], [e.t for e in clip.entrances], tol=TOLERANCE_S
        )
        result.update(hits=hits, misses=misses, false_positives=false_pos, truth_count=len(clip.entrances))
        report[clip.file] = result
        print(f"{clip.file}: hits {hits}/{len(clip.entrances)}, false positives {false_pos}, "
              f"obstacle types {result['obstacle_types']}")
    (config.OUT / "spark_entrances.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
