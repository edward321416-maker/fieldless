"""One scan at a time on a background thread. Spend is capped per scan and per day (spec 13), so scans never run
side by side. Queued scans are picked up when the worker starts. A scan a restart left running is marked failed, not
resumed, because resuming would pay again for the stage that was cut off. Its spend so far is already saved."""
import logging
import queue
import threading
from pathlib import Path

from pipeline import db, second_opinion
from pipeline.meta_api import MetaApi, error_text
from pipeline.runner import Scan, meter_for, run_scan

log = logging.getLogger(__name__)

SAVE_EVERY_USD = 0.05  # spend is written to the scan this often, so a killed run still counts toward the caps


def spend_saver(db_path: Path, scan_id: int, saved_usd: float):
    """A CostMeter hook that writes the scan's spend to the database each time it has grown by SAVE_EVERY_USD. It
    opens its own connection (settle runs on the pipeline's request threads), never waits and never raises: while one
    save runs, other settles skip, and a later settle carries a higher total."""
    lock = threading.Lock()
    last = [saved_usd]

    def save(spent_usd: float) -> None:
        if not lock.acquire(blocking=False):
            return
        try:
            if spent_usd - last[0] < SAVE_EVERY_USD:
                return
            conn = db.connect(db_path)
            try:
                db.update_scan(conn, scan_id, cost_usd=spent_usd)
            finally:
                conn.close()
            last[0] = spent_usd
        except Exception:
            log.exception("could not save the spend of scan %s", scan_id)
        finally:
            lock.release()

    return save


class ScanWorker:
    def __init__(self, db_path: Path, run_dir: Path, run=None, on_done=None, public_cap_usd: float | None = None):
        self.db_path = Path(db_path)
        self.run_dir = Path(run_dir)
        self.public_cap_usd = public_cap_usd  # Settings.public_cost_cap_usd
        self._run = run or self._run_pipeline
        self._on_done = on_done  # called with the scan id after every scan, finished or crashed
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # one worker thread only: scans never run side by side
        conn = db.connect(self.db_path)
        try:
            scans = list(reversed(db.list_scans(conn, limit=1000)))  # oldest first
            for s in scans:
                if s["status"] == "running":  # cut off by a restart: its spend so far is saved, re-running would pay again
                    for stage, state in s["stage_status"].items():
                        if state == "running":
                            db.set_stage(conn, s["id"], stage, "stopped")
                    db.update_scan(conn, s["id"], status="failed",
                                   error="interrupted by a server restart; start a new survey")
        finally:
            conn.close()
        for s in scans:
            if s["status"] == "queued":
                self._queue.put(s["id"])
        self._thread = threading.Thread(target=self._loop, name="scan-worker", daemon=True)
        self._thread.start()

    def submit(self, scan_id: int) -> None:
        self._queue.put(scan_id)

    def join(self) -> None:
        """Wait until every submitted scan has finished (tests)."""
        self._queue.join()

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while True:
            scan_id = self._queue.get()
            try:
                if scan_id is None:
                    return
                self._run(scan_id)
            except BaseException as exc:  # RunAborted is a BaseException; one broken scan must not stop the worker
                log.exception("scan %s crashed", scan_id)
                self._mark_failed(scan_id, exc)
            finally:
                if scan_id is not None and self._on_done:
                    try:
                        self._on_done(scan_id)
                    except Exception:
                        log.exception("after-scan hook failed for scan %s", scan_id)
                self._queue.task_done()

    def _mark_failed(self, scan_id: int, exc: BaseException) -> None:
        """Record a crash on the scan, unless the scan already finished. Never raises: a failure here is logged, and
        the worker goes on with the next scan."""
        try:
            conn = db.connect(self.db_path)
            try:
                if db.get_scan(conn, scan_id)["status"] in ("queued", "running"):
                    db.update_scan(conn, scan_id, status="failed", error=error_text(exc))
            finally:
                conn.close()
        except Exception:
            log.exception("could not mark scan %s as failed", scan_id)

    def _run_pipeline(self, scan_id: int) -> None:
        conn = db.connect(self.db_path)
        try:
            scan = db.get_scan(conn, scan_id)
            meter = meter_for(conn, scan, public_cap_usd=self.public_cap_usd)
            meter.on_spend = spend_saver(self.db_path, scan_id, scan["cost_usd"])
            api = MetaApi(self.run_dir / "calls.jsonl", meter)
            run_scan(Scan(conn, scan_id, api, self.run_dir, scan["bbox"],
                          second_opinion=second_opinion.from_env(self.run_dir)))
        finally:
            conn.close()
