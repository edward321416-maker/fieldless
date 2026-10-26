"""Gate test 3: can Muse Spark count entrance steps from the best SAM frame?"""
import json
from pathlib import Path

from spike import config, mapillary, video
from spike import meta_client as mc
from spike.console import utf8_stdout
from spike.truth import load_truth

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["step_count", "has_ramp", "door_type", "confidence", "notes"],
    "properties": {
        "step_count": {"type": "integer"},
        "has_ramp": {"type": "boolean"},
        "door_type": {"type": "string", "enum": ["swing", "sliding", "automatic", "revolving", "none_visible"]},
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
}

PROMPT = """This photo shows a shop entrance seen from the sidewalk.
Count the steps (risers) a wheelchair user would have to climb from the sidewalk up to the door threshold.
A raised threshold or lip of a few centimeters counts as 1 step. If the entrance is level with the sidewalk, return 0.
Also report whether a ramp is present, the door type, your confidence from 0 to 1, and a one-sentence note on what you counted.
Return JSON only."""

SAVE_EVERY = 5
RESULT_KEYS = ("step_count", "has_ramp", "door_type", "confidence", "notes")


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def clip_photo(clip_file: str, t: float, dst: Path) -> tuple[Path, str | None]:
    """Full-resolution Mapillary photo at time t if the clip came from a sequence, else a video frame."""
    frames_path = config.CLIPS / Path(clip_file).stem / "frames.json"
    if frames_path.exists():
        frames = json.loads(frames_path.read_text(encoding="utf-8"))["frames"]
        frame = frames[min(len(frames) - 1, max(0, round(t * config.CLIP_FPS)))]
        return mapillary.original_image(frame["id"], dst), frame["id"]
    return video.frame_at(config.CLIPS / clip_file, t, dst), None


def write_results(rows: list[dict]) -> float:
    """Write step_count.json. Accuracy is over rows without an error; scored and failed count them."""
    scored = [r for r in rows if "error" not in r]
    accuracy = sum(bool(r["correct"]) for r in scored) / len(scored) if scored else 0.0
    (config.OUT / "step_count.json").write_text(
        json.dumps({"rows": rows, "accuracy": accuracy, "scored": len(scored),
                    "failed": len(rows) - len(scored)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return accuracy


def main() -> None:
    utf8_stdout()
    stairs = json.loads((config.OUT / "sam_stairs.json").read_text(encoding="utf-8"))
    best_phrase = stairs["best_phrase"]
    truth = {(c.file, i): e for c in load_truth() for i, e in enumerate(c.entrances)}
    frames_dir = config.OUT / "frames"
    frames_dir.mkdir(exist_ok=True)
    rows = []
    for n, r in enumerate(stairs["rows"], start=1):
        entrance = truth[(r["clip"], r["index"])]
        row = {"clip": r["clip"], "index": r["index"], "truth_steps": entrance.step_count,
               "image_id": None, **{k: None for k in RESULT_KEYS}, "correct": None}
        try:
            t = (r["phrases"].get(best_phrase) or {}).get("best_frame_t")
            t = entrance.t if t is None else t
            image, image_id = clip_photo(r["clip"], t, frames_dir / f"{Path(r['clip']).stem}_e{r['index']}.jpg")
            row["image_id"] = image_id
            result = mc.spark_json([mc.text_part(PROMPT), mc.image_part(image)], "entrance_read", SCHEMA)
            row.update({k: result[k] for k in RESULT_KEYS})
            row["correct"] = result["step_count"] == entrance.step_count
        except Exception as exc:  # one entrance must not abort the run
            row["error"] = _err(exc)
        rows.append(row)
        if n % SAVE_EVERY == 0:
            write_results(rows)
        # printed outside the try, so a console failure can never turn a scored row into an error row
        if "error" in row:
            print(entrance.name, "error", row["error"])
        else:
            print(entrance.name, "truth", entrance.step_count, "pred", row["step_count"], "conf", row["confidence"])
    accuracy = write_results(rows)
    print("accuracy", accuracy)


if __name__ == "__main__":
    main()
