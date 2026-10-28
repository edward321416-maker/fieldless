"""Stage 4 of the Glimmer gate (docs/specs/2026-10-28-glimmer-modal-design.md, section 3, optional): when a check
costs little, a scan can ask more often. Rebuild a finished scan's picks at shorter group_m spacings from the SAM
areas it already measured (work/scan<N>/find_<id>.json, so no SAM calls), ask Muse Glimmer every pick once, and score
each spacing against the city's inventory: found, repeats of one city point, and finds not in the record.

The flow here is Glimmer alone: an unclear answer or a failed request keeps the pick for a person, as Spark's
fallback would at worst; a "no" drops it.

    GLIMMER_URL=... python -m eval.glimmer_dense --run d5-seattle-rec --scan 4 --group-m 15 8 --out OUT.json
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from assist.tools import counted_inventory
from eval.glimmer_replay import _found, judge_for
from eval.store import load_scan, open_ro
from pipeline import survey_checks

ITEMS = ("utility_pole", "litter_can", "parking_pay_station")  # the checks with judge = "glimmer"


def cached_areas(work: Path, check) -> dict[int, int]:
    """The SAM areas the scan measured for the check's `find` phrase (photo idx -> area)."""
    path = Path(work) / f"find_{check.id}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("phrase") != check.find:
        raise ValueError(f"{path.name} measured {saved.get('phrase')!r}; the item now finds {check.find!r}")
    return {int(i): a for i, a in saved["areas"].items()}


def picks_at(frames: list[dict], areas: dict[int, int], group_m: float | None,
             every: int = survey_checks.SAMPLE_EVERY) -> list[dict]:
    """The picks survey_checks.run_check makes with this group_m, from areas already measured. A sampled photo with
    no saved area counts as a failed request, which ends a run of photos as it does in a scan."""
    by_idx = {f["idx"]: f for f in frames}
    clips: dict[int, list[int]] = {}
    for f in sorted(frames, key=lambda f: f["idx"]):
        clips.setdefault(f["clip"], []).append(f["idx"])
    picks = []
    for clip_idxs in clips.values():
        sampled = {i: areas.get(i) for i in clip_idxs[::every]}
        for group in survey_checks.groups(sampled, clip_idxs):
            for piece in (survey_checks.split_by_distance(group, by_idx, group_m) if group_m else [group]):
                idx = survey_checks.best(piece, sampled)
                picks.append({"idx": idx, "area": sampled[idx], "group": piece})
    return survey_checks.merge_near(picks, by_idx)


def rows_from(item_picks: list[dict], answers: dict[int, dict], item: str) -> list[dict]:
    """The item's rows as Glimmer alone would write them: "no" (or the target not visible) drops a pick, "yes" is an
    asset, and "unclear" or a failed request stays for a person. Glimmer's box sets the row's direction."""
    rows = []
    for p in item_picks:
        answer = answers.get(p["idx"]) or {"error": "not asked"}
        label = "unclear" if "error" in answer else counted_inventory(answer)
        if label == "no":
            continue
        rows.append({"id": -1 - p["idx"], "issue_type": item, "frame_idx": p["idx"], "object_label": label,
                     "review_status": "auto" if label == "yes" else "needs_review", "corrections": [],
                     "model_outputs": {"check": {"answer": label, "box": answer.get("box") or []}}})
    return rows


def scan_picks(data: dict, unavailable: dict, item: str) -> set[int]:
    """The photos the scan itself asked about for the item: its rows, the picks dropped as not the target, and the
    picks whose request failed."""
    rows = {r["frame_idx"] for r in data["issues"] if r["issue_type"] == item}
    gone = unavailable.get(item) or {}
    return rows | set(gone.get("not_target", [])) | set(gone.get("ask", []))


def run(run_dir: Path, scan_id: int, judge, standard, seattle: Path, spacings: list[float | None],
        workers: int = 16) -> dict:
    from pipeline import collect
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, scan_id)
    finally:
        conn.close()
    work = run_dir / "work" / f"scan{scan_id}"
    unavailable = json.loads((work / "checks_unavailable.json").read_text(encoding="utf-8"))
    frames = {f["idx"]: f for f in data["frames"]}
    if hasattr(judge, "wake"):
        judge.wake()
    report = {}
    for item in ITEMS:
        check = standard.check(item)
        areas = cached_areas(work, check)
        by_m = {m: picks_at(data["frames"], areas, m) for m in spacings}
        asked = sorted({p["idx"] for ps in by_m.values() for p in ps})

        def ask(idx):
            try:
                return judge.answer(check, collect.photo_path(frames[idx]["image_id"]))
            except Exception as exc:  # counted, and kept for a person like an unclear answer
                return {"error": f"{exc}"[:300]}

        with ThreadPoolExecutor(workers) as pool:
            answers = dict(zip(asked, pool.map(ask, asked)))
        report[item] = {
            "asked": len(asked),
            "errors": sum("error" in a for a in answers.values()),
            "scan_group_m": check.group_m,
            "same_picks_as_scan": {str(m): {p["idx"] for p in ps} == scan_picks(data, unavailable, item)
                                   for m, ps in by_m.items() if m == check.group_m},
            "by_group_m": {str(m): {"picks": len(ps), "rows": len(rows_from(ps, answers, item)),
                                    **_found(rows_from(ps, answers, item), data, item, seattle)}
                           for m, ps in by_m.items()},
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
    ap.add_argument("--group-m", type=float, nargs="+", default=[15.0, 8.0])
    ap.add_argument("--seattle", default=str(LEDGER.parent / "seattle"))
    ap.add_argument("--reasoning", choices=("low", "medium", "high"), default="medium")
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    judge = judge_for(run_dir, args.reasoning)
    check_budget(LEDGER, planned_usd=1.0)
    started = time.time()
    report = run(run_dir, args.scan, judge, load("us"), Path(args.seattle), args.group_m)
    report["_reasoning"] = args.reasoning
    report["_ledger"] = append(LEDGER, f"dense {args.run} scan {args.scan} group_m {args.group_m}",
                               busy_s=time.time() - started)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for item, r in report.items():
        if item.startswith("_"):
            continue
        print(item, "asked", r["asked"], "errors", r["errors"], "same picks as scan", r["same_picks_as_scan"])
        for m, s in r["by_group_m"].items():
            print(f"  group_m {m}: picks {s['picks']} rows {s['rows']} found {s['found']} of {s['covered']}"
                  f" | repeat {s['repeat']} | not in record {s['not_in_record']}")


if __name__ == "__main__":
    main()
