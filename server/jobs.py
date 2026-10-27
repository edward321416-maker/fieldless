"""One scan at a time on a background thread. Spend is capped per scan and per day (spec 13), so scans never run
side by side. Scans a restart left queued or running are picked up when the worker starts; the runner resumes them
at the stage that stopped."""
import logging
import queue
import threading
from pathlib import Path

from pipeline import db
from pipeline.meta_api import MetaApi, error_text
from pipeline.runner import Scan, meter_for, run_scan

log = logging.getLogger(__name__)


class ScanWorker:
    def __init__(self, db_path: Path, run_dir: Path, run=None):
        self.db_path = Path(db_path)
        self.run_dir = Path(run_dir)
        self._run = run or self._run_pipeline
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # one worker thread only: scans never run side by side
        conn = db.connect(self.db_path)
        try:
            waiting = [s["id"] for s in db.list_scans(conn, limit=1000) if s["status"] in ("queued", "running")]
        finally:
            conn.close()
        for scan_id in reversed(waiting):  # list_scans is newest first
            self._queue.put(scan_id)
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
            except Exception as exc:  # one broken scan must not stop the worker
                log.exception("scan %s crashed", scan_id)
                self._mark_failed(scan_id, exc)
            finally:
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
            api = MetaApi(self.run_dir / "calls.jsonl", meter_for(conn, scan))
            run_scan(Scan(conn, scan_id, api, self.run_dir, scan["bbox"]))
        finally:
            conn.close()
