"""Draft and test one inventory survey item (plan 5):

    python -m assist.run_inventory --target "streetlight pole" --testset seattle_poles --run inv-poles
        [--describe "Seattle City Light poles that carry a street lamp"] [--cap 1.0]

An inventory item records where a thing is (a streetlight pole, a street tree, a litter can), not a defect, so it
needs no rule to cite: Muse Spark drafts it from the target's name and the description; code checks the id and the
texts; the agent picks the SAM 3.1 phrase and tests that Muse Spark confirms what SAM found, on a test set whose photos
only say whether they show the target. The results go to <data>/items/drafts/ as TOML (a person adds the item to
pipeline/standards/<code>.toml if they accept it), a Markdown report with the scores code measured, and the full
history as JSON."""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from assist import agent, items as items_mod, testset
from assist.responder import make_responder
from assist.tools import Bench
from pipeline import collect, config
from pipeline.cost import CostMeter
from pipeline.meta_api import MetaApi, text_part

DRAFT_ESTIMATE_USD = 0.02  # a short text prompt and one small JSON answer


def slug(target: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-") or "target"


def draft(api, target: str, describe: str = "") -> dict:
    return api.spark_json([text_part(items_mod.inventory_prompt(target, describe))], "inventory_item_draft",
                          items_mod.INVENTORY_SCHEMA, estimate_usd=DRAFT_ESTIMATE_USD)


def _find_text(f: dict) -> str:
    return (f"SAM found {f['found_visible']} of {f['total_visible']} photos showing the target; fired on "
            f"{f['false_on_negatives']} of {f['total_negatives']} without it")


def _confirm_text(c: dict) -> str:
    return (f"kept {c['kept_true']} of {c['total_visible']} photos showing the target (SAM found {c['found_visible']}: "
            f"Spark said yes {c['confirmed']}, no {c['rejected_wrongly']}, unclear {c['unclear']}); kept "
            f"{c['kept_false']} of {c['total_negatives']} photos without it (SAM fired on {c['false_finds']}: "
            f"Spark said no {c['false_finds_rejected']}, unclear {c['false_finds_unclear']})")


def _final(item: dict, result: dict, bench) -> dict:
    """The drafted item with the agent's final find and ask, and the scores code measured for exactly that pair. It
    counts as tested only if score_confirm ran on that find and ask; the agent's word is not enough."""
    f = result.get("item") or {}
    final = {**item, "find": (f.get("find") or item["find"]).strip(),
             "ask": items_mod.with_unclear(f.get("ask") or item["ask"]), "agent_note": f.get("note", "")}
    found = bench.last("score_find", phrase=final["find"])
    confirmed = bench.last("score_confirm", item_id=item["id"], find=final["find"], ask=final["ask"])
    final["tested"] = confirmed is not None
    if found:
        final["test_find"] = _find_text(found)
    if confirmed:
        final["test_confirm"] = _confirm_text(confirmed)
    return final


def _phrases(history: list[dict]) -> list[str]:
    """Every phrase SAM tried, best first by found_visible - false_on_negatives (the agent's rule), as table rows."""
    seen = {h["phrase"]: h for h in history if h["tool"] == "score_find"}
    ranked = sorted(seen.values(), key=lambda h: -(h["found_visible"] - h["false_on_negatives"]))
    return [f"| \"{h['phrase']}\" | {h['found_visible']} of {h['total_visible']} | {h['false_on_negatives']} of "
            f"{h['total_negatives']} | {h['found_visible'] - h['false_on_negatives']} |" for h in ranked]


def _wrong(entries: list[dict]) -> str:
    return "; ".join(f"{w['photo_id']} ({w['answer']}: {w.get('note') or '-'})" for w in entries) or "none"


def _asks(history: list[dict]) -> list[str]:
    """Every find and ask pair Spark was tested on, in the order tried."""
    seen: dict[tuple[str, str], dict] = {}
    for h in history:
        if h["tool"] == "score_confirm":
            seen.setdefault((h["find"], h["ask"]), h)
    lines = []
    for n, h in enumerate(seen.values(), 1):
        lines += [f"{n}. find \"{h['find']}\": {_confirm_text(h)}", "", f"   > {h['ask']}", "",
                  f"   - not yes on photos showing it: {_wrong(h['wrong_visible'])}",
                  f"   - not no on photos without it: {_wrong(h['wrong_negatives'])}", ""]
    return lines


def _item_lines(i: dict) -> list[str]:
    return [f"- id: `{i['id']}`", f"- find (SAM 3.1): \"{i['find']}\"", f"- ask (Muse Spark): {i['ask']}",
            f"- labels: \"{i['label_yes']}\" / \"{i['label_no']}\"",
            f"- test, find: {i.get('test_find', 'not tested')}",
            f"- test, confirm: {i.get('test_confirm', 'not tested')}",
            f"- agent's note: {i['agent_note'] or '-'}",
            f"- in the TOML: {'yes' if i['tested'] else 'no, this find and ask were never tested together'}"]


def _report(target: str, describe: str, ts, drafted: dict, final: dict | None, problems: list[str], result: dict,
            history: list[dict], cost: float) -> str:
    lines = [f"# Inventory item for \"{target}\"", ""]
    if describe:
        lines.append(f"- What the survey means by it: {describe}")
    lines += [f"- Tested on: {ts.name}, {len(ts.visible())} photos showing \"{ts.target}\" and {len(ts.negatives())} "
              f"without it",
              f"- Agent: {'finished' if result['finished'] else 'stopped: ' + result['stopped']}, "
              f"{result['calls']} tool calls", f"- Model spend: ${cost:.2f}", "", "## The item", ""]
    lines += _item_lines(final) if final else ["No item: Muse Spark's draft was dropped by code (below)."]
    lines += ["", "## Phrases tried (scored by code)", "",
              "| phrase | found, of photos showing the target | fired on, of photos without it | found minus fired |",
              "|---|---|---|---|"] + (_phrases(history) or ["| none | | | |"])
    lines += ["", "## Asks tried (scored by code)", ""] + (_asks(history) or ["- none", ""])
    others = ", ".join(f"\"{a}\"" for a in drafted.get("alternatives") or []) or "none"
    lines += ["## Muse Spark's draft", "", f"- find: \"{drafted.get('find', '')}\", other phrases: {others}",
              f"- ask: {drafted.get('ask', '')}", "", "## Drafts dropped by code", ""]
    lines += [f"- {p}" for p in problems] or ["- none"]
    return "\n".join(lines) + "\n"


def inventory(api, respond, target: str, ts, out_dir: Path, describe: str = "", photo=collect.photo_path,
              workers: int = 4, stamp: str | None = None, say=None) -> dict:
    """say(line), if given, hears what happens as it happens: the draft, then every tool call the agent makes."""
    drafted = draft(api, target, describe)
    valid, problems = items_mod.validate_inventory(drafted)
    if say and valid:
        negatives = len(ts.negatives())
        say(f'Muse Spark drafted an item for "{target}": find "{valid[0]["find"]}"')
        say(f"The agent tests it on {ts.name}: {len(ts.visible())} photos that show a {target}, {negatives} "
            f"that do{'es' if negatives == 1 else ''} not")
    elif say:
        say("Code dropped the draft: " + "; ".join(problems))
    bench = Bench(api, ts, items=valid, photo=photo, workers=workers)
    final = None
    result = {"finished": False, "item": {}, "calls": 0, "stopped": "no valid draft"}
    if valid:
        result = agent.run_inventory(respond, bench, valid[0],
                                     on_step=(lambda *step: say(agent.describe(*step))) if say else None)
        final = _final(valid[0], result, bench)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"inventory-{slug(target)}-{stamp}"
    header = [f"Inventory item drafted by Muse Spark for \"{target}\"",
              f"tested by the assist inventory agent on {ts.name}; add an accepted [[check]] table to "
              f"pipeline/standards/<code>.toml"]
    accepted = [final] if final and final["tested"] else []  # an untested item stays in the report only
    if not accepted:
        header.append("no tested item: see the report")
    paths = {"toml": stem.with_suffix(".toml"), "md": stem.with_suffix(".md"), "json": stem.with_suffix(".json")}
    paths["toml"].write_text(items_mod.to_toml(accepted, header), encoding="utf-8")
    paths["md"].write_text(_report(target, describe, ts, drafted, final, problems, result, bench.history,
                                   api.meter.spent_usd), encoding="utf-8")
    paths["json"].write_text(json.dumps({"target": target, "describe": describe, "draft": drafted,
                                         "problems": problems, "agent": result, "final": final,
                                         "history": bench.history, "cost_usd": api.meter.spent_usd},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help='the thing the survey records, such as "streetlight pole"')
    ap.add_argument("--testset", required=True)
    ap.add_argument("--run", default="inventory")
    ap.add_argument("--describe", default="", help="what the survey means by the target, in one sentence")
    ap.add_argument("--cap", type=float, default=1.0, help="model spend cap in USD for this run")
    args = ap.parse_args(argv)
    ts = testset.load(args.testset)
    run_dir = config.run_dir(args.run)
    api = MetaApi(run_dir / "calls.jsonl", CostMeter(args.cap))
    for p in ts.photos:  # the photos are Mapillary images; fetch any not cached yet
        if collect.fetch_photo({"id": p.id, "thumb_2048_url": None}) is None:
            raise SystemExit(f"could not fetch photo {p.id}")
    paths = inventory(api, make_responder(api, agent.INVENTORY_TOOLS), args.target, ts,
                      config.data_dir() / "items" / "drafts", describe=args.describe,
                      say=lambda line: print(line, flush=True))
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    print(f"model spend: ${api.meter.spent_usd:.2f}")
    return paths


if __name__ == "__main__":
    main()
