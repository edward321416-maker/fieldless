"""A survey item the agent writes in the app (docs/specs/2026-10-29-agent-items-design.md), run by server.jobs on a
finished scan's photos (pipeline.item_runs.ScanPhotos):

- start() has Muse Spark draft the item (assist.run_inventory.draft), then gathers the answers it is tested on: from
  the city's own positions when the city publishes the asset ([extra] in eval/cities/<city>.toml), otherwise from a
  person, who answers ten of the scan's photos (it returns "labels_needed" and the server asks).
- resume() takes the person's answers.
- Both test the item with the inventory agent (assist.agent.run_inventory) and return a Check only if code's scores
  pass: the item keeps at least PASS_KEPT of the photos that show the target and at most PASS_FALSE of those that do
  not. The agent writes the wording; code scores it; the city or a person gives the answers.

A test photo's id is the scan's photo idx, as a string. Every step is told to on_progress in a sentence a person reads
on the screen."""
import json
import re
from datetime import datetime
from pathlib import Path

from assist import agent, items as items_mod, testset_build
from assist.responder import make_responder
from assist.run_inventory import _final, draft
from assist.testset import Photo, TestSet
from assist.tools import Bench
from eval import city, city_check as city_files
from pipeline import config
from pipeline.sam_output import confident, parse
from pipeline.survey_items import Check

PASS_KEPT, PASS_FALSE = 0.7, 0.2  # of photos showing the target, the share kept; of photos without it, kept wrongly
MIN_POSITIVES = 3  # fewer photos showing the target than this cannot test an item
LABEL_PHOTOS = (6, 4)  # photos where SAM found the drafted phrase, and where it did not, for a person to answer
PROBE_PHOTOS = 40  # at most this many of the scan's photos are asked of SAM to choose them ($0.10)
CITY_PHOTOS = (12, 6)  # photos showing the target and photos without it, chosen from the city's positions
GROUP_M = 15.0  # one row per 15 m of street, as the other inventory items
CITY = "seattle"  # the city file a US scan's assets are looked up in


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"


def layer_for(photos, target: str) -> tuple[dict, dict] | None:
    """(the city's layer settings, its GeoJSON) when the city publishes the target and the layer is on this disk."""
    if photos.standard != "US":
        return None
    town = city_files.load_city(CITY)
    cfg = city_files.extra_layer(town, target)
    if cfg is None:
        return None
    try:
        path = config.data_dir() / cfg["root"] / cfg["layer"]
        return cfg, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError):
        return None  # no data folder or no layer on this server: a person answers instead


def _photo_dicts(photos) -> list[dict]:
    return [{"id": str(f["idx"]), "lat": f["lat"], "lng": f["lng"], "compass": f.get("compass"),
             "sequence": str(f.get("sequence_id") or f["clip"]), "captured_at": f.get("captured_at")}
            for f in photos.frames if f["idx"] in photos.paths]


def _city_set(photos, target: str, layer: dict) -> TestSet:
    chosen = testset_build.sample(testset_build.label_photos(_photo_dicts(photos), layer), *CITY_PHOTOS)
    return TestSet("city", target, {}, tuple(Photo(p["id"], testset_build.VISIBLE[p["class"]], {}, p["note"])
                                             for p in chosen))


def _person_set(target: str, labels: dict) -> TestSet:
    return TestSet("person", target, {}, tuple(Photo(str(i), v if v in ("yes", "no") else "unclear", {})
                                               for i, v in sorted(labels.items())))


def _spread(seq: list, k: int) -> list:
    """k items of seq, evenly spaced, first and last included when k > 1."""
    if len(seq) <= k:
        return list(seq)
    return [seq[round(n * (len(seq) - 1) / (k - 1))] for n in range(k)] if k > 1 else seq[:1]


def _to_ask(api, photos, find: str) -> list[int]:
    """Ten photos for a person: LABEL_PHOTOS[0] where SAM finds the drafted phrase and LABEL_PHOTOS[1] where it does
    not, both spread along the scan, from at most PROBE_PHOTOS photos asked of SAM. Short of one kind, the other fills
    in, so a person always sees as many as there are."""
    probe = _spread(sorted(photos.paths), PROBE_PHOTOS)
    found = [i for i in probe if confident(parse(api.sam_image(find, photos.paths[i]), video=False))]
    missed = [i for i in probe if i not in found]
    k_hit, k_miss = LABEL_PHOTOS
    hits = _spread(found, min(len(found), k_hit + max(0, k_miss - len(missed))))
    misses = _spread(missed, k_hit + k_miss - len(hits))
    return sorted(hits + misses)


def _write_drafts(target: str, final: dict, test: dict, history: list[dict]) -> None:
    """The tested item as a TOML a person may add to pipeline/standards/<code>.toml, and the run as JSON. A disk that
    refuses does not stop the run."""
    try:
        out = config.data_dir() / "items" / "drafts"
        out.mkdir(parents=True, exist_ok=True)
        stem = out / f"agent-{_slug(target).replace('_', '-')}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        header = [f'Inventory item written by Muse Spark in the app for "{target}"',
                  f"tested on {test['labels']} answers: kept {test['kept'][0]} of {test['kept'][1]} photos showing it, "
                  f"{test['false'][0]} of {test['false'][1]} without it"]
        stem.with_suffix(".toml").write_text(items_mod.to_toml([final], header), encoding="utf-8")
        stem.with_suffix(".json").write_text(json.dumps({"target": target, "final": final, "test": test,
                                                         "history": history}, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    except (OSError, RuntimeError):
        pass


def _check(final: dict) -> Check:
    target = final["target"]
    return Check(id=final["id"], find=final["find"], target=target, ask=final["ask"], report_when="",
                 label_yes=final["label_yes"], label_no=final["label_no"],
                 label_unclear=f"{target[:1].upper() + target[1:]}: unclear from the photo", inventory=True,
                 group_m=GROUP_M, question=f"Is this a {target}?", find_by="photos")


def _test(api, photos, item: dict, ts: TestSet, labels: str, layer: str | None, on_progress, respond) -> dict:
    bench = Bench(api, ts, items=[item], photo=lambda pid: photos.paths[int(pid)])
    result = agent.run_inventory(respond or make_responder(api, agent.INVENTORY_TOOLS), bench, item,
                                 on_step=lambda *step: on_progress(agent.describe(*step)))
    final = _final(item, result, bench)
    found = bench.last("score_find", phrase=final["find"]) or {}
    scored = bench.last("score_confirm", item_id=item["id"], find=final["find"], ask=final["ask"])
    test = {"labels": labels, "layer": layer, "phrase": final["find"], "ask": final["ask"], "calls": result["calls"],
            "found": [found.get("found_visible", 0), len(ts.visible())]}
    if scored is None:
        on_progress("The agent did not test its final wording")
        return {"status": "failed", "reason": "No reliable wording found: the agent did not test its final wording",
                "test": test}
    kept, wrong = scored["kept_true"], scored["kept_false"]
    test |= {"kept": [kept, scored["total_visible"]], "false": [wrong, scored["total_negatives"]]}
    passed = (kept >= PASS_KEPT * scored["total_visible"]
              and wrong <= PASS_FALSE * max(1, scored["total_negatives"]))
    words = (f"keeps {kept} of {scored['total_visible']} photos showing it, {wrong} of {scored['total_negatives']} "
             f"without it")
    if not passed:
        on_progress(f"Not good enough: {words}")
        return {"status": "failed", "test": test,
                "reason": f"No reliable wording found: it {words} (needs {PASS_KEPT:.0%} and at most {PASS_FALSE:.0%})"}
    on_progress(f"Passed: {words}")
    _write_drafts(item["target"], final, test, bench.history)
    return {"status": "ready", "check": _check(final), "test": test}


def _draft(api, target: str, on_progress) -> tuple[dict | None, str]:
    drafted = draft(api, target)
    valid, problems = items_mod.validate_inventory(drafted)
    if not valid:
        return None, "Muse Spark's draft was not usable: " + "; ".join(problems)
    item = {**valid[0], "id": f"agent_{_slug(target)}", "target": target.strip()}
    on_progress(f'Muse Spark drafted the item: find "{item["find"]}", ask "{item["ask"]}"')
    return item, ""


def start(api, photos, target: str, on_progress, respond=None) -> dict:
    item, problem = _draft(api, target, on_progress)
    if item is None:
        return {"status": "failed", "reason": problem, "test": None}
    found = layer_for(photos, target)
    if found is not None:
        cfg, layer = found
        ts = _city_set(photos, target, layer)
        if len(ts.visible()) >= MIN_POSITIVES:
            on_progress(f"{cfg['name']} lists where each one is: testing on {len(ts.visible())} photos that show a "
                        f"{target} and {len(ts.negatives())} that do not")
            return _test(api, photos, item, ts, "city", cfg["name"], on_progress, respond)
        on_progress(f"{cfg['name']} has too few in these photos: a person answers instead")
    asked = _to_ask(api, photos, item["find"])
    on_progress(f"No city list to test against here: a person answers {len(asked)} photos")
    return {"status": "labels_needed", "photos": asked, "state": {"item": item}}


def resume(api, photos, state: dict, labels: dict, on_progress, respond=None) -> dict:
    item = state["item"]
    ts = _person_set(item["target"], labels)
    if len(ts.visible()) < MIN_POSITIVES:
        reason = (f"Too few photos show a {item['target']} here to test the item ({len(ts.visible())} of "
                  f"{len(labels)}; it needs {MIN_POSITIVES})")
        on_progress(reason)
        return {"status": "failed", "reason": reason, "test": None}
    on_progress(f"Testing on your answers: {len(ts.visible())} photos show a {item['target']}, "
                f"{len(ts.negatives())} do not")
    return _test(api, photos, item, ts, "person", None, on_progress, respond)


def city_check(photos, check, rows: list[dict]) -> dict | None:
    """How many of the city's records in the photos' view the item's rows found (eval.city), when the city publishes
    the asset."""
    found = layer_for(photos, check.target) if check is not None else None
    if found is None:
        return None
    cfg, layer = found
    r = city.evaluate(rows, photos.frames, layer, check.id, inventory=True, id_field="OBJECTID")
    return {"layer": cfg["name"], "found": r["found"], "covered": r["covered"]}
