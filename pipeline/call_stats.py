"""How one survey used the models: calls, call time and cost per stage and model, from the call logs.

    python -m pipeline.call_stats --run demo --scan 17 \\
        --window calls.jsonl 2026-10-29T01:14:00Z 2026-10-29T01:23:00Z \\
        --window calls.jsonl 2026-10-29T06:33:00Z 2026-10-29T06:34:00Z

The logs (calls.jsonl, and calls_open.jsonl beside each for Muse Spark Open) do not name the survey a call was for,
so the windows say when the survey ran: one survey runs at a time in a run folder, and a later rerun or report text
is another window. A log path is taken relative to the run folder. The result is written to
<run>/call_stats_scan<N>.json, which the app serves with the survey (/api/scans/{id}/calls), as it serves the
comparison with the city's records. It is made offline for the surveys shown as demos; the pipeline is not changed.

Each call's stage comes from what it asked: SAM 3.1 with a survey item's phrase or video is "Survey items", with
"person" or "license plate" is blurring the evidence photos, with a box or "stairs" is "Entrances", and with the
walking surface or another object is "Sidewalk"; each Muse Spark schema belongs to one stage. "seconds" adds up each
call's own duration; calls run side by side, so the sum is longer than the survey's processing time.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline import config, db, survey_items

STAGES = [("scene", "Scene: entrances and objects in each walk's video"), ("sidewalk", "Sidewalk"),
          ("items", "Survey items"), ("second_opinion", "Second opinion on possible defects"),
          ("entrances", "Entrances"), ("evidence", "Evidence photos (blurring people and plates)"),
          ("report", "Report text")]
SPARK_STAGE = {"street_scan": "scene", "event_check": "sidewalk", "survey_check": "items",
               "entrance_read": "entrances", "survey_report_text": "report"}
MODEL_NAMES = {"sam": "SAM 3.1", "spark": "Muse Spark", "spark_open": "Muse Spark Open", "glimmer": "Muse Glimmer",
               "upload": "File upload (for SAM 3.1 video)"}
BLUR = {"person", "license plate"}
NOTE = ("seconds adds up each call's own duration. Calls run side by side, so it is longer than the processing time "
        "(total.processing_seconds).")


def _ts(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def read_calls(path: Path, start: str, end: str) -> list[dict]:
    """The log's calls that ended inside [start, end] (UTC, as db.now() writes); unreadable lines are skipped."""
    a, b = _ts(start), _ts(end)
    out = []
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            call = json.loads(line)
        except ValueError:
            continue
        if isinstance(call, dict) and a <= (call.get("ts") or 0) <= b:
            out.append(call)
    return out


def stage_of(call: dict, finds: set[str], surfaces: set[str]) -> tuple[str, str] | None:
    """(stage, model key) of one logged call, or None for a call that is not the survey's (a failed upload)."""
    kind = call.get("kind", "")
    if kind == "spark":
        return SPARK_STAGE.get(call.get("schema", ""), "items"), "spark"
    if kind == "spark_open":
        return "second_opinion", "spark_open"
    if kind.startswith("glimmer"):
        return "items", "glimmer"
    if kind == "upload":
        path = call.get("path", "")
        stage = ("scene" if "_spark" in path else "items" if path.startswith("checks") else
                 "entrances" if path.startswith("stairs") else "sidewalk")
        return stage, "upload"
    if kind == "sam":
        prompt = call.get("prompt") or ""
        if prompt in BLUR:
            return "evidence", "sam"
        if prompt.startswith("<") or prompt == "stairs":
            return "entrances", "sam"
        if prompt in finds:
            return "items", "sam"
        return "sidewalk", "sam"  # the walking surface (surfaces) and the objects on it
    return None


def build(conn, run_dir: Path, scan_id: int, windows: list[tuple[str, str, str]]) -> dict:
    """The survey's call record from its windows [(log path, start, end)]; Muse Spark Open's log beside each."""
    scan = db.get_scan(conn, scan_id)
    std = survey_items.load(scan["jurisdiction"])
    finds = {c.find for c in std.checks}
    surfaces = {std.path.surface} if getattr(std.path, "surface", None) else set()
    rows: dict[tuple[str, str], dict] = {}
    for log, start, end in windows:
        path = Path(run_dir) / log
        calls = read_calls(path, start, end) + read_calls(path.with_name("calls_open.jsonl"), start, end)
        for call in calls:
            where = stage_of(call, finds, surfaces)
            if where is None:
                continue
            row = rows.setdefault(where, {"calls": 0, "seconds": 0.0, "cost_usd": 0.0, "costed": True})
            row["calls"] += 1
            row["seconds"] += float(call.get("seconds") or 0)
            if "cost_usd" in call:
                row["cost_usd"] += float(call["cost_usd"] or 0)
            else:
                row["costed"] = False  # Muse Spark Open on Baseten logs no cost (its own credit)
    order = {s: i for i, (s, _) in enumerate(STAGES)}
    labels = dict(STAGES)
    stages = [{"stage": stage, "label": labels[stage], "model": MODEL_NAMES[model], "calls": r["calls"],
               "seconds": round(r["seconds"], 1), "cost_usd": round(r["cost_usd"], 4) if r["costed"] else None}
              for (stage, model), r in sorted(rows.items(), key=lambda kv: (order[kv[0][0]], kv[0][1]))]
    return {"scan_id": scan_id, "windows": [{"log": log, "start": a, "end": b} for log, a, b in windows],
            "note": NOTE, "stages": stages,
            "total": {"calls": sum(s["calls"] for s in stages), "seconds": round(sum(s["seconds"] for s in stages), 1),
                      "cost_usd": round(sum(s["cost_usd"] or 0 for s in stages), 4),
                      "scan_cost_usd": round(scan["cost_usd"], 4),
                      "processing_seconds": round(scan["processing_seconds"], 1)}}


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--scan", type=int, required=True)
    parser.add_argument("--window", nargs=3, action="append", required=True, metavar=("LOG", "START", "END"))
    args = parser.parse_args(argv)
    run_dir = config.run_dir(args.run)
    conn = db.connect(run_dir / "threshold.db")
    try:
        record = build(conn, run_dir, args.scan, [tuple(w) for w in args.window])
    finally:
        conn.close()
    out = run_dir / f"call_stats_scan{args.scan}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    t = record["total"]
    print(f"{out}: {t['calls']} calls, ${t['cost_usd']} logged against the survey's ${t['scan_cost_usd']}")
    return out


if __name__ == "__main__":
    main()
