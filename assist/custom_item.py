"""A survey item the agent writes in the app (docs/specs/2026-10-29-agent-items-design.md), run by server.jobs on a
finished scan's photos (pipeline.item_runs.ScanPhotos). No person is asked anything (user decision, 2026-10-29):

- start() has Muse Spark draft the item (assist.run_inventory.draft), then makes the answer key it is tested on: a
  second model, Muse Spark Open, says of each of KEY_PHOTOS photos whether it shows the target, asked plainly, not
  with the item's wording. The photos are those where SAM finds the drafted phrase, those the city's list says should
  show one ([extra] in eval/cities/<city>.toml; on 2026-10-29 only 3 of 12 such Seattle photos did, so the list
  picks photos but never answers), and others spread along the scan.
- The inventory agent (assist.agent.run_inventory) then tunes the wording against that key, and a Check is returned
  only if code's scores pass: the item keeps at least PASS_KEPT of the photos the key says show the target and at most
  PASS_FALSE of those it says do not. Muse Spark writes; code scores; another model answers.

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
from assist.tools import Bench, counted_inventory
from eval import city, city_check as city_files
from pipeline import config, second_opinion
from pipeline.parallel import run_parallel
from pipeline.sam_output import confident, parse
from pipeline.survey_items import Check

PASS_KEPT, PASS_FALSE = 0.7, 0.2  # of photos showing the target, the share kept; of photos without it, kept wrongly
MIN_POSITIVES = 3  # fewer photos showing the target than this cannot test an item
KEY_PHOTOS = 18  # photos the second model answers: SAM's finds and the city's first, then others spread out
CITY_PHOTOS = 6  # at most this many photos the city's list says should show the target
PROBE_PHOTOS = 40  # at most this many of the scan's photos are asked of SAM to choose them ($0.10)
JUDGE = "Muse Spark Open"
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
        return None  # no data folder or no layer on this server: the photos are chosen without it


def _photo_dicts(photos) -> list[dict]:
    return [{"id": str(f["idx"]), "lat": f["lat"], "lng": f["lng"], "compass": f.get("compass"),
             "sequence": str(f.get("sequence_id") or f["clip"]), "captured_at": f.get("captured_at")}
            for f in photos.frames if f["idx"] in photos.paths]


def _city_photos(photos, layer: dict) -> list[int]:
    """Photos the city's positions say should show the asset (assist.testset_build's cone): candidates only."""
    chosen = testset_build.sample(testset_build.label_photos(_photo_dicts(photos), layer), CITY_PHOTOS, 0)
    return [int(p["id"]) for p in chosen]


def second_model(api):
    """Muse Spark Open on Baseten, which answers the key (None when BASETEN_API_KEY is not set)."""
    return second_opinion.from_env(Path(getattr(api, "log_path", config.data_dir() / "x")).parent)


def _key(judge, photos, target: str, idxs: list[int]) -> TestSet:
    """The second model's answer for each photo, asked plainly whether it shows the target."""
    plain = Check(id="key", find=target, target=target, report_when="", label_yes="", label_no="", label_unclear="",
                  ask=f"Does this street photo show a {target}? Answer yes only if one is clearly in view. "
                      f"{items_mod.UNCLEAR}")
    answers = run_parallel(lambda i: counted_inventory(judge.answer(plain, photos.paths[i])), idxs, 6)
    return TestSet("key", target, {}, tuple(Photo(str(i), a, {}) for i, a in zip(idxs, answers)))


def _spread(seq: list, k: int) -> list:
    """k items of seq, evenly spaced, first and last included when k > 1."""
    if len(seq) <= k:
        return list(seq)
    return [seq[round(n * (len(seq) - 1) / (k - 1))] for n in range(k)] if k > 1 else seq[:1]


def _candidates(api, photos, find: str, city_idxs: list[int]) -> list[int]:
    """KEY_PHOTOS photos for the key: where SAM finds the drafted phrase (from at most PROBE_PHOTOS asked), then the
    city's, then others spread along the scan, so photos without the target are in the key too."""
    probe = _spread(sorted(photos.paths), PROBE_PHOTOS)
    found = [i for i in probe if confident(parse(api.sam_image(find, photos.paths[i]), video=False))]
    picked = list(dict.fromkeys(_spread(found, KEY_PHOTOS // 2) + city_idxs))[:KEY_PHOTOS]
    rest = [i for i in sorted(photos.paths) if i not in picked]
    return sorted(picked + _spread(rest, KEY_PHOTOS - len(picked)))


def _write_drafts(target: str, final: dict, test: dict, history: list[dict]) -> None:
    """The tested item as a TOML a person may add to pipeline/standards/<code>.toml, and the run as JSON. A disk that
    refuses does not stop the run."""
    try:
        out = config.data_dir() / "items" / "drafts"
        out.mkdir(parents=True, exist_ok=True)
        stem = out / f"agent-{_slug(target).replace('_', '-')}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        header = [f'Inventory item written by Muse Spark in the app for "{target}"',
                  f"tested on {JUDGE}'s answers: kept {test['kept'][0]} of {test['kept'][1]} photos showing it, "
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


def _test(api, photos, item: dict, ts: TestSet, layer: str | None, on_progress, respond) -> dict:
    bench = Bench(api, ts, items=[item], photo=lambda pid: photos.paths[int(pid)])
    result = agent.run_inventory(respond or make_responder(api, agent.INVENTORY_TOOLS), bench, item,
                                 on_step=lambda *step: on_progress(agent.describe(*step)))
    final = _final(item, result, bench)
    found = bench.last("score_find", phrase=final["find"]) or {}
    scored = bench.last("score_confirm", item_id=item["id"], find=final["find"], ask=final["ask"])
    test = {"labels": "model", "judge": JUDGE, "layer": layer, "phrase": final["find"], "ask": final["ask"], "calls": result["calls"],
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
    on_progress(f'Muse Spark drafted the item: find "{item["find"]}", ask "{agent._short(item["ask"], 90)}"')
    return item, ""


def start(api, photos, target: str, on_progress, respond=None, judge=None) -> dict:
    item, problem = _draft(api, target, on_progress)
    if item is None:
        return {"status": "failed", "reason": problem, "test": None}
    judge = judge or second_model(api)
    if judge is None:
        return {"status": "failed", "test": None,
                "reason": f"No second model to test against: {JUDGE} is not set up on this server"}
    found = layer_for(photos, target)
    city_idxs = _city_photos(photos, found[1]) if found else []
    idxs = _candidates(api, photos, item["find"], city_idxs)
    on_progress(f"{JUDGE} looks at {len(idxs)} photos and says which show a {target}"
                + (f" ({len(city_idxs)} of them where {found[0]['name']} lists one)" if city_idxs else ""))
    ts = _key(judge, photos, target, idxs)
    seen = len(ts.visible())
    if seen < MIN_POSITIVES:
        reason = (f"Too few photos show a {target} here to test the item ({JUDGE} saw one in {seen} of {len(idxs)}; "
                  f"it needs {MIN_POSITIVES})")
        on_progress(reason)
        return {"status": "failed", "reason": reason, "test": None}
    on_progress(f"{JUDGE} saw a {target} in {seen} photos and none in {len(ts.negatives())}: testing on those")
    return _test(api, photos, item, ts, found[0]["name"] if found else None, on_progress, respond)


def resume(api, photos, state: dict, labels: dict, on_progress, respond=None) -> dict:
    """Kept for the server's interface: this version does not ask a person, so no run waits for answers."""
    return {"status": "failed", "test": None, "reason": "This version does not ask a person for answers"}


def city_check(photos, check, rows: list[dict]) -> dict | None:
    """How many of the city's records in the photos' view the item's rows found (eval.city), when the city publishes
    the asset."""
    found = layer_for(photos, check.target) if check is not None else None
    if found is None:
        return None
    cfg, layer = found
    r = city.evaluate(rows, photos.frames, layer, check.id, inventory=True, id_field="OBJECTID")
    return {"layer": cfg["name"], "found": r["found"], "covered": r["covered"]}
