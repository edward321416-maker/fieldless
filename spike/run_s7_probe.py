"""Spike: can Muse Spark judge tactile paving breaks, damage and fading on one full-resolution still?

Runs one structured Spark call per labelled street_b photo, saves answers to spike/out/s7_probe.json (resumable)
and scores them against spike/s7_labels.json. Prompt and schema come from the plan-1 author's draft probe.

    python -m spike.run_s7_probe --limit 2   # cost check on two photos
    python -m spike.run_s7_probe             # all labelled photos
"""
import argparse
import json
from pathlib import Path

from spike import config
from spike import meta_client as mc
from spike.s7_score import score

PHOTOS = config.ROOT.parent.parent.parent / "threshold" / "spike" / "clips" / "street_b"
LABELS = config.ROOT / "s7_labels.json"
RESULTS = config.OUT / "s7_probe.json"
SPARK_IN_USD, SPARK_OUT_USD = 1.25e-6, 4.25e-6  # per token, muse-spark-1.3

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tactile_visible", "line_end", "damage", "box", "confidence", "note"],
    "properties": {
        "tactile_visible": {"type": "boolean"},
        "line_end": {"type": "string", "enum": ["none", "normal", "abrupt", "unclear"]},
        "damage": {"type": "string", "enum": ["none", "broken", "missing", "worn", "covered", "unclear"]},
        "box": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
}

PROMPT = """A pedestrian took this photo on a sidewalk in Korea. Look only at the yellow tactile paving on the ground
(raised blocks for blind pedestrians: bar blocks form a guide line, dot blocks mark warnings and turns).
Answer as JSON:
- tactile_visible: whether yellow tactile paving is visible on the ground.
- line_end: if a tactile guide line ends in this photo, how it ends. "none": no end is visible (it continues, or
  leaves the picture). "normal": it ends at a crosswalk, curb, building entrance, stairs or a dot-block pad.
  "abrupt": it stops in the middle of the sidewalk for no visible reason, or a section of it is missing.
  "unclear": you cannot tell.
- damage: the worst condition of the visible tactile blocks. "none", "broken" (cracked, chipped or sunken blocks),
  "missing" (blocks missing or replaced by other paving), "worn" (color faded or bumps worn flat),
  "covered" (covered by a plate, mat, paint or debris), "unclear".
- box: the problem area (abrupt end or damage) as [x1, y1, x2, y2] on a 0-1000 scale of the image, or [] if none.
- confidence: 0 to 1 for your line_end and damage answers.
- note: one sentence on what you see."""


def labelled_ids(labels: dict) -> list[int]:
    ids = {int(k) for k in labels["structural"]} | {int(k) for k in labels["worn"]}
    ids |= {int(i) for i in labels["intact"]} | {int(k) for k in labels["excluded"]}
    return sorted(ids)


def spark_cost(entries: list[dict]) -> float:
    total = 0.0
    for e in entries:
        u = e.get("usage") or {}
        total += u.get("input_tokens", 0) * SPARK_IN_USD + u.get("output_tokens", 0) * SPARK_OUT_USD
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after this many new calls (0 = no limit)")
    args = ap.parse_args()

    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    rows = {int(k): v for k, v in json.loads(RESULTS.read_text(encoding="utf-8")).items()} if RESULTS.exists() else {}
    log_start = sum(1 for _ in open(config.OUT / "calls.jsonl", encoding="utf-8")) if (config.OUT / "calls.jsonl").exists() else 0

    new_calls = 0
    for i in labelled_ids(labels):
        if i in rows and "error" not in rows[i]:
            continue
        if args.limit and new_calls >= args.limit:
            break
        try:
            rows[i] = mc.spark_json([mc.image_part(PHOTOS / f"{i:04d}.jpg"), mc.text_part(PROMPT)], "tactile_condition", SCHEMA)
        except Exception as exc:  # noqa: BLE001 - a failed photo is recorded and the run goes on
            rows[i] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        new_calls += 1
        print(i, json.dumps(rows[i], ensure_ascii=False), flush=True)
        RESULTS.write_text(json.dumps({str(k): v for k, v in sorted(rows.items())}, ensure_ascii=False, indent=1),
                           encoding="utf-8")

    entries = [json.loads(line) for line in open(config.OUT / "calls.jsonl", encoding="utf-8")] if (config.OUT / "calls.jsonl").exists() else []
    run_entries = [e for e in entries[log_start:] if e.get("kind") == "spark" and e.get("schema") == "tactile_condition"]
    all_entries = [e for e in entries if e.get("kind") == "spark" and e.get("schema") == "tactile_condition"]
    print(f"this run: {len(run_entries)} calls, ${spark_cost(run_entries):.4f}; "
          f"all s7 calls: {len(all_entries)}, ${spark_cost(all_entries):.4f}")
    print(json.dumps(score(rows, labels), ensure_ascii=False))


if __name__ == "__main__":
    main()
