"""Spec section 20 fallback re-test for the two core metrics the D1 gate failed, on the same frozen labels.

Stairs (every labeled entrance; the clip photo at the labeled t, full resolution):
  concept: SAM `stairs` on the still photo (the gate used 1280-wide video windows).
  box:     Spark says whether there is a step or sill and boxes it (0-1000 scale); if so, SAM is prompted with
           that box. A hit is a confident mask.
Tactile (clips with tactile labels; every labeled tactile photo and every NEG_STEP-th other photo):
  concept: SAM `yellow tactile paving` and `tactile paving` on the still photo; a hit is a confident mask from
           either phrase (the gate's union rule).
  box:     Spark boxes the tactile paving on a SPARK_TACTILE_WIDTH-wide copy; SAM is prompted with the boxes on
           the full photo.
Hit rates on positives (stepped entrances, tactile photos) compare with the gate thresholds. The same rates on
negatives (flat entrances, other photos) are false-positive rates, which the gate did not measure.

  python -m spike.run_box_retest --clips PATH [--part stairs|tactile|all]

Output: spike/out/box_retest.json (rewritten after every photo; a rerun of one part keeps the other part's rows
when truth.json is unchanged), spike/out/box_retest.md, and raw SAM text in spike/out/box_retest/.
"""
import argparse
import json
from pathlib import Path

import cv2

from spike import config
from spike import meta_client as mc
from spike.console import utf8_stdout
from spike.gate_report import _spark_cost
from spike.sam_parse import check_frames, confident, parse_sam_output
from spike.truth import in_ranges, load_truth, truth_sha256

STAIR_PHRASE = "stairs"  # the gate's best phrase
TACTILE_PHRASES = ["yellow tactile paving", "tactile paving"]  # the gate's two phrases that found anything
CONF = 0.5
MAX_BOXES = 16  # SAM accepts at most 16 prompted objects
NEG_STEP = 2
SPARK_TACTILE_WIDTH = 1280
STAIRS_THRESHOLD = 0.7  # the gate's thresholds (spec section 20)
TACTILE_THRESHOLD = 0.8
SAM_PER_IMAGE = 0.0025  # $2.50 per 1k images (dev.meta.ai/models/sam-3-1)
METHODS = ("concept", "box", "either")

BOX = {"type": "object", "additionalProperties": False, "required": ["x1", "y1", "x2", "y2"],
       "properties": {k: {"type": "integer"} for k in ("x1", "y1", "x2", "y2")}}
STEP_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["has_step", "step_box", "notes"],
               "properties": {"has_step": {"type": "boolean"}, "step_box": BOX, "notes": {"type": "string"}}}
TACTILE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["boxes"],
                  "properties": {"boxes": {"type": "array", "items": BOX}}}
STEP_PROMPT = ("This street photo shows a shop or building entrance seen from the sidewalk. Look at the most clearly "
               "visible entrance. Is there any step, sill or raised threshold between the sidewalk and the door? "
               "If yes, give one bounding box around all the steps or the sill, in coordinates from 0 to 1000 "
               "relative to the image width (x) and height (y), origin top-left. If no, set has_step false and "
               "the box to zeros. Return JSON only.")
TACTILE_PROMPT = ("List bounding boxes of every stretch of tactile paving visible on the ground in this street "
                  "photo: the raised guide blocks for blind pedestrians, usually yellow but sometimes faded to "
                  "ochre or brown. Coordinates from 0 to 1000 relative to the image width (x) and height (y), "
                  "origin top-left. At most 8 boxes. Empty list if there is none. Return JSON only.")


def to_pixels(box: dict, w: int, h: int) -> tuple[int, int, int, int] | None:
    """A 0-1000 box as inclusive pixel corners clamped to the image, or None if it has no area."""
    x1, x2 = (min(w - 1, max(0, int(box[k] * w / 1000))) for k in ("x1", "x2"))
    y1, y2 = (min(h - 1, max(0, int(box[k] * h / 1000))) for k in ("y1", "y2"))
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def box_prompt(boxes: list[tuple[int, int, int, int]], w: int, h: int) -> str:
    """SAM box-prompt text for one image: objects 1..n on frame 0 (dev.meta.ai/docs/sam/segmenting)."""
    return "<0f>" + ",".join(f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}|>"
                             for i, (x1, y1, x2, y2) in enumerate(boxes[:MAX_BOXES], 1))


def negative_photos(photos: int, ranges: list[tuple[float, float]], step: int) -> list[int]:
    outside = [k for k in range(photos) if not in_ranges(k, ranges)]
    return outside[::step]


def _any_hit(values) -> bool | None:
    """True if any request hit; False if all were scored and none hit; None if one failed and none hit."""
    values = list(values)
    if True in values:
        return True
    return False if None not in values else None


def _either(row: dict) -> bool | None:
    return _any_hit((row.get("concept"), row.get("box")))


def _rate(rows: list[dict], method: str, positive: bool) -> tuple[float | None, int]:
    values = [_either(r) if method == "either" else r.get(method) for r in rows if r["positive"] == positive]
    scored = [v for v in values if v is not None]
    return (sum(scored) / len(scored) if scored else None), len(scored)


def summarize_rows(rows: list[dict]) -> dict:
    out = {}
    for method in METHODS:
        hit, positives = _rate(rows, method, True)
        fp, negatives = _rate(rows, method, False)
        out[method] = {"hit_rate": hit, "positives": positives, "false_positive_rate": fp, "negatives": negatives}
    return out


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def _sam_hit(prompt: str, image: dict, raw_path: Path) -> bool:
    text = mc.sam(prompt, image, images=1)
    raw_path.write_text(text, encoding="utf-8")  # before parsing, so a parse error keeps the paid answer
    parsed = parse_sam_output(text)
    check_frames(parsed, 1)
    return bool(confident(parsed, CONF))


def _size(photo: Path) -> tuple[int, int]:
    h, w = cv2.imread(str(photo)).shape[:2]
    return w, h


def stairs_row(clip_file: str, index: int, entrance, clips_dir: Path, raw_dir: Path) -> dict:
    stem = Path(clip_file).stem
    photo = clips_dir / stem / f"{int(entrance.t):04d}.jpg"
    row = {"clip": clip_file, "index": index, "t": entrance.t, "name": entrance.name,
           "step_count": entrance.step_count, "positive": entrance.step_count >= 1}
    w, h = _size(photo)
    image = mc.image_part(photo)
    try:
        row["concept"] = _sam_hit(STAIR_PHRASE, image, raw_dir / f"{stem}_e{index}_concept.txt")
    except Exception as exc:  # one failed request is recorded, never counted as a miss
        row["concept"], row["concept_error"] = None, _err(exc)
    try:
        spark = mc.spark_json([mc.text_part(STEP_PROMPT), image], "step_box", STEP_SCHEMA)
        row["spark"] = spark
        px = to_pixels(spark["step_box"], w, h) if spark["has_step"] else None
        row["box_px"] = px
        row["box"] = _sam_hit(box_prompt([px], w, h), image, raw_dir / f"{stem}_e{index}_box.txt") if px else False
    except Exception as exc:
        row["box"], row["box_error"] = None, _err(exc)
    return row


def tactile_row(clip_file: str, k: int, ranges, clips_dir: Path, raw_dir: Path, tmp: Path) -> dict:
    stem = Path(clip_file).stem
    photo = clips_dir / stem / f"{k:04d}.jpg"
    row = {"clip": clip_file, "photo": k, "positive": in_ranges(k, ranges)}
    w, h = _size(photo)
    image = mc.image_part(photo)
    by_phrase: dict[str, bool | None] = {}
    for phrase in TACTILE_PHRASES:
        try:
            by_phrase[phrase] = _sam_hit(phrase, image, raw_dir / f"{stem}_{k:04d}_{phrase.replace(' ', '_')}.txt")
        except Exception as exc:
            by_phrase[phrase] = None
            row.setdefault("concept_errors", {})[phrase] = _err(exc)
    row["concept_by_phrase"] = by_phrase
    row["concept"] = _any_hit(by_phrase.values())
    try:
        small = tmp / f"{stem}_{k:04d}_spark.jpg"
        im = cv2.imread(str(photo))
        cv2.imwrite(str(small), cv2.resize(im, (SPARK_TACTILE_WIDTH, round(h * SPARK_TACTILE_WIDTH / w)),
                                           interpolation=cv2.INTER_AREA))
        spark = mc.spark_json([mc.text_part(TACTILE_PROMPT), mc.image_part(small)], "tactile_boxes", TACTILE_SCHEMA)
        px = [p for b in spark["boxes"] if (p := to_pixels(b, w, h))][:MAX_BOXES]
        row["spark_boxes"], row["box_px"] = spark["boxes"], px
        row["box"] = _sam_hit(box_prompt(px, w, h), image, raw_dir / f"{stem}_{k:04d}_box.txt") if px else False
    except Exception as exc:
        row["box"], row["box_error"] = None, _err(exc)
    return row


def run_cost(calls_path: Path) -> dict:
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    spark = sum(_spark_cost(c) for c in calls if c["kind"] == "spark")
    sam = sum(c.get("billed_images", 0) * SAM_PER_IMAGE for c in calls if c["kind"] == "sam")
    failed = sum(c["kind"] in ("sam_failed", "spark_failed") for c in calls)
    return {"spark_usd": spark, "sam_usd": sam, "total_usd": spark + sam, "failed_attempts": failed}


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def markdown(report: dict) -> str:
    lines = ["# Box re-test (spec section 20 fallbacks)", "",
             "Same frozen labels as the D1 gate. Hit rate is on positives (stepped entrances, labeled tactile "
             "photos); false-positive rate is on negatives (flat entrances, other photos).", "",
             "| Test | Method | Hit rate | Positives | Threshold | Result | False-positive rate | Negatives |",
             "|---|---|---|---|---|---|---|---|"]
    for part, threshold in (("stairs", STAIRS_THRESHOLD), ("tactile", TACTILE_THRESHOLD)):
        summary = report.get("summary", {}).get(part)
        if not summary:
            continue
        for method in METHODS:
            s = summary[method]
            result = "n/a" if s["hit_rate"] is None else ("PASS" if s["hit_rate"] >= threshold else "FAIL")
            lines.append(f"| {part} | {method} | {_pct(s['hit_rate'])} | {s['positives']} | {threshold} | "
                         f"{result} | {_pct(s['false_positive_rate'])} | {s['negatives']} |")
    cost = report.get("cost")
    if cost:
        lines += ["", f"Cost: ${cost['total_usd']:.2f} (Spark ${cost['spark_usd']:.2f}, SAM ${cost['sam_usd']:.2f}); "
                      f"failed attempts: {cost['failed_attempts']}"]
    lines += ["", "## Notes", "", "- Filled in by hand after the run."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clips", type=Path, default=config.CLIPS, help="folder with truth.json and clip photos")
    parser.add_argument("--part", choices=("stairs", "tactile", "all"), default="all")
    args = parser.parse_args(argv)
    truth_file = args.clips / "truth.json"
    sha = truth_sha256(truth_file)
    out_path = config.OUT / "box_retest.json"
    report: dict = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    if report.get("_meta", {}).get("truth_sha256") != sha:
        report = {}
    report["_meta"] = {"truth_sha256": sha}
    raw_dir = config.OUT / "box_retest"
    raw_dir.mkdir(exist_ok=True)
    tmp = config.OUT / "tmp"
    tmp.mkdir(exist_ok=True)
    clips = load_truth(truth_file)

    def save() -> None:
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.part in ("stairs", "all"):
        report["stairs"] = []
        for clip in clips:
            for index, entrance in enumerate(clip.entrances):
                row = stairs_row(clip.file, index, entrance, args.clips, raw_dir)
                report["stairs"].append(row)
                save()
                print("stairs", clip.file, entrance.t, entrance.step_count, "concept", row["concept"], "box", row["box"])
        report.setdefault("summary", {})["stairs"] = summarize_rows(report["stairs"])
    if args.part in ("tactile", "all"):
        report["tactile"] = []
        for clip in clips:
            if not clip.tactile_ranges:
                continue
            photos = len(list((args.clips / Path(clip.file).stem).glob("*.jpg")))
            positives = [k for k in range(photos) if in_ranges(k, clip.tactile_ranges)]
            for k in sorted(positives + negative_photos(photos, clip.tactile_ranges, NEG_STEP)):
                row = tactile_row(clip.file, k, clip.tactile_ranges, args.clips, raw_dir, tmp)
                report["tactile"].append(row)
                save()
                print("tactile", clip.file, k, "positive", row["positive"], "concept", row["concept"], "box", row["box"])
        report.setdefault("summary", {})["tactile"] = summarize_rows(report["tactile"])
    calls_path = config.OUT / "calls.jsonl"
    if calls_path.exists():
        report["cost"] = run_cost(calls_path)
    save()
    (config.OUT / "box_retest.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report.get("summary"), indent=1), report.get("cost"))


if __name__ == "__main__":
    main()
