import hashlib
import json

import pytest

from spike import config, gate_report
from spike.gate_report import MIN_SCORED, build_report, collect_metrics, decide, evaluate, fallbacks


# --- decision logic (brief) -------------------------------------------------------------------

def test_all_pass_means_both_layers():
    criteria = evaluate({
        "spark_entrance_recall": 0.9, "sam_stairs_rate": 0.8, "step_count_accuracy": 0.7,
        "tactile_coverage": 0.85, "obstacle_detection_rate": 0.75,
        "cost_per_100_photos_usd": 0.3, "seconds_per_100_photos": 200, "kr_recent_pedestrian_photos": 800,
    })
    assert all(c.passed for c in criteria)
    assert decide(criteria) == "GO: both layers"


def test_korea_coverage_does_not_change_layer_decision():
    criteria = evaluate({"sam_stairs_rate": 0.8, "step_count_accuracy": 0.8,
                         "tactile_coverage": 0.9, "obstacle_detection_rate": 0.8,
                         "kr_recent_pedestrian_photos": 50})
    assert decide(criteria) == "GO: both layers"


def test_sidewalk_only():
    criteria = evaluate({"sam_stairs_rate": 0.5, "step_count_accuracy": 0.9,
                         "tactile_coverage": 0.9, "obstacle_detection_rate": 0.8})
    assert decide(criteria).startswith("GO: sidewalk")


def test_entrance_only():
    criteria = evaluate({"sam_stairs_rate": 0.8, "step_count_accuracy": 0.8,
                         "tactile_coverage": 0.2, "obstacle_detection_rate": 0.8})
    assert decide(criteria).startswith("GO: entrance")


def test_no_go():
    assert decide(evaluate({"sam_stairs_rate": 0.1, "tactile_coverage": 0.1})).startswith("NO-GO")


def test_cost_limit_is_strict_upper_bound():
    (criterion,) = evaluate({"cost_per_100_photos_usd": 0.6})
    assert criterion.passed is False


# --- collect_metrics against small fake result files ------------------------------------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    clips = tmp_path / "clips"
    clips.mkdir()
    monkeypatch.setattr(config, "OUT", out)
    monkeypatch.setattr(config, "CLIPS", clips)
    return out, clips


def write(out, name, data):
    (out / name).write_text(json.dumps(data), encoding="utf-8")


def write_calls(out, lines):
    (out / "calls.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def spark_clip(duration=100, hits=4, truth=5, errors=(), out_of_range=0):
    return {"duration_s": duration, "entrances": [], "obstacle_types": [], "errors": list(errors),
            "out_of_range": out_of_range, "hits": hits, "misses": truth - hits, "false_positives": 0,
            "truth_count": truth}


def stairs_file(best="steps", rates=None, counts=None, rows=None):
    rates = {"stairs": 0.5, "steps": 0.8} if rates is None else rates
    counts = ({"stairs": {"scored": 5, "failed": 1}, "steps": {"scored": 5, "failed": 1}}
              if counts is None else counts)
    return {"rows": rows or [], "rates": rates, "counts": counts, "best_phrase": best}


def steps_file(accuracy=0.6, scored=5, failed=1):
    rows = [{"clip": "street_a.mp4", "index": 0, "truth_steps": 2, "step_count": 2, "correct": True}]
    rows.append({"clip": "street_a.mp4", "index": 1, "truth_steps": 1, "step_count": None, "correct": None,
                 "error": "RuntimeError: boom"})
    return {"rows": rows, "accuracy": accuracy, "scored": scored, "failed": failed}


def sidewalk_clip(any_rate, scored, failed=0, best="tactile paving", events=()):
    return {"coverage": {"tactile paving": any_rate}, "coverage_counts": {"tactile paving": {"scored": scored,
            "failed": failed}}, "coverage_any_phrase": any_rate,
            "coverage_any_counts": {"scored": scored, "failed": failed}, "best_tactile_phrase": best,
            "obstacle_labels_queried": [], "events": list(events), "errors": []}


def event(detected, scored):
    return {"label": "bicycle", "t_start": 1.0, "t_end": 2.0, "on_tactile_truth": True, "detected": detected,
            "scored": scored, "max_consecutive_photos": 2 if detected else 0, "overlap_detected": None}


def test_obstacle_rate_excludes_unscored_events(env):
    out, _ = env
    write(out, "sidewalk.json", {
        "street_a.mp4": sidewalk_clip(0.9, 10, events=[event(True, True), event(None, False), event(True, True)]),
        "street_b.mp4": sidewalk_clip(0.9, 10, events=[event(False, True), event(None, False)]),
    })
    metrics, extras = collect_metrics()
    assert metrics["obstacle_detection_rate"] == pytest.approx(2 / 3)
    assert extras["sample_sizes"]["obstacle_detection_rate"] == {"scored": 3, "failed": 2}


def test_tactile_coverage_is_pooled_by_scored_photos(env):
    out, _ = env
    write(out, "sidewalk.json", {
        "street_a.mp4": sidewalk_clip(1.0, 10),
        "street_b.mp4": sidewalk_clip(0.5, 30, failed=2),
        "street_c.mp4": sidewalk_clip(None, 0, failed=7, best=None),  # nothing scored: left out of the pool
    })
    metrics, extras = collect_metrics()
    assert metrics["tactile_coverage"] == pytest.approx((1.0 * 10 + 0.5 * 30) / 40)  # not the mean of 1.0 and 0.5
    assert extras["sample_sizes"]["tactile_coverage"] == {"scored": 40, "failed": 9}
    assert extras["tactile_phrases"] == {"street_a.mp4": "tactile paving", "street_b.mp4": "tactile paving"}


def test_stairs_rate_comes_from_rates_of_best_phrase_and_tolerates_error_rows(env):
    out, _ = env
    rows = [
        {"clip": "street_a.mp4", "index": 0, "t": 5.0, "window_start": 3.0, "step_count": 2,
         "phrases": {"steps": {"detected": True, "max_c": 0.9, "best_frame_t": 5.0}}},
        {"clip": "street_a.mp4", "index": 1, "t": 20.0, "window_start": 18.0, "step_count": 1, "phrases": {},
         "error": "RuntimeError: upload failed"},  # no phrase entries at all
    ]
    write(out, "sam_stairs.json", stairs_file(best="steps", rows=rows))
    write(out, "step_count.json", steps_file())
    metrics, extras = collect_metrics()
    assert metrics["sam_stairs_rate"] == 0.8  # rates["steps"], not recomputed from rows
    assert extras["stairs_phrase"] == "steps"
    assert extras["sample_sizes"]["sam_stairs_rate"] == {"scored": 5, "failed": 1}
    assert metrics["step_count_accuracy"] == 0.6
    assert extras["sample_sizes"]["step_count_accuracy"] == {"scored": 5, "failed": 1}


def test_metric_below_min_scored_is_left_out_and_listed_as_insufficient(env):
    out, _ = env
    write(out, "step_count.json", steps_file(accuracy=1.0, scored=MIN_SCORED - 1, failed=0))
    write(out, "sam_stairs.json", stairs_file(counts={"stairs": {"scored": 1, "failed": 0},
                                                       "steps": {"scored": 2, "failed": 0}}))
    write(out, "spark_entrances.json", {"street_a.mp4": spark_clip(hits=2, truth=2)})
    metrics, extras = collect_metrics()
    for name in ("step_count_accuracy", "sam_stairs_rate", "spark_entrance_recall"):
        assert name not in metrics
    assert extras["insufficient"]["step_count_accuracy"] == MIN_SCORED - 1
    assert extras["insufficient"]["sam_stairs_rate"] == 2
    assert extras["insufficient"]["spark_entrance_recall"] == 2
    assert not any(c.name == "step_count_accuracy" for c in evaluate(metrics))


def test_exactly_min_scored_is_enough(env):
    out, _ = env
    write(out, "step_count.json", steps_file(accuracy=2 / 3, scored=MIN_SCORED, failed=0))
    metrics, extras = collect_metrics()
    assert metrics["step_count_accuracy"] == pytest.approx(2 / 3)
    assert "step_count_accuracy" not in extras["insufficient"]


def test_insufficient_secondary_metric_does_not_block_its_layer(env):
    # Spec section 20 (changed from the first gate report): only the core metric decides a layer.
    out, _ = env
    write(out, "sam_stairs.json", stairs_file(rates={"steps": 0.9}, counts={"steps": {"scored": 6, "failed": 0}}))
    write(out, "step_count.json", steps_file(accuracy=1.0, scored=2, failed=0))  # too few, but secondary
    write(out, "sidewalk.json", {"street_a.mp4": sidewalk_clip(0.9, 10, events=[
        event(True, True), event(True, True), event(True, True), event(False, True)])})
    metrics, extras = collect_metrics()
    assert "step_count_accuracy" in extras["insufficient"]
    assert decide(evaluate(metrics)) == "GO: both layers"


def test_missing_result_files_are_insufficient_not_crashes(env):
    metrics, extras = collect_metrics()
    assert extras["insufficient"] == {"kr_recent_pedestrian_photos": 0, "spark_entrance_recall": 0,
                                      "sam_stairs_rate": 0, "step_count_accuracy": 0, "tactile_coverage": 0,
                                      "obstacle_detection_rate": 0, "cost_per_100_photos_usd": 0,
                                      "seconds_per_100_photos": 0}
    assert "cost_per_100_photos_usd" not in metrics  # no calls, no photos: nothing to divide
    decision = decide(evaluate(metrics))
    assert decision.startswith("NO-GO")
    assert "entrance layer untested (insufficient data)" in decision
    assert "sidewalk layer untested (insufficient data)" in decision
    assert "reduced" not in decision


def test_spark_recall_and_error_counters(env):
    out, _ = env
    write(out, "spark_entrances.json", {
        "street_a.mp4": spark_clip(hits=4, truth=5, errors=[{"start": 60.0, "end": 100.0, "error": "x"}],
                                   out_of_range=2),
        "street_b.mp4": spark_clip(hits=3, truth=5, out_of_range=1),
    })
    metrics, extras = collect_metrics()
    assert metrics["spark_entrance_recall"] == pytest.approx(0.7)  # (4 + 3) / (5 + 5)
    assert extras["sample_sizes"]["spark_entrance_recall"]["scored"] == 10
    assert extras["spark_chunk_errors"] == 1
    assert extras["spark_out_of_range"] == 3


def test_korea_photo_count_and_km_come_from_coverage_and_frames(env):
    out, clips = env
    write(out, "coverage.json", {"since": "2024", "areas": {}, "kr_best": {
        "area": "seoul_myeongdong", "recent_on_foot_flat": 444, "recent_flat": 444, "capped": False}})
    write(out, "spark_entrances.json", {"street_a.mp4": spark_clip(), "street_b.mp4": spark_clip()})
    write_calls(out, [{"kind": "spark", "usage": {"input_tokens": 1000, "output_tokens": 100}, "seconds": 1.0}])
    for stem, km in (("street_a", 0.5), ("street_b", 0.25)):
        (clips / stem).mkdir()
        (clips / stem / "frames.json").write_text(json.dumps({"km": km, "frames": []}), encoding="utf-8")
    metrics, _ = collect_metrics()
    assert metrics["kr_recent_pedestrian_photos"] == 444
    assert metrics["km_covered"] == pytest.approx(0.75)
    assert metrics["cost_per_km_usd"] == pytest.approx((1000 * 1.25e-6 + 100 * 4.25e-6) / 0.75)


def test_cost_per_100_photos_counts_chosen_stair_phrase_and_all_tactile_phrases(env):
    out, _ = env
    write(out, "spark_entrances.json", {"street_a.mp4": spark_clip(duration=100), "street_b.mp4": spark_clip(duration=100)})
    write(out, "sam_stairs.json", stairs_file(best="steps"))
    write(out, "sidewalk.json", {
        "street_a.mp4": sidewalk_clip(0.9, 10, best="tactile paving"),
        "street_b.mp4": sidewalk_clip(0.9, 10, best="braille block"),
    })
    lines = [
        {"kind": "spark", "usage": {"input_tokens": 1_000_000, "output_tokens": 100_000}, "seconds": 10.0},
        {"kind": "spark", "usage": None, "seconds": 20.0},
        {"kind": "upload", "bytes": 10, "seconds": 5.0},
        {"kind": "delete_failed", "file_id": "f"},  # no seconds key
        {"kind": "sam", "concept": "stairs", "billed_frames": 50, "requested_frames": 5, "seconds": 1.0},
        {"kind": "sam", "concept": "steps", "billed_frames": 100, "requested_frames": 100, "seconds": 1.0},
        {"kind": "sam", "concept": "tactile paving", "billed_frames": 100, "requested_frames": 100, "seconds": 1.0},
        {"kind": "sam", "concept": "yellow tactile paving", "billed_frames": 100, "requested_frames": 100,
         "seconds": 1.0},
        {"kind": "sam", "concept": "braille block", "billed_frames": 100, "requested_frames": 100, "seconds": 1.0},
        {"kind": "sam", "concept": "electric scooter", "billed_frames": 60, "requested_frames": 60, "seconds": 1.0},
    ]
    write_calls(out, lines)
    metrics, _ = collect_metrics()
    spark_cost = 1_000_000 * 1.25e-6 + 100_000 * 4.25e-6
    # steps (chosen stair phrase), all three tactile phrases (the metric is their union), obstacle label;
    # not "stairs", an explored stair phrase that lost
    production_sam = (100 + 100 + 100 + 100 + 60) * 0.0002
    all_sam = (50 + 100 + 100 + 100 + 100 + 60) * 0.0002
    assert metrics["cost_per_100_photos_usd"] == pytest.approx((spark_cost + production_sam) / 200 * 100)
    assert metrics["spike_total_cost_usd"] == pytest.approx(spark_cost + all_sam)
    assert metrics["seconds_per_100_photos"] == pytest.approx(sum(l.get("seconds", 0) for l in lines) / 200 * 100)


def test_failed_attempts_add_upper_bound_cost_to_spike_total_only(env):
    out, _ = env
    write(out, "spark_entrances.json", {"street_a.mp4": spark_clip(duration=100)})
    write_calls(out, [
        {"kind": "sam", "concept": "electric scooter", "billed_frames": 60, "requested_frames": 60, "seconds": 4.0},
        {"kind": "sam_failed", "concept": "electric scooter", "billed_frames": 60, "requested_frames": 60,
         "seconds": 3.0, "error": "SamStreamError: SAM stream failed"},
        {"kind": "spark_failed", "schema": "street_scan", "seconds": 1.0, "error": "APIConnectionError: x"},
        {"kind": "run_aborted", "consecutive_failures": 5, "error": "x"},
    ])
    metrics, extras = collect_metrics()
    assert metrics["spike_total_cost_usd"] == pytest.approx((60 + 60) * 0.0002)
    assert metrics["cost_per_100_photos_usd"] == pytest.approx(60 * 0.0002)  # production estimate: successes only
    assert metrics["seconds_per_100_photos"] == pytest.approx(4.0)  # failed seconds are inside the success line
    assert extras["failed_attempts"] == {"sam": 1, "spark": 1, "cost_usd": pytest.approx(60 * 0.0002),
                                         "seconds": 4.0, "run_aborts": 1}
    report = build_report(metrics, extras)
    assert "Failed API attempts (possibly billed): 2 (SAM 1, Spark 1, 4 s)" in report
    assert "runs stopped by the circuit breaker: 1" in report


# --- report ----------------------------------------------------------------------------------

def full_dataset(out):
    write(out, "coverage.json", {"kr_best": {"area": "seoul_myeongdong", "recent_on_foot_flat": 444}})
    write(out, "spark_entrances.json", {
        "street_a.mp4": spark_clip(hits=4, truth=5, errors=[{"start": 60.0}], out_of_range=2),
        "street_b.mp4": spark_clip(hits=3, truth=5, out_of_range=1),
    })
    write(out, "sam_stairs.json", stairs_file(best="steps"))
    write(out, "step_count.json", steps_file(accuracy=1.0, scored=2, failed=0))  # insufficient
    write(out, "sidewalk.json", {"street_a.mp4": sidewalk_clip(0.8, 10, failed=1, events=[
        event(True, True), event(None, False), event(True, True), event(False, True)])})
    write_calls(out, [{"kind": "spark", "usage": {"input_tokens": 1000, "output_tokens": 100}, "seconds": 2.0},
                      {"kind": "sam", "concept": "steps", "billed_frames": 50, "requested_frames": 5,
                       "seconds": 1.0}])


def test_report_shows_sample_sizes_insufficient_rows_and_counters(env):
    out, _ = env
    full_dataset(out)
    metrics, extras = collect_metrics()
    report = build_report(metrics, extras)
    assert "| Criterion | Value | Threshold | Result | Sample size |" in report
    assert "| spark_entrance_recall | 0.700 | 0.8 | FAIL (incomplete: 1 chunk errors) | 10 scored |" in report
    assert "| sam_stairs_rate | 0.800 | 0.7 | PASS | 5 scored / 1 failed |" in report
    assert "| tactile_coverage | 0.800 | 0.8 | PASS | 10 scored / 1 failed |" in report
    assert "INSUFFICIENT DATA (n=2)" in report
    assert "step_count_accuracy" in report.split("INSUFFICIENT DATA (n=2)")[0].splitlines()[-1]
    assert "Spark chunk errors: 1" in report
    assert "outside their chunk: 3" in report
    assert "Chosen stair phrase: steps" in report
    assert "street_a.mp4: tactile paving" in report
    assert ("D1 finding: Muse Spark samples uploaded video once every 2 s; "
            "Spark clips hold each photo 2 s (config.SPARK_HOLD)") in report
    assert "Demo area: **Korea**" in report
    assert "## Notes" in report
    assert report.index("Decision: ") < report.index("| Criterion")


def test_main_writes_report_to_out_dir(env, capsys):
    out, _ = env
    full_dataset(out)
    gate_report.main()
    written = (out / "gate_report.md").read_text(encoding="utf-8")
    assert written.startswith("# D1 Gate Report")
    assert written.strip() == capsys.readouterr().out.strip()


# --- truth.json consistency across outputs ----------------------------------------------------

def write_truth_file(clips, content=b'{"clips": {}}'):
    (clips / "truth.json").write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def test_meta_keys_are_skipped_when_iterating_clips(env):
    out, clips = env
    h = write_truth_file(clips)
    write(out, "spark_entrances.json", {"_meta": {"truth_sha256": h}, "street_a.mp4": spark_clip(hits=4, truth=5)})
    write(out, "sidewalk.json", {"_meta": {"truth_sha256": h},
                                 "street_a.mp4": sidewalk_clip(0.9, 10, events=[event(True, True)] * 3)})
    metrics, extras = collect_metrics()
    assert metrics["spark_entrance_recall"] == pytest.approx(0.8)
    assert metrics["tactile_coverage"] == pytest.approx(0.9)
    assert extras["tactile_phrases"] == {"street_a.mp4": "tactile paving"}
    assert extras["truth_warning"] is None


def test_matching_truth_hashes_give_no_warning(env):
    out, clips = env
    h = write_truth_file(clips)
    write(out, "spark_entrances.json", {"_meta": {"truth_sha256": h}})
    write(out, "sam_stairs.json", {**stairs_file(), "truth_sha256": h})
    write(out, "step_count.json", {**steps_file(), "truth_sha256": h})
    write(out, "sidewalk.json", {"_meta": {"truth_sha256": h}})
    metrics, extras = collect_metrics()
    assert extras["truth_warning"] is None
    assert "WARNING" not in build_report(metrics, extras)


def test_outputs_from_different_truth_versions_are_flagged_at_the_top(env):
    out, clips = env
    h = write_truth_file(clips)
    write(out, "sam_stairs.json", {**stairs_file(), "truth_sha256": h})
    write(out, "step_count.json", {**steps_file(), "truth_sha256": "f" * 64})  # made from an older truth.json
    metrics, extras = collect_metrics()
    warning = extras["truth_warning"]
    assert warning.startswith("WARNING: results come from different truth.json versions")
    assert "step_count.json ffffffffffff" in warning and f"current truth.json {h[:12]}" in warning
    report = build_report(metrics, extras)
    assert report.index(warning) < report.index("Decision: ")


def test_output_differing_from_current_truth_is_flagged(env):
    out, clips = env
    write_truth_file(clips, b'{"clips": {"edited": {}}}')
    write(out, "sam_stairs.json", {**stairs_file(), "truth_sha256": "a" * 64})
    _, extras = collect_metrics()
    assert extras["truth_warning"] is not None


def test_output_without_a_recorded_hash_is_flagged(env):
    out, clips = env
    write_truth_file(clips)
    write(out, "sam_stairs.json", stairs_file())  # written before outputs recorded the truth hash
    _, extras = collect_metrics()
    assert "sam_stairs.json (no hash recorded)" in extras["truth_warning"]


# --- spec section 20 decision: core metrics decide, secondary metrics trigger fallbacks ----------

def test_secondary_fails_do_not_demote_a_layer():
    criteria = evaluate({"sam_stairs_rate": 0.8, "step_count_accuracy": 0.4, "spark_entrance_recall": 0.5,
                         "tactile_coverage": 0.9, "obstacle_detection_rate": 0.3})
    assert decide(criteria) == "GO: both layers"
    actions = fallbacks(criteria, {})
    assert actions == [
        "spark_entrance_recall: Spark reads only; entrance candidates come from SAM `door` detection",
        "step_count_accuracy: height estimate simplified to standard riser height × step count; "
        "uncertain counts shown as ⚪",
        "obstacle_detection_rate: reduce obstacle types to the ones SAM detects reliably",
    ]


def test_core_fail_demotes_and_lists_the_box_prompt_fallback():
    criteria = evaluate({"sam_stairs_rate": 0.5, "tactile_coverage": 0.9})
    assert decide(criteria) == "GO: sidewalk layer as main, entrance layer reduced"
    assert fallbacks(criteria, {}) == [
        "sam_stairs_rate: switch to box prompts (Spark boxes → SAM) and re-test before demoting"]


def test_insufficient_core_metric_leaves_the_layer_untested_not_reduced():
    sidewalk_only = evaluate({"tactile_coverage": 0.9, "obstacle_detection_rate": 0.8, "step_count_accuracy": 0.9})
    decision = decide(sidewalk_only)
    assert decision == "GO: sidewalk layer as main, entrance layer untested (insufficient data)"
    assert "reduced" not in decision
    entrance_only = evaluate({"sam_stairs_rate": 0.9})
    assert decide(entrance_only) == "GO: entrance layer as main, sidewalk layer untested (insufficient data)"
    assert fallbacks(sidewalk_only, {"sam_stairs_rate": 2}) == [
        "sam_stairs_rate: entrance layer untested (insufficient data), n=2; "
        "label more samples and re-run before deciding"]


def test_core_fail_with_the_other_layer_untested_is_no_go():
    decision = decide(evaluate({"sam_stairs_rate": 0.1}))
    assert decision.startswith("NO-GO: entrance layer failed, sidewalk layer untested (insufficient data)")


def test_report_lists_fallbacks_or_none(env):
    out, _ = env
    full_dataset(out)  # obstacle rate 2/3 fails, spark recall 0.7 fails
    report = build_report(*collect_metrics())
    section = report.split("## Fallbacks (spec section 20)")[1].split("\n\n")[1]
    assert section.splitlines() == [
        "- spark_entrance_recall: Spark reads only; entrance candidates come from SAM `door` detection",
        "- obstacle_detection_rate: reduce obstacle types to the ones SAM detects reliably",
    ]
    assert fallbacks(evaluate({"sam_stairs_rate": 0.9, "tactile_coverage": 0.9}), {}) == []


# --- report extras (not gate criteria) --------------------------------------------------------

def test_extra_measurements_from_saved_outputs(env):
    out, _ = env
    write(out, "step_count.json", {"rows": [
        {"truth_steps": 0, "correct": True},  # level entrance: left out
        {"truth_steps": 1, "correct": True},
        {"truth_steps": 2, "correct": False},
        {"truth_steps": 3, "correct": None, "error": "RuntimeError: x"},  # error row: left out
    ], "accuracy": 0.5, "scored": 3, "failed": 1})
    write(out, "sam_stairs.json", stairs_file(best="steps", rows=[
        {"step_count": 1, "phrases": {"steps": {"detected": True}}},  # one step: left out
        {"step_count": 2, "phrases": {"steps": {"detected": True}, "stairs": {"detected": False}}},
        {"step_count": 3, "phrases": {"steps": {"detected": False}}},
        {"step_count": 2, "phrases": {"steps": {"error": "RuntimeError: x"}}},  # errored phrase: left out
        {"step_count": 4, "phrases": {}, "error": "RuntimeError: cut"},  # errored row: left out
    ]))
    write(out, "spark_entrances.json", {"street_b.mp4": {**spark_clip(), "obstacle_types": ["bicycle"]}})
    scooter = {**event(False, True), "label": "Electric Scooter"}  # Spark did not list it: left out
    write(out, "sidewalk.json", {"street_b.mp4": sidewalk_clip(0.9, 10, events=[
        event(True, True), {**event(False, True), "label": " Bicycle "}, event(None, False), scooter])})

    metrics, extras = collect_metrics()
    m = extras["extra_measurements"]
    assert m["step_count_accuracy_stepped"] == {"value": 0.5, "n": 2}
    assert m["stairs_rate_2plus_steps"] == {"value": 0.5, "n": 2}
    assert m["obstacle_rate_spark_types"] == {"value": 0.5, "n": 2}  # the two scored bicycle events only
    report = build_report(metrics, extras)
    assert "- Step-count accuracy on stepped entrances (truth step_count >= 1): 0.500 (n=2)" in report
    assert "- Stair detection rate on entrances with 2+ steps (phrase steps): 0.500 (n=2)" in report
    assert "- Obstacle detection rate on events whose label Spark also listed for the clip: 0.500 (n=2)" in report


# --- report hygiene -------------------------------------------------------------------------------

def test_pooled_tactile_coverage_of_exactly_point_eight_passes(env):
    out, _ = env
    write(out, "sidewalk.json", {"street_a.mp4": sidewalk_clip(1 / 1, 1), "street_b.mp4": sidewalk_clip(31 / 39, 39)})
    metrics, _ = collect_metrics()
    assert metrics["tactile_coverage"] == 0.8  # 32 / 40, not 0.7999999999999999
    (criterion,) = [c for c in evaluate(metrics) if c.name == "tactile_coverage"]
    assert criterion.passed


def test_truncated_last_calls_line_is_skipped_and_counted(env):
    out, _ = env
    write(out, "spark_entrances.json", {"street_a.mp4": spark_clip(duration=100)})
    good = {"kind": "sam", "concept": "electric scooter", "billed_frames": 50, "seconds": 1.0}
    (out / "calls.jsonl").write_text(json.dumps(good) + "\n[1, 2]\n" + '{"kind": "sam", "conc', encoding="utf-8")
    metrics, extras = collect_metrics()
    assert extras["calls_read"] == 1 and extras["calls_skipped_lines"] == 2
    assert metrics["spike_total_cost_usd"] == pytest.approx(50 * 0.0002)
    assert "calls.jsonl: 1 entries read, 2 unreadable lines skipped" in build_report(metrics, extras)


def test_demo_area_unknown_when_no_korean_area_was_summarized(env):
    out, _ = env
    write(out, "coverage.json", {"since": "2020-01-01", "areas": {}, "kr_best": None})
    report = build_report(*collect_metrics())
    assert "Demo area: **unknown (no Korean area summarized)**" in report
    assert "| kr_recent_pedestrian_photos | n/a | 300 | INSUFFICIENT DATA (n=0) |" in report


def test_korea_count_is_an_integer_with_its_capped_flag(env):
    out, _ = env
    write(out, "coverage.json", {"kr_best": {"area": "seoul_myeongdong", "recent_on_foot_flat": 444,
                                             "recent_flat": 500, "capped": True}})
    report = build_report(*collect_metrics())
    assert "| kr_recent_pedestrian_photos | 444 | 300 | PASS | seoul_myeongdong, capped: a lower bound |" in report


def test_missing_cost_and_time_are_insufficient_rows_and_km_is_not_printed_as_zero(env):
    out, _ = env
    write(out, "sam_stairs.json", stairs_file())
    report = build_report(*collect_metrics())
    assert "| cost_per_100_photos_usd | n/a | 0.6 | INSUFFICIENT DATA (n=0) | - |" in report
    assert "| seconds_per_100_photos | n/a | 300.0 | INSUFFICIENT DATA (n=0) | - |" in report
    assert "Kilometers of street in the test clips: unknown" in report
    assert "per km: $0.00" not in report and "test clips: 0.00" not in report
    assert "Spike total cost including phrase exploration: unknown" in report


def test_spark_recall_is_not_marked_incomplete_without_chunk_errors(env):
    out, _ = env
    write(out, "spark_entrances.json", {"street_a.mp4": spark_clip(hits=4, truth=5)})
    report = build_report(*collect_metrics())
    assert "| spark_entrance_recall | 0.800 | 0.8 | PASS | 5 scored |" in report


def test_d1_note_uses_the_configured_hold(env, monkeypatch):
    monkeypatch.setattr(config, "SPARK_HOLD", 3)
    report = build_report(*collect_metrics())
    assert "Spark clips hold each photo 3 s (config.SPARK_HOLD)" in report
