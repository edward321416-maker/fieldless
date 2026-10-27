"""Independent API requests on a thread pool. D1: a SAM image request takes about 3 s, so a 400-photo scan needs
parallel requests to stay under 5 minutes per 100 photos (spec section 20)."""
from concurrent.futures import ThreadPoolExecutor


def run_parallel(fn, items, workers: int) -> list:
    """fn(item) for every item, results in item order. The first exception that escapes fn cancels the items not
    yet started and is raised. Callers catch per-item errors inside fn and let only run-stopping ones escape
    (CostCapReached, RunAborted)."""
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, item) for item in items]
        try:
            return [f.result() for f in futures]
        except BaseException:
            for f in futures:
                f.cancel()
            raise
