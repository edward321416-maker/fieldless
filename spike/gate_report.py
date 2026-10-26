"""Turn spike results into the D1 go/no-go report (spec section 20).

Reads the result files written by the spike scripts (spike/out/*.json, calls.jsonl, clips/<stem>/frames.json).
A rate metric needs at least MIN_SCORED scored samples; below that it is left out of the metrics and listed under
`insufficient` in the report, as are the Korea, cost and time numbers when their inputs are missing.

Decision (spec section 20, see decide): each layer is demoted only by its core metric (entrance: sam_stairs_rate,
sidewalk: tactile_coverage). A failed secondary metric triggers its listed fallback (see fallbacks), not a
demotion, and a layer whose core metric is insufficient is reported as untested rather than reduced.
"""
import json
from dataclasses import dataclass

from spike import config
from spike.console import utf8_stdout
from spike.truth import clip_entries, truth_path, truth_sha256

THRESHOLDS = {
    "kr_recent_pedestrian_photos": 300,
    "spark_entrance_recall": 0.8,
    "sam_stairs_rate": 0.7,
    "step_count_accuracy": 0.7,
    "tactile_coverage": 0.8,
    "obstacle_detection_rate": 0.7,
    "cost_per_100_photos_usd": 0.6,
    "seconds_per_100_photos": 300.0,
}
AT_LEAST = ["kr_recent_pedestrian_photos", "spark_entrance_recall", "sam_stairs_rate", "step_count_accuracy",
            "tactile_coverage", "obstacle_detection_rate"]
BELOW = ["cost_per_100_photos_usd", "seconds_per_100_photos"]
SPARK_IN_PER_TOKEN = 1.25e-6
SPARK_OUT_PER_TOKEN = 4.25e-6
SAM_PER_FRAME = 0.0002
MIN_SCORED = 3  # a rate over fewer scored samples is not evidence either way
FAILED_KINDS = ("sam_failed", "spark_failed")  # calls.jsonl lines for failed attempts (meta_client._log_failures)
RUN_OUTPUTS = ("spark_entrances.json", "sam_stairs.json", "step_count.json", "sidewalk.json")
LAYERS = {"entrance": "sam_stairs_rate", "sidewalk": "tactile_coverage"}  # the core metric of each layer
CORE_FALLBACK = "switch to box prompts (Spark boxes → SAM) and re-test before demoting"
SIZE_FALLBACK = "reduce the number of concepts and the chunk size"
FALLBACKS = {  # spec section 20, "fail" column
    "kr_recent_pedestrian_photos": "demo in a photo-dense overseas city (US ADA rules, tactile paving in Tokyo); "
                                   "cover Korea with uploaded video",
    "spark_entrance_recall": "Spark reads only; entrance candidates come from SAM `door` detection",
    "sam_stairs_rate": CORE_FALLBACK,
    "step_count_accuracy": "height estimate simplified to standard riser height × step count; "
                           "uncertain counts shown as ⚪",
    "tactile_coverage": CORE_FALLBACK,
    "obstacle_detection_rate": "reduce obstacle types to the ones SAM detects reliably",
    "cost_per_100_photos_usd": SIZE_FALLBACK,
    "seconds_per_100_photos": SIZE_FALLBACK,
}
UNTESTED = "untested (insufficient data)"


@dataclass(frozen=True)
class Criterion:
    name: str
    value: float
    threshold: float
    passed: bool


def evaluate(metrics: dict) -> list[Criterion]:
    out = [Criterion(k, metrics[k], THRESHOLDS[k], metrics[k] >= THRESHOLDS[k]) for k in AT_LEAST if k in metrics]
    out += [Criterion(k, metrics[k], THRESHOLDS[k], metrics[k] < THRESHOLDS[k]) for k in BELOW if k in metrics]
    return out


def layer_status(criteria: list[Criterion]) -> dict[str, str]:
    """"pass", "fail" or "untested" per layer, from its core metric alone (untested: the metric is missing)."""
    passed = {c.name: c.passed for c in criteria}
    return {layer: "untested" if core not in passed else "pass" if passed[core] else "fail"
            for layer, core in LAYERS.items()}


def decide(criteria: list[Criterion]) -> str:
    """Spec section 20: a layer is demoted only when its core metric fails. Secondary metrics (step count, obstacles,
    Spark recall, cost, time) trigger fallbacks, never a demotion; a missing core metric leaves its layer untested."""
    status = layer_status(criteria)
    entrance, sidewalk = status["entrance"], status["sidewalk"]
    if entrance == sidewalk == "pass":
        return "GO: both layers"
    if entrance == "pass":
        rest = "sidewalk layer moved to recommended scope" if sidewalk == "fail" else f"sidewalk layer {UNTESTED}"
        return f"GO: entrance layer as main, {rest}"
    if sidewalk == "pass":
        rest = "entrance layer reduced" if entrance == "fail" else f"entrance layer {UNTESTED}"
        return f"GO: sidewalk layer as main, {rest}"
    if entrance == sidewalk == "fail":
        return "NO-GO: apply fallbacks from spec section 20 and re-test"
    parts = ", ".join(f"{layer} layer {'failed' if st == 'fail' else UNTESTED}" for layer, st in status.items())
    todo = "apply the fallbacks and label enough samples" if "fail" in status.values() else "label enough samples"
    return f"NO-GO: {parts}; {todo}, then re-test"


def fallbacks(criteria: list[Criterion], insufficient: dict) -> list[str]:
    """The spec section 20 action for every failed criterion, then a note for each layer whose core metric is
    insufficient (that layer is untested, so its fallback is not triggered yet)."""
    out = [f"{c.name}: {FALLBACKS[c.name]}" for c in criteria if not c.passed and c.name in FALLBACKS]
    out += [f"{core}: {layer} layer {UNTESTED}, n={insufficient[core]}; label more samples and re-run before deciding"
            for layer, core in LAYERS.items() if core in insufficient]
    return out


def _spark_cost(call: dict) -> float:
    usage = call.get("usage") or {}
    return usage.get("input_tokens", 0) * SPARK_IN_PER_TOKEN + usage.get("output_tokens", 0) * SPARK_OUT_PER_TOKEN


def _load(name: str) -> dict | None:
    path = config.OUT / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def read_calls() -> tuple[list[dict], int]:
    """calls.jsonl entries and the number of lines skipped as unreadable (e.g. a last line cut off by a crash)."""
    path = config.OUT / "calls.jsonl"
    if not path.exists():
        return [], 0
    calls, skipped = [], 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(entry, dict) and isinstance(entry.get("kind"), str):
            calls.append(entry)
        else:
            skipped += 1
    return calls, skipped


def _rate(hits: int, n: int) -> dict:
    return {"value": hits / n if n else None, "n": n}


def extra_measurements(stairs: dict, steps: dict, spark: dict, sidewalk: dict) -> dict[str, dict]:
    """Numbers printed in the report but not gate criteria, each {"value": rate or None, "n": sample size}.

    - step_count_accuracy_stepped: step_count.json rows without an error whose truth has >= 1 step.
    - stairs_rate_2plus_steps: sam_stairs.json rows with step_count >= 2, best phrase only, error rows/phrases skipped.
    - obstacle_rate_spark_types: scored sidewalk events whose label Spark listed in that clip's obstacle_types.
    """
    stepped = [r for r in steps.get("rows", []) if "error" not in r and (r.get("truth_steps") or 0) >= 1]
    best = stairs.get("best_phrase")
    multi = [r["phrases"][best] for r in stairs.get("rows", [])
             if best and r.get("step_count", 0) >= 2 and "error" not in r
             and best in r.get("phrases", {}) and "error" not in r["phrases"][best]]
    known = []
    for clip, v in sidewalk.items():
        types = {t.strip().lower() for t in spark.get(clip, {}).get("obstacle_types", [])}
        known += [e for e in v.get("events", []) if e.get("scored") and e["label"].strip().lower() in types]
    return {
        "step_count_accuracy_stepped": _rate(sum(bool(r.get("correct")) for r in stepped), len(stepped)),
        "stairs_rate_2plus_steps": _rate(sum(bool(p.get("detected")) for p in multi), len(multi)),
        "obstacle_rate_spark_types": _rate(sum(e["detected"] is True for e in known), len(known)),
    }


def _recorded_truth_hash(data: dict) -> str | None:
    return data.get("truth_sha256") or (data.get("_meta") or {}).get("truth_sha256")


def truth_consistency(outputs: dict[str, dict | None]) -> tuple[dict[str, str | None], str | None]:
    """Compare the truth.json hash recorded in each run output with each other and with the current truth.json.

    Returns ({name: hash or None}, warning or None). "current truth.json" is the file now; outputs that are missing
    are left out, and an output without a recorded hash counts as a mismatch (it predates hashing).
    """
    current = truth_sha256() if truth_path().exists() else None
    hashes = {name: _recorded_truth_hash(data) for name, data in outputs.items() if data is not None}
    if current is not None:
        hashes["current truth.json"] = current
    if None not in hashes.values() and len(set(hashes.values())) <= 1:
        return hashes, None
    shown = ", ".join(f"{name} {h[:12] if h else '(no hash recorded)'}" for name, h in hashes.items())
    return hashes, (f"WARNING: results come from different truth.json versions ({shown}); "
                    "rerun the stale steps before trusting this report.")


def _rate_metric(metrics: dict, extras: dict, name: str, value: float | None, scored: int,
                 failed: int | None) -> None:
    """Record a rate with its sample size; below MIN_SCORED it goes to extras["insufficient"], not into metrics."""
    extras["sample_sizes"][name] = {"scored": scored, "failed": failed}
    if scored < MIN_SCORED or value is None:
        extras["insufficient"][name] = scored
    else:
        metrics[name] = value


def collect_metrics() -> tuple[dict, dict]:
    """Return (metrics, extras). metrics holds numbers only; extras holds sample sizes, counters and chosen phrases."""
    from spike.run_sam_stairs import PHRASES

    metrics: dict = {}
    extras: dict = {"insufficient": {}, "sample_sizes": {}, "spark_chunk_errors": 0, "spark_out_of_range": 0,
                    "stairs_phrase": None, "tactile_phrases": {}, "photos": 0}

    coverage = _load("coverage.json")
    kr_best = (coverage or {}).get("kr_best")
    extras["coverage_file"] = coverage is not None
    extras["kr_best"] = kr_best
    if kr_best:
        metrics["kr_recent_pedestrian_photos"] = kr_best["recent_on_foot_flat"]
    else:
        extras["insufficient"]["kr_recent_pedestrian_photos"] = 0

    raw = {name: _load(name) for name in RUN_OUTPUTS}
    extras["truth_hashes"], extras["truth_warning"] = truth_consistency(raw)

    # spark_entrances.json: {"_meta": {...}, clip: {duration_s, hits, truth_count, errors, out_of_range, ...}}
    spark = clip_entries(raw["spark_entrances.json"] or {})
    truth_total = sum(v["truth_count"] for v in spark.values())
    recall = sum(v["hits"] for v in spark.values()) / truth_total if truth_total else None
    _rate_metric(metrics, extras, "spark_entrance_recall", recall, truth_total, None)
    extras["spark_chunk_errors"] = sum(len(v.get("errors", [])) for v in spark.values())
    extras["spark_out_of_range"] = sum(v.get("out_of_range", 0) for v in spark.values())

    # sam_stairs.json: {rows, rates, counts, best_phrase}; rates are over stepped entrances, failed requests excluded
    stairs = raw["sam_stairs.json"] or {}
    best_stairs = stairs.get("best_phrase")
    extras["stairs_phrase"] = best_stairs
    if best_stairs:
        n = stairs["counts"][best_stairs]
        _rate_metric(metrics, extras, "sam_stairs_rate", stairs["rates"].get(best_stairs), n["scored"], n["failed"])
    else:
        _rate_metric(metrics, extras, "sam_stairs_rate", None, 0, None)

    # step_count.json: {rows, accuracy, scored, failed}; accuracy is over rows without an error
    steps = raw["step_count.json"] or {}
    _rate_metric(metrics, extras, "step_count_accuracy", steps.get("accuracy"), steps.get("scored", 0),
                 steps.get("failed"))

    # sidewalk.json: {"_meta": {...}, clip: {coverage_any_phrase, coverage_any_counts, best_tactile_phrase, ...}}
    sidewalk = clip_entries(raw["sidewalk.json"] or {})
    pooled = [v for v in sidewalk.values() if v.get("coverage_any_phrase") is not None]
    tactile_scored = sum(v["coverage_any_counts"]["scored"] for v in pooled)
    # hits are whole photos: round each clip's rate x scored back to a count, so 32 of 40 is exactly 0.8
    tactile_hits = sum(round(v["coverage_any_phrase"] * v["coverage_any_counts"]["scored"]) for v in pooled)
    tactile_value = tactile_hits / tactile_scored if tactile_scored else None
    tactile_failed = sum(v["coverage_any_counts"]["failed"] for v in sidewalk.values()) if sidewalk else None
    _rate_metric(metrics, extras, "tactile_coverage", tactile_value, tactile_scored, tactile_failed)

    events = [e for v in sidewalk.values() for e in v["events"]]
    scored_events = [e for e in events if e.get("scored")]  # detected is None on unscored events
    obstacle_value = (sum(bool(e["detected"]) for e in scored_events) / len(scored_events)) if scored_events else None
    _rate_metric(metrics, extras, "obstacle_detection_rate", obstacle_value, len(scored_events),
                 len(events) - len(scored_events) if sidewalk else None)
    extras["tactile_phrases"] = {clip: v["best_tactile_phrase"] for clip, v in sidewalk.items()
                                 if v.get("best_tactile_phrase")}
    extras["extra_measurements"] = extra_measurements(stairs, steps, spark, sidewalk)

    calls, extras["calls_skipped_lines"] = read_calls()
    extras["calls_read"] = len(calls)
    spark_cost = sum(_spark_cost(c) for c in calls if c["kind"] == "spark")
    # Production runs only the chosen stair phrase, but all three tactile phrases: tactile_coverage is their union.
    not_in_production = set(PHRASES) - {best_stairs}
    sam_all = sum(c.get("billed_frames", 0) * SAM_PER_FRAME for c in calls if c["kind"] == "sam")
    sam_production = sum(
        c.get("billed_frames", 0) * SAM_PER_FRAME
        for c in calls if c["kind"] == "sam" and c.get("concept") not in not_in_production
    )
    # Failed attempts may or may not be billed: count them at full price (an upper bound) in the spike total only.
    sam_failed = [c for c in calls if c["kind"] == "sam_failed"]
    spark_failed = [c for c in calls if c["kind"] == "spark_failed"]
    failed_cost = (sum(c.get("billed_frames", 0) * SAM_PER_FRAME for c in sam_failed)
                   + sum(_spark_cost(c) for c in spark_failed))
    extras["failed_attempts"] = {"sam": len(sam_failed), "spark": len(spark_failed), "cost_usd": failed_cost,
                                 "seconds": sum(c.get("seconds", 0) for c in sam_failed + spark_failed),
                                 "run_aborts": sum(c["kind"] == "run_aborted" for c in calls)}
    metrics["spike_total_cost_usd"] = spark_cost + sam_all + failed_cost
    photos = sum(v["duration_s"] for v in spark.values()) * config.CLIP_FPS
    extras["photos"] = photos
    if photos and calls:  # without recorded calls a $0 cost would read as a pass
        metrics["cost_per_100_photos_usd"] = (spark_cost + sam_production) / photos * 100
        # a successful request's seconds already include its failed attempts and backoff, so *_failed lines are
        # left out here to avoid counting that time twice
        metrics["seconds_per_100_photos"] = sum(c.get("seconds", 0) for c in calls
                                                if c["kind"] not in FAILED_KINDS) / photos * 100
    else:
        extras["insufficient"]["cost_per_100_photos_usd"] = 0
        extras["insufficient"]["seconds_per_100_photos"] = 0
    km = 0.0
    for clip_file in spark:
        frames_path = config.CLIPS / clip_file.rsplit(".", 1)[0] / "frames.json"
        if frames_path.exists():
            km += json.loads(frames_path.read_text(encoding="utf-8"))["km"]
    if km:
        metrics["km_covered"] = km
        metrics["cost_per_km_usd"] = (spark_cost + sam_production) / km
    return metrics, extras


def _value(value) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else f"{value:.3f}"


def _result(name: str, passed: bool | None, extras: dict) -> str:
    """PASS / FAIL, or INSUFFICIENT DATA when passed is None; Spark recall is marked incomplete after chunk errors."""
    text = f"INSUFFICIENT DATA (n={extras['insufficient'][name]})" if passed is None else "PASS" if passed else "FAIL"
    if name == "spark_entrance_recall" and extras.get("spark_chunk_errors"):
        text += f" (incomplete: {extras['spark_chunk_errors']} chunk errors)"
    return text


def _sample_cell(name: str, extras: dict) -> str:
    size = extras["sample_sizes"].get(name)
    if size is None:
        if name in ("cost_per_100_photos_usd", "seconds_per_100_photos") and extras.get("photos"):
            return f"{extras['photos']:g} photos"
        kr_best = extras.get("kr_best")
        if name == "kr_recent_pedestrian_photos" and kr_best:
            capped = kr_best.get("capped")
            note = "" if capped is None else ", capped: a lower bound" if capped else ", not capped"
            return f"{kr_best.get('area', '?')}{note}"
        return "-"
    text = f"{size['scored']} scored"
    if size["failed"] is not None:
        text += f" / {size['failed']} failed"
    return text


def _extra_lines(extras: dict) -> list[str]:
    m = extras.get("extra_measurements")
    if not m:
        return ["- none"]

    def line(label: str, entry: dict) -> str:
        value = "n/a" if entry["value"] is None else f"{entry['value']:.3f}"
        return f"- {label}: {value} (n={entry['n']})"

    return [
        line("Step-count accuracy on stepped entrances (truth step_count >= 1)", m["step_count_accuracy_stepped"]),
        line(f"Stair detection rate on entrances with 2+ steps (phrase {extras['stairs_phrase'] or 'none'})",
             m["stairs_rate_2plus_steps"]),
        line("Obstacle detection rate on events whose label Spark also listed for the clip",
             m["obstacle_rate_spark_types"]),
    ]


def build_report(metrics: dict, extras: dict) -> str:
    criteria = evaluate(metrics)
    decision = decide(criteria)
    insufficient = extras["insufficient"]
    korea = next((c for c in criteria if c.name == "kr_recent_pedestrian_photos"), None)
    if korea is not None:
        demo_area = "Korea" if korea.passed else "overseas (spec section 20 fallback)"
    elif extras.get("coverage_file"):
        demo_area = "unknown (no Korean area summarized)"
    else:
        demo_area = "unknown (coverage.json missing)"
    rows = [f"| {c.name} | {_value(c.value)} | {c.threshold} | {_result(c.name, c.passed, extras)} "
            f"| {_sample_cell(c.name, extras)} |" for c in criteria]
    rows += [f"| {name} | n/a | {THRESHOLDS[name]} | {_result(name, None, extras)} "
             f"| {_sample_cell(name, extras)} |" for name in AT_LEAST + BELOW if name in insufficient]
    tactile = "; ".join(f"{clip}: {phrase}" for clip, phrase in extras["tactile_phrases"].items()) or "none"
    actions = fallbacks(criteria, insufficient)
    warning = extras.get("truth_warning")
    lines = [
        "# D1 Gate Report", "",
        *([f"**{warning}**", ""] if warning else []),
        f"Decision: **{decision}**", "",
        f"Demo area: **{demo_area}**", "",
        "| Criterion | Value | Threshold | Result | Sample size |", "|---|---|---|---|---|",
        *rows,
        "",
    ]
    if insufficient:
        lines += [f"Not passed for lack of data (fewer than {MIN_SCORED} scored samples, or no input): "
                  f"{', '.join(insufficient)}.", ""]
    lines += ["## Fallbacks (spec section 20)", "", *([f"- {a}" for a in actions] or ["- none"]), ""]
    failed = extras.get("failed_attempts") or {"sam": 0, "spark": 0, "cost_usd": 0.0, "seconds": 0.0, "run_aborts": 0}
    if extras.get("calls_read"):
        total_cost = f"${metrics.get('spike_total_cost_usd', 0):.2f}"
    else:
        total_cost = "unknown (no calls recorded in calls.jsonl)"
    if "km_covered" in metrics:
        km_line = (f"Kilometers of street in the test clips: {metrics['km_covered']:.2f}; "
                   f"production cost per km: ${metrics['cost_per_km_usd']:.2f}")
    else:
        km_line = "Kilometers of street in the test clips: unknown (no frames.json with km for the Spark clips)"
    lines += [
        f"Spike total cost including phrase exploration: {total_cost}",
        f"Failed API attempts (possibly billed): {failed['sam'] + failed['spark']} "
        f"(SAM {failed['sam']}, Spark {failed['spark']}, {failed['seconds']:.0f} s); "
        f"their upper-bound cost ${failed['cost_usd']:.2f} is included in the spike total"
        + (f"; runs stopped by the circuit breaker: {failed['run_aborts']}" if failed["run_aborts"] else ""),
        f"calls.jsonl: {extras.get('calls_read', 0)} entries read, "
        f"{extras.get('calls_skipped_lines', 0)} unreadable lines skipped",
        km_line,
        "`cost_per_100_photos_usd` is a production estimate: Spark, the chosen stair phrase, all three tactile "
        "phrases (tactile_coverage is their union) and the obstacle labels. "
        "`seconds_per_100_photos` is sequential API time, an upper bound.",
        "",
        f"Spark chunk errors: {extras['spark_chunk_errors']}; "
        f"entrances reported outside their chunk: {extras['spark_out_of_range']}",
        f"Chosen stair phrase: {extras['stairs_phrase'] or 'none'}",
        f"Chosen tactile phrase per clip: {tactile}",
        "D1 finding: Muse Spark samples uploaded video once every 2 s; "
        f"Spark clips hold each photo {config.SPARK_HOLD / config.CLIP_FPS:g} s (config.SPARK_HOLD)",
        "",
        "## Extra measurements (not gate criteria)",
        "",
        *_extra_lines(extras),
        "",
        "## Notes",
        "",
        "- Best stair phrase, best tactile phrase, observed API limits, and fallbacks applied go here.",
    ]
    return "\n".join(lines)


def main() -> None:
    utf8_stdout()
    metrics, extras = collect_metrics()
    report = build_report(metrics, extras)
    (config.OUT / "gate_report.md").write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
