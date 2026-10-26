"""Turn spike results into the D1 go/no-go report (spec section 20).

Reads the result files written by the spike scripts (spike/out/*.json, calls.jsonl, clips/<stem>/frames.json).
A rate metric needs at least MIN_SCORED scored samples; below that it is left out of the metrics (so `decide`
treats it as not passed) and listed under `insufficient` in the report.
"""
import json
from dataclasses import dataclass

from spike import config
from spike.console import utf8_stdout

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


def decide(criteria: list[Criterion]) -> str:
    passed = {c.name: c.passed for c in criteria}
    entrance_ok = passed.get("sam_stairs_rate", False) and passed.get("step_count_accuracy", False)
    sidewalk_ok = passed.get("tactile_coverage", False) and passed.get("obstacle_detection_rate", False)
    if entrance_ok and sidewalk_ok:
        return "GO: both layers"
    if sidewalk_ok:
        return "GO: sidewalk layer as main, entrance layer reduced"
    if entrance_ok:
        return "GO: entrance layer as main, sidewalk layer moved to recommended scope"
    return "NO-GO: apply fallbacks from spec section 20 and re-test"


def _spark_cost(call: dict) -> float:
    usage = call.get("usage") or {}
    return usage.get("input_tokens", 0) * SPARK_IN_PER_TOKEN + usage.get("output_tokens", 0) * SPARK_OUT_PER_TOKEN


def _load(name: str) -> dict | None:
    path = config.OUT / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


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
    from spike.run_sidewalk import TACTILE_PHRASES

    metrics: dict = {}
    extras: dict = {"insufficient": {}, "sample_sizes": {}, "spark_chunk_errors": 0, "spark_out_of_range": 0,
                    "stairs_phrase": None, "tactile_phrases": {}, "photos": 0}

    coverage = _load("coverage.json")
    kr_best = (coverage or {}).get("kr_best")
    if kr_best:
        metrics["kr_recent_pedestrian_photos"] = kr_best["recent_on_foot_flat"]

    # spark_entrances.json: {clip: {duration_s, hits, truth_count, errors, out_of_range, ...}}
    spark = _load("spark_entrances.json") or {}
    truth_total = sum(v["truth_count"] for v in spark.values())
    recall = sum(v["hits"] for v in spark.values()) / truth_total if truth_total else None
    _rate_metric(metrics, extras, "spark_entrance_recall", recall, truth_total, None)
    extras["spark_chunk_errors"] = sum(len(v.get("errors", [])) for v in spark.values())
    extras["spark_out_of_range"] = sum(v.get("out_of_range", 0) for v in spark.values())

    # sam_stairs.json: {rows, rates, counts, best_phrase}; rates are over stepped entrances, failed requests excluded
    stairs = _load("sam_stairs.json") or {}
    best_stairs = stairs.get("best_phrase")
    extras["stairs_phrase"] = best_stairs
    if best_stairs:
        n = stairs["counts"][best_stairs]
        _rate_metric(metrics, extras, "sam_stairs_rate", stairs["rates"].get(best_stairs), n["scored"], n["failed"])
    else:
        _rate_metric(metrics, extras, "sam_stairs_rate", None, 0, None)

    # step_count.json: {rows, accuracy, scored, failed}; accuracy is over rows without an error
    steps = _load("step_count.json") or {}
    _rate_metric(metrics, extras, "step_count_accuracy", steps.get("accuracy"), steps.get("scored", 0),
                 steps.get("failed"))

    # sidewalk.json: {clip: {coverage_any_phrase, coverage_any_counts, best_tactile_phrase, events, ...}}
    sidewalk = _load("sidewalk.json") or {}
    pooled = [v for v in sidewalk.values() if v.get("coverage_any_phrase") is not None]
    tactile_scored = sum(v["coverage_any_counts"]["scored"] for v in pooled)
    tactile_value = (sum(v["coverage_any_phrase"] * v["coverage_any_counts"]["scored"] for v in pooled)
                     / tactile_scored) if tactile_scored else None
    tactile_failed = sum(v["coverage_any_counts"]["failed"] for v in sidewalk.values()) if sidewalk else None
    _rate_metric(metrics, extras, "tactile_coverage", tactile_value, tactile_scored, tactile_failed)

    events = [e for v in sidewalk.values() for e in v["events"]]
    scored_events = [e for e in events if e.get("scored")]  # detected is None on unscored events
    obstacle_value = (sum(bool(e["detected"]) for e in scored_events) / len(scored_events)) if scored_events else None
    _rate_metric(metrics, extras, "obstacle_detection_rate", obstacle_value, len(scored_events),
                 len(events) - len(scored_events) if sidewalk else None)
    extras["tactile_phrases"] = {clip: v["best_tactile_phrase"] for clip, v in sidewalk.items()
                                 if v.get("best_tactile_phrase")}

    calls_path = config.OUT / "calls.jsonl"
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if calls_path.exists() else []
    spark_cost = sum(_spark_cost(c) for c in calls if c["kind"] == "spark")
    explored = set(PHRASES) | set(TACTILE_PHRASES)
    chosen = ({best_stairs} | set(extras["tactile_phrases"].values())) - {None}
    sam_all = sum(c.get("billed_frames", 0) * SAM_PER_FRAME for c in calls if c["kind"] == "sam")
    sam_production = sum(
        c.get("billed_frames", 0) * SAM_PER_FRAME
        for c in calls if c["kind"] == "sam" and (c["concept"] not in explored or c["concept"] in chosen)
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
    km = 0.0
    for clip_file in spark:
        frames_path = config.CLIPS / clip_file.rsplit(".", 1)[0] / "frames.json"
        if frames_path.exists():
            km += json.loads(frames_path.read_text(encoding="utf-8"))["km"]
    if km:
        metrics["km_covered"] = km
        metrics["cost_per_km_usd"] = (spark_cost + sam_production) / km
    return metrics, extras


def _sample_cell(name: str, extras: dict) -> str:
    size = extras["sample_sizes"].get(name)
    if size is None:
        if name in ("cost_per_100_photos_usd", "seconds_per_100_photos") and extras.get("photos"):
            return f"{extras['photos']:g} photos"
        return "-"
    text = f"{size['scored']} scored"
    if size["failed"] is not None:
        text += f" / {size['failed']} failed"
    return text


def build_report(metrics: dict, extras: dict) -> str:
    criteria = evaluate(metrics)
    decision = decide(criteria)
    korea = next((c for c in criteria if c.name == "kr_recent_pedestrian_photos"), None)
    if korea is None:
        demo_area = "unknown (coverage.json missing)"
    elif korea.passed:
        demo_area = "Korea"
    else:
        demo_area = "overseas (spec section 20 fallback)"
    insufficient = extras["insufficient"]
    rows = [f"| {c.name} | {c.value:.3f} | {c.threshold} | {'PASS' if c.passed else 'FAIL'} "
            f"| {_sample_cell(c.name, extras)} |" for c in criteria]
    rows += [f"| {name} | n/a | {THRESHOLDS[name]} | INSUFFICIENT DATA (n={insufficient[name]}) "
             f"| {_sample_cell(name, extras)} |" for name in AT_LEAST if name in insufficient]
    tactile = "; ".join(f"{clip}: {phrase}" for clip, phrase in extras["tactile_phrases"].items()) or "none"
    lines = [
        "# D1 Gate Report", "",
        f"Decision: **{decision}**", "",
        f"Demo area: **{demo_area}**", "",
        "| Criterion | Value | Threshold | Result | Sample size |", "|---|---|---|---|---|",
        *rows,
        "",
    ]
    if insufficient:
        lines += [f"Fewer than {MIN_SCORED} scored samples counts as not passed: {', '.join(insufficient)}.", ""]
    failed = extras.get("failed_attempts") or {"sam": 0, "spark": 0, "cost_usd": 0.0, "seconds": 0.0, "run_aborts": 0}
    lines += [
        f"Spike total cost including phrase exploration: ${metrics.get('spike_total_cost_usd', 0):.2f}",
        f"Failed API attempts (possibly billed): {failed['sam'] + failed['spark']} "
        f"(SAM {failed['sam']}, Spark {failed['spark']}, {failed['seconds']:.0f} s); "
        f"their upper-bound cost ${failed['cost_usd']:.2f} is included in the spike total"
        + (f"; runs stopped by the circuit breaker: {failed['run_aborts']}" if failed["run_aborts"] else ""),
        f"Kilometers of street in the test clips: {metrics.get('km_covered', 0):.2f}; "
        f"production cost per km: ${metrics.get('cost_per_km_usd', 0):.2f}",
        "`cost_per_100_photos_usd` counts only the chosen phrases (production estimate). "
        "`seconds_per_100_photos` is sequential API time, an upper bound.",
        "",
        f"Spark chunk errors: {extras['spark_chunk_errors']}; "
        f"entrances reported outside their chunk: {extras['spark_out_of_range']}",
        f"Chosen stair phrase: {extras['stairs_phrase'] or 'none'}",
        f"Chosen tactile phrase per clip: {tactile}",
        "D1 finding: Muse Spark samples uploaded video once every 2 s; "
        "Spark clips hold each photo 2 s (config.SPARK_HOLD)",
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
