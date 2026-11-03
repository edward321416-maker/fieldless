"""Does a seen city record still say what its photo shows? (docs/specs/2026-11-01-record-update-list-design.md)

    python -m eval.record_check --run demo --scan 30 --city seattle-ballard --dry-run
    python -m eval.record_check --run demo --scan 30 --city seattle-ballard

An Asset Check says a record is there. This goes one step further for the records the photos settled, wherever the
city's file names a yes/no field a photo can answer ([items.<id>.compare]). Muse Spark answers on the blurred evidence
photo, about the object in the row's box. Muse Spark Open is asked only where Spark answers against the city, and a
record differs only when both do. The records that agree with the city are counted too: they say how often the two
models would otherwise be wrong against a list that is right.

Each row ends in one of the states of eval.attribute_state (agree, different, unsettled) and says why it stayed
unsettled. A record seen in a photo whose answer is not agree is not a record that needs no visit: eval.asset_check
turns it into a visit, and a seen record this check never asked about into one too.

Writes <run>/record_check_scan<N>.json, which eval.asset_check reads. It spends at most COST_CAP_USD of Meta credit.
It does not change the city's records.
"""
import argparse
import dataclasses
import json
from collections import Counter
from pathlib import Path

from eval import attribute_state
from eval.asset_check import DATE, FIX, SAME
from eval.city_check import load_city
from pipeline import config, second_opinion, survey_items
from pipeline.cost import CostCapReached, CostMeter
from pipeline.meta_api import MetaApi, error_text, image_part, text_part
from pipeline.parallel import run_parallel
from pipeline.survey_checks import SCHEMA, prompt

COST_CAP_USD = 0.6  # 30 records at about $0.012 each, with what a parallel run holds in reserve
WORKERS = 8
SETTLED = (FIX, DATE, SAME)  # a row the photos left unsettled has no photo of the record to ask about
ANSWERED = ("yes", "no", "unclear")


def jobs_for(check: dict, town: dict, layers: dict[str, dict[str, dict]]) -> list[dict]:
    """The rows to ask about. layers: each item's records by the id a row names them by. A record whose field is empty
    has nothing to compare."""
    jobs = []
    for row in check["rows"]:
        compare = town["items"][row["item"]].get("compare")
        # a record waiting on this very check is a visit that says what it would be if the check agreed
        settled = row["action"] in SETTLED or row.get("action_if_attribute_agrees") in SETTLED
        if not compare or not settled or not row.get("box") or not row.get("evidence"):
            continue
        value = {compare["yes"]: "yes", compare["no"]: "no"}.get(layers[row["item"]].get(row["city_record"], {}).get(compare["field"]))
        if value:
            jobs.append({"item": row["item"], "record": row["city_record"], "image": row["evidence"]["image"],
                         "box": row["box"], "city_value": value})
    return jobs


_said = attribute_state._said


def differs(job: dict, spark: dict | None, other: dict | None) -> bool:
    said = _said(spark)
    return said is not None and said != job["city_value"] and _said(other) == said


def _stated(row: dict) -> dict:
    """The row with where its check ended (eval.attribute_state), so a saved check says it without being re-read."""
    state = attribute_state.state_of(row)
    return {**row, "state": state["state"], "reason": state["reason"]}


def ask(api, open_model, standard, town: dict, job: dict, run_dir: Path) -> dict:
    compare = town["items"][job["item"]]["compare"]
    check = dataclasses.replace(standard.check(job["item"]), ask=compare["ask"])
    photo = Path(run_dir) / "evidence" / job["image"]
    try:
        spark = api.spark_json([image_part(photo), text_part(prompt(check, job["box"]))], "record_check", SCHEMA)
    except CostCapReached as exc:
        return _stated({**job, "spark": {"answer": "not_asked", "error": str(exc)}, "open": None, "differs": False})
    except Exception as exc:
        return _stated({**job, "spark": {"answer": "error", "error": error_text(exc)}, "open": None, "differs": False})
    said, other = _said(spark), None
    if said and said != job["city_value"]:
        try:
            other = open_model.answer(check, photo, job["box"])
        except Exception as exc:
            other = {"answer": "error", "error": error_text(exc)}
    out = {**job, "spark": spark, "open": other, "differs": differs(job, spark, other)}
    if out["differs"]:
        out |= {"city_says": compare[f"says_{job['city_value']}"], "photo_shows": compare[f"says_{said}"]}
    return _stated(out)


def run(jobs: list[dict], api, open_model, standard, town: dict, run_dir: Path, workers: int = WORKERS) -> list[dict]:
    return run_parallel(lambda job: ask(api, open_model, standard, town, job, run_dir), jobs, workers)


def summary(rows: list[dict]) -> dict:
    asked = sum(r["spark"].get("answer") in ANSWERED for r in rows)
    agree = sum(_said(r["spark"]) == r["city_value"] for r in rows)
    differ = sum(r["differs"] for r in rows)
    return {"asked": asked, "agree": agree, "differ": differ, "unsettled": len(rows) - agree - differ}


def unsettled_by(rows: list[dict]) -> dict[str, int]:
    """Why the records that stayed unsettled did, by reason (eval.attribute_state)."""
    states = [attribute_state.state_of(r) for r in rows]
    return dict(Counter(s["reason"] for s in states if s["state"] == attribute_state.UNSETTLED))


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--scan", type=int, required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--dry-run", action="store_true", help="choose the rows and stop: no model is called")
    args = parser.parse_args(argv)
    run_dir = config.run_dir(args.run)
    check = json.loads((run_dir / f"asset_check_scan{args.scan}.json").read_text(encoding="utf-8"))
    town = load_city(args.city)
    root = config.data_dir() / town["root"]
    layers = {}
    for item, cfg in town["items"].items():
        if "compare" in cfg:
            features = json.loads((root / cfg["layer"]).read_text(encoding="utf-8"))["features"]
            layers[item] = {str(f["properties"].get(cfg["record_field"])): f["properties"] for f in features}
    jobs = jobs_for(check, town, layers)
    print(f"{len(jobs)} records to ask about; at most ${len(jobs) * config.SPARK_IMAGE_ESTIMATE_USD:.2f} reserved")
    if args.dry_run:
        return {"records": len(jobs)}
    open_model = second_opinion.from_env(run_dir)
    if open_model is None:
        raise SystemExit("BASETEN_API_KEY is not set: there is no second model to confirm a difference")
    meter = CostMeter(COST_CAP_USD)
    api = MetaApi(run_dir / "calls_second_look.jsonl", meter)
    standard = survey_items.load("US")
    rows = run(jobs, api, open_model, standard, town, run_dir)
    result = {"scan_id": args.scan, "city": args.city, "model": config.SPARK_MODEL, "second_model": open_model.model,
              "cost_usd": round(meter.spent_usd, 4), "summary": summary(rows), "unsettled_by": unsettled_by(rows),
              "rows": rows}
    path = run_dir / f"record_check_scan{args.scan}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    print(f"${meter.spent_usd:.2f} spent; wrote {path}")
    return result


if __name__ == "__main__":
    main()
