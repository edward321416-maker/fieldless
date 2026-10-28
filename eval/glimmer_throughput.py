"""Stage 2 (docs/specs/2026-10-28-glimmer-modal-design.md, section 3): with the GPU warm, how many inventory checks a
second does Glimmer answer, and what does one cost in GPU time? Photos and prompts are the scan's own inventory picks,
cycled to the number of calls. Spark's figure beside it ($0.012 a check) is the average measured on the Seattle scans
(docs/metrics.md).

    GLIMMER_URL=... python -m eval.glimmer_throughput --run d5-seattle-rec --scan 4 --calls 300 --concurrency 16 32
"""
import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from eval.glimmer_ledger import H100_USD_PER_S

SPARK_USD_PER_CALL = 0.012


def per_call_usd(calls: int, wall_s: float, usd_per_s: float = H100_USD_PER_S) -> float:
    return wall_s * usd_per_s / calls


def summary(latencies: list[float], wall_s: float, calls: int, concurrency: int) -> dict:
    ordered = sorted(latencies)
    return {"concurrency": concurrency, "calls": calls, "wall_s": round(wall_s, 1),
            "calls_per_s": round(calls / wall_s, 2), "p50_s": round(statistics.median(ordered), 2),
            "p90_s": round(ordered[int(0.9 * (len(ordered) - 1))], 2),
            "usd_per_call": per_call_usd(calls, wall_s), "spark_usd_per_call": SPARK_USD_PER_CALL}


def main(argv=None) -> None:
    from eval.glimmer_ledger import LEDGER, append, check_budget
    from eval.glimmer_replay import LAYERS, picks
    from eval.store import load_scan, open_ro
    from pipeline import collect, config
    from pipeline.second_opinion import glimmer_from_env
    from pipeline.survey_items import load

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", default="d5-seattle-rec")
    ap.add_argument("--scan", type=int, default=4)
    ap.add_argument("--calls", type=int, default=300)
    ap.add_argument("--concurrency", type=int, nargs="+", default=[16, 32])
    args = ap.parse_args(argv)
    run_dir = config.run_dir(args.run)
    judge = glimmer_from_env(run_dir)
    if judge is None:
        raise SystemExit("set GLIMMER_URL to the deployed fieldless-glimmer address")
    check_budget(LEDGER, planned_usd=0.8)
    conn = open_ro(run_dir / "threshold.db")
    try:
        data = load_scan(conn, args.scan)
    finally:
        conn.close()
    unavailable = json.loads((run_dir / "work" / f"scan{args.scan}" / "checks_unavailable.json")
                             .read_text(encoding="utf-8"))
    frames, standard = {f["idx"]: f for f in data["frames"]}, load("us")
    jobs = [(standard.check(item), collect.photo_path(frames[p["idx"]]["image_id"]))
            for item in LAYERS for p in picks(data, unavailable, item)]
    started = time.time()
    judge.answer(*jobs[0])  # waits out the cold start; not part of the timed runs
    cold = time.time() - started
    results, busy = [], 0.0
    for c in args.concurrency:
        work = [jobs[i % len(jobs)] for i in range(args.calls)]

        def one(job):
            t = time.time()
            judge.answer(*job)
            return time.time() - t

        t0 = time.time()
        with ThreadPoolExecutor(c) as pool:
            lat = list(pool.map(one, work))
        wall = time.time() - t0
        busy += wall
        results.append(summary(lat, wall, args.calls, c))
        print(results[-1])
    entry = append(LEDGER, f"throughput {args.calls}x{args.concurrency}", busy_s=busy, cold_start_s=cold)
    out = run_dir / f"glimmer_throughput_scan{args.scan}.json"
    out.write_text(json.dumps({"cold_s": round(cold, 1), "runs": results, "ledger": entry}, indent=1),
                   encoding="utf-8")
    print("cold start", round(cold, 1), "s; ledger", entry)


if __name__ == "__main__":
    main()
