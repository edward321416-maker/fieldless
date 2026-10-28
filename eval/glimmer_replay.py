"""Stage 1 of the Glimmer gate (docs/specs/2026-10-28-glimmer-modal-design.md, section 3): ask Muse Glimmer every
inventory pick Muse Spark already answered in a scan, and score the flow a scan would run (Glimmer, and Spark only
where Glimmer is unclear) against the city's inventory, beside Spark alone. Spark's answers are in the scan, so this
costs no Meta credit; only Glimmer's GPU time.

A pick is a photo the check asked about: every row of the item (Spark said yes or unclear) and every photo Spark
dropped as not the target (work/scan<N>/checks_unavailable.json, not_target).

    GLIMMER_URL=... python -m eval.glimmer_replay --run d5-seattle-rec --scan 4 --out OUT.json [--limit K]
"""
import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from assist.tools import counted_inventory
from eval import city
from eval.store import load_scan, open_ro

MIN_AGREE = 0.85
MAX_UNCLEAR = 0.30
LAYERS = {  # the evaluation's inputs: item id -> (layer under data/seattle, installed-date field)
    "utility_pole": ("assets/poles.geojson", None),
    "street_tree": ("assets/trees.geojson", "PLANTED_DATE"),
    "litter_can": ("assets/litter.geojson", None),
    "parking_pay_station": ("assets/paystations.geojson", None),
}


def _spark(row: dict) -> str:
    label = row.get("object_label")
    return label if label in ("yes", "unclear") else "no"


def picks(data: dict, unavailable: dict, item: str) -> list[dict]:
    """{"idx", "row", "spark"} for every photo the item's check asked about, in photo order."""
    rows = {r["frame_idx"]: r for r in data["issues"]
            if r["issue_type"] == item and r.get("review_status") != "rejected"}
    out = [{"idx": idx, "row": row, "spark": _spark(row)} for idx, row in rows.items()]
    out += [{"idx": idx, "row": None, "spark": "no"}
            for idx in (unavailable.get(item) or {}).get("not_target", []) if idx not in rows]
    return sorted(out, key=lambda p: p["idx"])


def cascade(glimmer: str, spark: str) -> str:
    """The flow's answer: Glimmer's when it decided, else Spark's (design 2.3)."""
    return glimmer if glimmer in ("yes", "no") else spark


def agreement(pairs: list[tuple[str, str]]) -> dict:
    """(glimmer, spark) answers -> the table, agreement where both decided, and Glimmer's unclear share."""
    both = [(g, s) for g, s in pairs if g in ("yes", "no") and s in ("yes", "no")]
    agree = sum(g == s for g, s in both)
    return {"table": dict(Counter(pairs)), "both_decided": len(both), "agree": agree,
            "share": round(agree / len(both), 3) if both else None,
            "glimmer_unclear_share": round(sum(g == "unclear" for g, _ in pairs) / len(pairs), 3) if pairs else None}


def replaced_rows(item_picks: list[dict], answers: dict[int, dict], item: str) -> list[dict]:
    """The item's rows as the flow would write them: the flow's answer, and Glimmer's box when Glimmer decided (it
    sets the row's direction in the city match). A photo Spark dropped becomes a row with a negative id."""
    rows = []
    for p in item_picks:
        g = counted_inventory(answers[p["idx"]]) if p["idx"] in answers else "unclear"
        label = cascade(g, p["spark"])
        base = p["row"] or {"id": -1 - p["idx"], "frame_idx": p["idx"], "review_status": "auto",
                            "model_outputs": {"check": {}}}
        check = {**(base.get("model_outputs") or {}).get("check", {}), "answer": label}
        if g in ("yes", "no"):
            check["box"] = answers[p["idx"]].get("box") or []
        rows.append({**base, "issue_type": item, "object_label": label, "corrections": [],
                     "model_outputs": {**(base.get("model_outputs") or {}), "check": check}})
    return rows


def gate(spark_found: int, flow_found: int, spark_false: int, flow_false: int, agree_share: float | None,
         unclear_share: float | None) -> dict:
    """The four rules of design section 3; every failed one is named."""
    reasons = []
    if flow_found < spark_found - 1:
        reasons.append(f"found {flow_found} vs Spark {spark_found} (more than 1 fewer)")
    if flow_false > spark_false + 1:
        reasons.append(f"test-set false keeps {flow_false} vs Spark {spark_false} (more than 1 more)")
    if agree_share is None or agree_share < MIN_AGREE:
        reasons.append(f"agreement {agree_share} below {MIN_AGREE}")
    if unclear_share is None or unclear_share > MAX_UNCLEAR:
        reasons.append(f"Glimmer unclear {unclear_share} above {MAX_UNCLEAR}")
    return {"pass": not reasons, "reasons": reasons}


def judge_for(run_dir: Path, reasoning: str = "low"):
    """Glimmer as the scan would call it (pipeline.second_opinion.glimmer_from_env), at a reasoning strength the
    model card allows. Low keeps the 2000-token output budget stage 1 measured it with; above low, the model reasons
    longer before the JSON, so the budget is at least 4000."""
    from pipeline.second_opinion import glimmer_from_env
    judge = glimmer_from_env(run_dir)
    if judge is None:
        raise SystemExit("set GLIMMER_URL to the deployed fieldless-glimmer address")
    judge.system = f"Reasoning strength: {reasoning}"
    judge.max_tokens = 2000 if reasoning == "low" else max(judge.max_tokens, 4000)
    return judge


def _found(rows: list[dict], data: dict, item: str, seattle: Path) -> dict:
    layer_file, installed = LAYERS[item]
    layer = json.loads((seattle / layer_file).read_text(encoding="utf-8"))
    r = city.evaluate(rows, data["frames"], layer, item, inventory=True, id_field="OBJECTID",
                      installed_field=installed)
    return {"found": len(r["matched"]), "covered": r["covered"], "repeat": len(r.get("rows_repeat", [])),
            "not_in_record": len(r["rows_unmatched"])}


def run(run_dir: Path, scan_id: int, judge, standard, seattle: Path, limit: int | None = None,
        workers: int = 8) -> dict:
    from pipeline import collect
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, scan_id)
    finally:
        conn.close()
    unavailable = json.loads((run_dir / "work" / f"scan{scan_id}" / "checks_unavailable.json")
                             .read_text(encoding="utf-8"))
    frames = {f["idx"]: f for f in data["frames"]}
    report = {}
    for item in LAYERS:
        check = standard.check(item)
        ps = picks(data, unavailable, item)[:limit]

        def ask(p):
            try:
                return judge.answer(check, collect.photo_path(frames[p["idx"]]["image_id"]))
            except Exception as exc:  # a failed request goes to Spark in a scan; here it counts as unclear
                return {"answer": "unclear", "visible": True, "box": [], "note": f"error: {exc}"[:300]}

        with ThreadPoolExecutor(workers) as pool:
            answers = dict(zip((p["idx"] for p in ps), pool.map(ask, ps)))
        agree = agreement([(counted_inventory(answers[p["idx"]]), p["spark"]) for p in ps])
        report[item] = {
            "picks": len(ps),
            "spark": _found([p["row"] for p in ps if p["row"]], data, item, seattle),
            "flow": _found(replaced_rows(ps, answers, item), data, item, seattle),
            "to_spark": sum(counted_inventory(a) == "unclear" for a in answers.values()),
            "errors": sum(str(a.get("note", "")).startswith("error:") for a in answers.values()),
            "agreement": {**agree, "table": {f"{g}/{s}": n for (g, s), n in agree["table"].items()}},
            "answers": {str(k): v for k, v in answers.items()}}
    return report


def main(argv=None) -> None:
    from eval.glimmer_ledger import LEDGER, append, check_budget
    from pipeline import config
    from pipeline.survey_items import load

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--scan", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=None, help="at most this many picks per item (a smoke test)")
    ap.add_argument("--seattle", default=str(LEDGER.parent / "seattle"))
    ap.add_argument("--reasoning", choices=("low", "medium", "high"), default="low")
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    judge = judge_for(run_dir, args.reasoning)
    check_budget(LEDGER, planned_usd=1.0)
    started = time.time()
    report = run(run_dir, args.scan, judge, load("us"), Path(args.seattle), limit=args.limit)
    report["_reasoning"] = args.reasoning
    report["_ledger"] = append(LEDGER, f"replay {args.run} scan {args.scan} {args.reasoning}",
                               busy_s=time.time() - started)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for item, r in report.items():
        if not item.startswith("_"):
            print(item, "picks", r["picks"], "found spark/flow", r["spark"]["found"], r["flow"]["found"],
                  "of", r["spark"]["covered"], "| repeat", r["flow"]["repeat"], "| agree", r["agreement"]["share"],
                  "| glimmer unclear", r["agreement"]["glimmer_unclear_share"], "| to spark", r["to_spark"],
                  "| errors", r["errors"])


if __name__ == "__main__":
    main()
