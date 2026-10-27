import pytest

from pipeline.cost import CostCapReached, CostMeter, sam_image_cost, sam_video_cost, spark_cost


def test_reserve_counts_pending_requests_against_the_cap():
    meter = CostMeter(cap_usd=1.0)
    meter.reserve(0.6)
    meter.settle(0.6, 0.5)
    meter.reserve(0.5)  # 0.5 spent + 0.5 reserved = exactly the cap
    with pytest.raises(CostCapReached):
        meter.reserve(0.01)


def test_settle_moves_the_reservation_to_spent():
    meter = CostMeter(cap_usd=1.0, spent_usd=0.2)
    meter.reserve(0.3)
    meter.settle(0.3, 0.1)
    assert meter.spent_usd == pytest.approx(0.3)
    assert meter.reserved_usd == 0


def test_prices():
    assert spark_cost({"input_tokens": 1_000_000, "output_tokens": 1_000_000}) == pytest.approx(5.5)
    assert spark_cost(None) == 0
    assert sam_video_cost(5) == pytest.approx(0.01)  # 50-frame minimum
    assert sam_video_cost(60) == pytest.approx(0.012)
    assert sam_image_cost(2) == pytest.approx(0.005)


def test_the_spend_hook_sees_the_running_total():
    seen = []
    meter = CostMeter(cap_usd=1.0, spent_usd=0.1, on_spend=seen.append)
    meter.reserve(0.2)
    meter.settle(0.2, 0.15)
    meter.reserve(0.2)
    meter.settle(0.2, 0.05)
    assert seen == pytest.approx([0.25, 0.30])
