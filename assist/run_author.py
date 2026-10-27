"""Turn a rule into tested survey items (plan 5):

    python -m assist.run_author --rule ada2010 --testset seattle_ramps --run author-ada [--cap 1.5]

Muse Spark reads the rule PDF and drafts items; code drops any whose section or quote is not in the rule; the agent
tests and revises them with SAM 3.1 and Muse Spark on the labelled photos; the results go to <data>/items/drafts/ as
TOML (a person adds the items they accept to pipeline/standards/<code>.toml) and a Markdown report with the scores
code measured."""
import argparse
import json
from datetime import datetime
from pathlib import Path

from assist import agent, items as items_mod, rules, testset
from assist.responder import make_responder
from assist.tools import Bench
from pipeline import collect, config
from pipeline.cost import CostMeter
from pipeline.meta_api import MetaApi, file_part, text_part

DRAFT_ESTIMATE_USD = 0.15
MAX_ITEMS = 6


def draft(api, rule) -> dict:
    prompt = items_mod.DRAFT_PROMPT.format(title=rule.title, max_items=MAX_ITEMS)
    with api.uploaded(rule.pdf) as file_id:
        return api.spark_json([file_part(file_id), text_part(prompt)], "survey_items_draft", items_mod.DRAFT_SCHEMA,
                              estimate_usd=DRAFT_ESTIMATE_USD)


def _final(valid: list[dict], finished: dict, bench) -> list[dict]:
    """The drafted items with the agent's final find and ask, and the scores code measured for exactly those. A tested
    item's report_when comes from code: the labelled question's defect answer, flipped if the ask is the opposite
    question (the agent may reword an ask, and its yes may then mean the defect)."""
    by_id = {i["id"]: i for i in finished.get("items", [])}
    out = []
    for d in valid:
        f = by_id.get(d["id"], {})
        item = {**d, "find": (f.get("find") or d["find"]).strip(), "ask": f.get("ask") or d["ask"],
                "agent_note": f.get("note", ""), "tested": False}
        found = bench.last("score_find", phrase=item["find"])
        asked = bench.last("score_ask", item_id=d["id"], find=item["find"], ask=item["ask"])
        if f.get("tested") and asked:
            defect = bench.ts.questions[asked["key"]].defect_when
            item.update(tested=True, report_when=defect if asked["polarity"] == "same" else testset.flip(defect))
        if found:
            item["test_find"] = (f"SAM found {found['found_visible']} of {found['total_visible']} photos showing the "
                                 f"target; fired on {found['false_on_negatives']} of {found['total_negatives']} "
                                 f"without it")
        if asked:
            item["test_ask"] = (f"right on {asked['right']} of {asked['decidable']} labelled photos "
                                f"({asked['unclear']} unclear, {asked['unchecked']} not found by SAM), "
                                f"against '{asked['key']}'")
        out.append(item)
    return out


def _report(rule, ts, drafted: dict, final: list[dict], problems: list[str], result: dict, cost: float) -> str:
    lines = [f"# Survey items drafted from {rule.title}", "",
             f"- Source: {rule.source_url} (pages {', '.join(f'{a}-{b}' for a, b in rule.pages)})",
             f"- Tested on: {ts.name}, {len(ts.photos)} labelled photos of the target \"{ts.target}\"",
             f"- Agent: {'finished' if result['finished'] else 'stopped: ' + result['stopped']}, "
             f"{result['calls']} tool calls", f"- Model spend: ${cost:.2f}", "", "## Items that a photo can settle", ""]
    for i in final:
        lines += [f"### `{i['id']}` (section {i['section']}, page {i['page']})", "",
                  f"> {i['quote']}", "",
                  f"- find (SAM 3.1): \"{i['find']}\"", f"- ask (Muse Spark): {i['ask']}",
                  f"- a defect when the answer is: {i['report_when']}",
                  f"- test, find: {i.get('test_find', 'not tested')}",
                  f"- test, ask: {i.get('test_ask', 'not tested')}",
                  f"- agent's note: {i['agent_note'] or '-'}", ""]
    lines += ["## Requirements a photo cannot settle (Muse Spark's reasons)", ""]
    lines += [f"- {n['section']}: {n['requirement']} ({n['reason']})" for n in drafted.get("not_checkable", [])]
    lines += ["", "## Drafts dropped by code", ""] + ([f"- {p}" for p in problems] or ["- none"])
    return "\n".join(lines) + "\n"


def author(api, respond, rule, ts, out_dir: Path, photo=collect.photo_path, workers: int = 4,
           stamp: str | None = None) -> dict:
    drafted = draft(api, rule)
    valid, problems = items_mod.validate(drafted, rule.read_text())
    bench = Bench(api, ts, items=valid, photo=photo, workers=workers)
    result = agent.run(respond, bench, valid)
    final = _final(valid, result, bench)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{rule.name}-{stamp}"
    header = [f"Drafted by Muse Spark from {rule.title}", f"source: {rule.source_url}",
              f"tested by the assist agent on {ts.name}; add accepted [[check]] tables to pipeline/standards/<code>.toml"]
    accepted = [i for i in final if i["tested"]]  # untested items stay in the report only
    paths = {"toml": stem.with_suffix(".toml"), "md": stem.with_suffix(".md"), "json": stem.with_suffix(".json")}
    paths["toml"].write_text(items_mod.to_toml(accepted, header), encoding="utf-8")
    paths["md"].write_text(_report(rule, ts, drafted, final, problems, result, api.meter.spent_usd),
                           encoding="utf-8")
    paths["json"].write_text(json.dumps({"draft": drafted, "problems": problems, "agent": result, "final": final,
                                         "history": bench.history, "cost_usd": api.meter.spent_usd},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rule", required=True)
    ap.add_argument("--testset", required=True)
    ap.add_argument("--run", default="author")
    ap.add_argument("--cap", type=float, default=1.5, help="model spend cap in USD for this run")
    args = ap.parse_args(argv)
    rule, ts = rules.get(args.rule), testset.load(args.testset)
    run_dir = config.run_dir(args.run)
    api = MetaApi(run_dir / "calls.jsonl", CostMeter(args.cap))
    for p in ts.photos:  # the photos are Mapillary images; fetch any not cached yet
        if collect.fetch_photo({"id": p.id, "thumb_2048_url": None}) is None:
            raise SystemExit(f"could not fetch photo {p.id}")
    paths = author(api, make_responder(api, agent.TOOLS), rule, ts, config.data_dir() / "items" / "drafts")
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    print(f"model spend: ${api.meter.spent_usd:.2f}")
    return paths


if __name__ == "__main__":
    main()
