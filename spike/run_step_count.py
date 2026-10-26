"""Gate test 3: can Muse Spark count entrance steps from the best SAM frame?

Output contract (spike/out/step_count.json): rows (one per sam_stairs.json row; an "error" key marks a row that
was not scored, including rows whose sam_stairs entry no longer matches truth.json), accuracy over rows without an
error, scored and failed counts, and truth_sha256 of the truth.json the run used.
"""
import json
from pathlib import Path

from spike import config, mapillary, video
from spike import meta_client as mc
from spike.console import utf8_stdout
from spike.truth import load_truth, truth_sha256

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


def write_results(rows: list[dict], truth_hash: str) -> float:
    """Write step_count.json. Accuracy is over rows without an error; scored and failed count them."""
    scored = [r for r in rows if "error" not in r]
    accuracy = sum(bool(r["correct"]) for r in scored) / len(scored) if scored else 0.0
    (config.OUT / "step_count.json").write_text(
        json.dumps({"rows": rows, "accuracy": accuracy, "scored": len(scored),
                    "failed": len(rows) - len(scored), "truth_sha256": truth_hash}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return accuracy


def stale_truth_error(r: dict, entrance) -> str | None:
    """Why a sam_stairs.json row no longer matches the current truth.json, or None if it still does.

    A row joins its truth entrance by (clip, index). If truth.json was edited after run_sam_stairs, the same index
    can point at another entrance, so paying Spark for it would score the wrong label.
    """
    if entrance is None:
        return "truth mismatch: sam_stairs.json is stale (entrance not in truth.json)"
    if (r.get("t"), r.get("step_count")) != (entrance.t, entrance.step_count):
        return (f"truth mismatch: sam_stairs.json is stale (t, step_count) {r.get('t'), r.get('step_count')} "
                f"vs truth {entrance.t, entrance.step_count}")
    return None


def main() -> None:
    utf8_stdout()
    stairs = json.loads((config.OUT / "sam_stairs.json").read_text(encoding="utf-8"))
    best_phrase = stairs["best_phrase"]
    truth_hash = truth_sha256()
    if stairs.get("truth_sha256") != truth_hash:
        print("warning: sam_stairs.json was made from a different truth.json; "
              "rows whose entrance changed are recorded as errors, not sent to Spark")
    truth = {(c.file, i): e for c in load_truth() for i, e in enumerate(c.entrances)}
    frames_dir = config.OUT / "frames"
    frames_dir.mkdir(exist_ok=True)
    rows = []
    for n, r in enumerate(stairs["rows"], start=1):
        entrance = truth.get((r["clip"], r["index"]))
        row = {"clip": r["clip"], "index": r["index"], "truth_steps": entrance.step_count if entrance else None,
               "image_id": None, **{k: None for k in RESULT_KEYS}, "correct": None}
        stale = stale_truth_error(r, entrance)
        if stale:
            row["error"] = stale  # checked before spending on the row
        else:
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
            write_results(rows, truth_hash)
        # printed outside the try, so a console failure can never turn a scored row into an error row
        name = entrance.name if entrance else f"{r['clip']} #{r['index']}"
        if "error" in row:
            print(name, "error", row["error"])
        else:
            print(name, "truth", entrance.step_count, "pred", row["step_count"], "conf", row["confidence"])
    accuracy = write_results(rows, truth_hash)
    print("accuracy", accuracy)


if __name__ == "__main__":
    main()
