import pytest

from eval import glimmer_throughput as tp


def test_per_call_cost_is_gpu_seconds_shared_by_the_calls():
    assert tp.per_call_usd(300, 120.0, 0.001097) == pytest.approx(120 * 0.001097 / 300)


def test_summary_reports_rate_and_latency_percentiles():
    s = tp.summary([1.0, 2.0, 3.0, 4.0], wall_s=2.0, calls=4, concurrency=2)
    assert s["calls_per_s"] == 2.0 and s["p50_s"] == 2.5 and s["concurrency"] == 2
    assert s["usd_per_call"] == pytest.approx(2.0 * 0.001097 / 4)
    assert s["spark_usd_per_call"] == 0.012
