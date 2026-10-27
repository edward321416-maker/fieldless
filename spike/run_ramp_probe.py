"""Plan 4 Task 0 gate: does SAM 3.1 find curb ramps in Seattle pedestrian photos, and does Muse Spark tell whether a
ramp has a detectable warning? Truth: data/seattle/probe_set.json (the 2d session looked at each photo at full
resolution; the city's DETWARN_EXISTS is kept beside it).

  python -m spike.run_ramp_probe [--limit N]
"""
import argparse
import json
from pathlib import Path

from pipeline import collect, config
from pipeline.cost import CostMeter
from pipeline.meta_api import MetaApi, image_part, text_part
from pipeline.sam_output import confident, parse

PHRASES = ["curb cut"]  # the first run (ramp_probe_phrases.md) tried "curb ramp", "curb cut", "sidewalk ramp"
ASK = ("Look at the curb ramp where the sidewalk meets the street. Does it have a detectable warning surface "
       "(a panel of raised truncated domes, usually yellow) at its bottom edge?")
FIND = "curb cut"
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["visible", "answer", "box", "confidence", "note"],
    "properties": {"visible": {"type": "boolean"}, "answer": {"type": "string", "enum": ["yes", "no", "unclear"]},
                   "box": {"type": "array", "items": {"type": "integer"}}, "confidence": {"type": "number"},
                   "note": {"type": "string"}},
}
PROMPT = """A pedestrian took this photo on a sidewalk.
{ask}
Answer as JSON:
- visible: whether the {find} is visible in the photo.
- answer: "yes", "no", or "unclear" if the photo does not show it well enough to tell.
- box: the {find} as [x1, y1, x2, y2] on a 0-1000 scale of the image, or [] if it is not visible.
- confidence: 0 to 1 for your answer.
- note: one sentence on what you see."""
OUT = Path(__file__).resolve().parent / "out"


def truth(p: dict) -> dict:
    """The 2d probe-set record in the names this script uses."""
    city = p.get("city") or {}  # the negatives (no ramp in frame) may carry no city record
    return {"image_id": p["photo"], "ramp_id": city.get("unitid") or city.get("objectid") or "-",
            "detwarn_city": city.get("detwarn_exists") or "-", "ramp_visible": p["ramp_visible"] == "yes",
            "domes_seen": p.get("warning_seen")}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    probe = [truth(p) for p in json.loads((config.data_dir() / "seattle" / "probe_set.json").read_text(encoding="utf-8"))]
    if args.limit:
        probe = probe[:args.limit]
    OUT.mkdir(exist_ok=True)
    api = MetaApi(OUT / "ramp_probe_calls.jsonl", CostMeter(cap_usd=1.5))
    rows = []
    for p in probe:
        photo = collect.photo_path(p["image_id"])
        found = {}
        for phrase in PHRASES:
            segs = confident(parse(api.sam_image(phrase, photo), video=False))
            found[phrase] = max((s.area for s in segs), default=0)
        answer = api.spark_json([image_part(photo), text_part(PROMPT.format(ask=ASK, find=FIND))], "survey_check",
                                SCHEMA)
        rows.append({**p, "sam_area": found, "spark": answer})
        print(p["image_id"], p["ramp_visible"], p["domes_seen"], found, answer["answer"], flush=True)
    (OUT / "ramp_probe.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    visible = [r for r in rows if r["ramp_visible"]]
    lines = ["# Ramp probe (plan 4 Task 0)", "",
             f"Photos: {len(rows)}, ramp visible: {len(visible)}. Cost: ${api.meter.spent_usd:.2f}.", "",
             "| phrase | found on visible | found on not visible |", "|---|---|---|"]
    for phrase in PHRASES:
        hit = sum(r["sam_area"][phrase] > 0 for r in visible)
        false = sum(r["sam_area"][phrase] > 0 for r in rows if not r["ramp_visible"])
        lines.append(f"| {phrase} | {hit}/{len(visible)} | {false}/{len(rows) - len(visible)} |")
    judged = [r for r in visible if r["domes_seen"] in ("yes", "no")]
    right = sum(r["spark"]["answer"] == r["domes_seen"] for r in judged)
    unclear = sum(r["spark"]["answer"] == "unclear" for r in judged)
    city = sum(r["spark"]["answer"] == ("yes" if r["detwarn_city"] == "Y" else "no") for r in judged)
    lines += ["", f"Spark on visible ramps whose domes were judged yes/no by eye: {right}/{len(judged)} right, "
                  f"{unclear} unclear; agrees with the city record on {city}/{len(judged)}.", "",
              "| photo | ramp | city DW | seen | Spark | conf | note |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["spark"]
        lines.append(f"| {r['image_id']} | {r['ramp_id']} | {r['detwarn_city']} | "
                     f"{r['domes_seen'] if r['ramp_visible'] else 'not visible'} | {s['answer']} | "
                     f"{s['confidence']:.2f} | {s['note']} |")
    (OUT / "ramp_probe.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
