"""Gate rule 2 (docs/specs/2026-10-28-glimmer-modal-design.md, section 3): on the inventory test sets built from the
city's lists, does the Glimmer-then-Spark flow keep more look-alikes than Spark alone? Both benches ask the scan's own
wording (pipeline/standards/us.toml) about the photos where SAM finds the item's phrase. SAM runs once; Spark's
answers cost about $0.2 a set in Meta credit, Glimmer's only GPU time.

    GLIMMER_URL=... python -m eval.glimmer_testsets --out OUT.json
"""
import argparse
import json
import time
from pathlib import Path

from eval.glimmer_replay import cascade

SETS = {"utility_pole": "seattle_utility_poles", "street_tree": "seattle_trees", "litter_can": "seattle_litter",
        "parking_pay_station": "seattle_paystations"}  # the evaluation's inputs: inventory item -> its test set


def flow_false(spark: dict[str, str], glimmer: dict[str, str], negatives: set[str]) -> int:
    """Photos without the target that the flow keeps as the target."""
    return sum(cascade(glimmer.get(p, "unclear"), spark.get(p, "unclear")) == "yes" for p in negatives)


def _answers(bench) -> dict[str, str]:
    return {photo: counted for (_, photo), (counted, _) in bench.confirmations.items()}


def main(argv=None) -> None:
    from assist import testset
    from assist.tools import Bench
    from eval.glimmer_ledger import LEDGER, append, check_budget
    from pipeline import config
    from pipeline.cost import CostMeter
    from pipeline.meta_api import MetaApi
    from pipeline.second_opinion import glimmer_from_env
    from pipeline.survey_items import load

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", default="glimmer-testsets")
    ap.add_argument("--cap", type=float, default=1.5, help="Meta spend cap in USD for Spark's answers")
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    judge = glimmer_from_env(run_dir)
    if judge is None:
        raise SystemExit("set GLIMMER_URL to the deployed fieldless-glimmer address")
    check_budget(LEDGER, planned_usd=0.6)
    api = MetaApi(run_dir / "calls.jsonl", CostMeter(args.cap))  # as assist/run_inventory.py builds it
    standard, report, started = load("us"), {}, time.time()
    for item, name in SETS.items():
        check, ts = standard.check(item), testset.load(name)
        items = [{"id": item, "target": check.target}]
        spark_bench = Bench(api, ts, items=items)
        s = spark_bench.confirm(item, check.find, check.ask)
        glimmer_bench = Bench(api, ts, items=items, judge=judge)
        glimmer_bench.areas = spark_bench.areas  # the same SAM finds; no second SAM bill
        g = glimmer_bench.confirm(item, check.find, check.ask)
        sa, ga = _answers(spark_bench), _answers(glimmer_bench)
        report[item] = {"spark_kept": s["kept_true"], "glimmer_kept": g["kept_true"], "spark_false": s["kept_false"],
                        "flow_false": flow_false(sa, ga, {p.id for p in ts.negatives()}),
                        "visible": len(ts.visible()), "negatives": len(ts.negatives()), "spark": sa, "glimmer": ga}
        print(item, {k: v for k, v in report[item].items() if k not in ("spark", "glimmer")})
    report["_ledger"] = append(LEDGER, "testsets", busy_s=time.time() - started)
    report["_meta_usd"] = round(api.meter.spent_usd, 3)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Meta spend", report["_meta_usd"])


if __name__ == "__main__":
    main()
