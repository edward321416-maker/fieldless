"""Spend of one run against its cap (spec section 13), and the price of each kind of request."""
import threading

from pipeline import config


class CostCapReached(RuntimeError):
    """The next request would take the run past its cap, so the run stops before paying for it."""


class CostMeter:
    """reserve() before a request and settle() after it, so parallel requests cannot pass the cap together.
    on_spend, if set, is called with the running total after each settle."""

    def __init__(self, cap_usd: float, spent_usd: float = 0.0, on_spend=None):
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd
        self.on_spend = on_spend
        self.reserved_usd = 0.0
        self._lock = threading.Lock()

    def reserve(self, usd: float) -> None:
        with self._lock:
            if self.spent_usd + self.reserved_usd + usd > self.cap_usd + 1e-9:
                raise CostCapReached(f"cost cap ${self.cap_usd:.2f}: spent ${self.spent_usd:.4f}, "
                                     f"reserved ${self.reserved_usd:.4f}, next ${usd:.4f}")
            self.reserved_usd += usd

    def settle(self, reserved_usd: float, actual_usd: float) -> None:
        with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - reserved_usd)
            self.spent_usd += actual_usd
            spent = self.spent_usd
        if self.on_spend:
            self.on_spend(spent)


def spark_cost(usage: dict | None) -> float:
    if not usage:
        return 0.0
    return (usage.get("input_tokens", 0) * config.SPARK_IN_PER_TOKEN
            + usage.get("output_tokens", 0) * config.SPARK_OUT_PER_TOKEN)


def sam_video_cost(frames: int) -> float:
    return max(config.SAM_MIN_FRAMES, frames) * config.SAM_PER_FRAME


def sam_image_cost(images: int = 1) -> float:
    return images * config.SAM_PER_IMAGE
