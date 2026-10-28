"""A running estimate of what Glimmer's GPU costs, one line per measurement run, so development stops before it eats
the Modal credit the live site also runs on (one $100 grant for the workspace). Modal bills a container from the
moment it starts until it scales down, so a run costs its cold start, the time it was busy, and the idle window after
the last request. The Modal dashboard is the real figure; this is the guard."""
import json
import time
from pathlib import Path

H100_USD_PER_S = 0.001097  # modal.com/pricing, H100, checked 2026-10-28
SCALEDOWN_S = 120  # deploy/modal_glimmer.py scaledown_window
DEV_CAP_USD = 30.0  # of the user's $40 for Glimmer; the rest is for the live site


def _data_dir() -> Path:
    """meta hackathon/data, found by walking up (the repo sits in threshold/ or wt/<name>/)."""
    here = Path(__file__).resolve()
    for folder in here.parents:
        if (folder / "data" / "seattle").is_dir():
            return folder / "data"
    return here.parents[1] / "data"


LEDGER = _data_dir() / "glimmer_gpu_ledger.jsonl"


def estimate_usd(busy_s: float, cold_start_s: float = 0.0) -> float:
    return (busy_s + cold_start_s + SCALEDOWN_S) * H100_USD_PER_S


def append(path: Path, what: str, busy_s: float, cold_start_s: float = 0.0) -> dict:
    row = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "what": what, "busy_s": round(busy_s, 1),
           "cold_start_s": round(cold_start_s, 1), "usd_est": round(estimate_usd(busy_s, cold_start_s), 4)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def total_usd(path: Path) -> float:
    path = Path(path)
    if not path.exists():
        return 0.0
    return sum(json.loads(line)["usd_est"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def check_budget(path: Path, planned_usd: float = 0.0) -> None:
    spent = total_usd(path)
    if spent + planned_usd > DEV_CAP_USD:
        raise SystemExit(f"Glimmer GPU estimate ${spent:.2f} + ${planned_usd:.2f} would pass the ${DEV_CAP_USD:.0f} "
                         "development cap; ask the user first")
