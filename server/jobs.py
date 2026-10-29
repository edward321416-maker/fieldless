"""One scan at a time on a background thread. Spend is capped per scan and per day (spec 13), so scans never run
side by side. Queued scans are picked up when the worker starts. A scan a restart left running is marked failed, not
resumed, because resuming would pay again for the stage that was cut off. Its spend so far is already saved.

Item runs (plan 12: a survey item the agent writes and runs on a finished scan's photos) share the queue, so a run
and a scan never spend side by side either. A run waits for a person's answers in "labels_needed", off the queue;
their answers put it back on."""
import importlib
import logging
import queue
import threading
from dataclasses import asdict
from pathlib import Path

from assist import report
from pipeline import db, item_runs, second_opinion
from pipeline.cost import CostCapReached
from pipeline.meta_api import MetaApi, error_text
from pipeline.runner import SAVE_EVERY_USD, Scan, meter_for, refuse_in_worker, run_scan, spend_saver  # noqa: F401

log = logging.getLogger(__name__)


class ItemRunStopped(Exception):
    """An item run cannot go on; the message is the one sentence the screen shows."""

class ScanWorker:
    def __init__(self, db_path: Path, run_dir: Path, run=None, on_done=None, public_cap_usd: float | None = None,
                 glimmer_daily_usd: float = second_opinion.GLIMMER_DAILY_USD, glimmer_total_usd: float | None = None,
                 items=None):
        self.db_path = Path(db_path)
        self.run_dir = Path(run_dir)
        self.public_cap_usd = public_cap_usd  # Settings.public_cost_cap_usd
        self.glimmer_daily_usd, self.glimmer_total_usd = glimmer_daily_usd, glimmer_total_usd  # Settings.glimmer_*
        self._run = run or self._run_pipeline
        self._on_done = on_done  # called with the scan id after every scan or item run, finished or crashed
        self._items = items  # the agent's item writer (assist.custom_item, loaded when a run first needs it)
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
        conn = db.connect(self.db_path)
        try:
            for r in db.item_runs(conn):
                if r["status"] == "running":  # the same rule as a scan: re-running would pay again
                    db.update_item_run(conn, r["id"], status="failed",
                                       error="interrupted by a server restart; make the item again")
                elif r["status"] == "queued":
                    self._queue.put(("item", r["id"]))
        finally:
            conn.close()
        self._thread = threading.Thread(target=self._loop, name="scan-worker", daemon=True)
        self._thread.start()

    def submit(self, scan_id: int) -> None:
        self._queue.put(scan_id)

    def submit_item(self, run_id: int) -> None:
        self._queue.put(("item", run_id))

    def join(self) -> None:
        """Wait until every submitted scan has finished (tests)."""
        self._queue.join()

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            scan_id = None if isinstance(job, tuple) else job  # an item run's scan id comes from _run_item
            try:
                if job is None:
                    return
                if isinstance(job, tuple):
                    scan_id = self._run_item(job[1])
                else:
                    self._run(job)
            except BaseException as exc:  # RunAborted is a BaseException; one broken scan must not stop the worker
                log.exception("job %s crashed", job)
                if isinstance(job, tuple):
                    self._item_failed(job[1], error_text(exc))
                else:
                    self._mark_failed(job, exc)
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

    def _item_failed(self, run_id: int, reason: str) -> None:
        try:
            conn = db.connect(self.db_path)
            try:
                if db.item_run(conn, run_id)["status"] in db.ITEM_RUN_ACTIVE:
                    db.update_item_run(conn, run_id, status="failed", error=reason)
            finally:
                conn.close()
        except Exception:
            log.exception("could not mark item run %s as failed", run_id)

    def _run_item(self, run_id: int) -> int | None:
        """One step of an item run: write and test the item (assist.custom_item.start, or .resume with a person's
        answers), then run it on the scan's photos. Returns the run's scan id."""
        refuse_in_worker()
        conn = db.connect(self.db_path)
        lock = threading.Lock()  # progress may come from the agent's request threads

        def progress(line: str) -> None:
            with lock:
                db.add_progress(conn, run_id, line)

        try:
            run = db.item_run(conn, run_id)
            if run["status"] != "queued":
                return run["scan_id"]
            db.update_item_run(conn, run_id, status="running", error=None)
            custom = self._items or importlib.import_module("assist.custom_item")
            meter = item_runs.meter_for_item(conn, run, public_cap_usd=self.public_cap_usd)
            meter.on_spend = spend_saver(self.db_path, run_id, run["cost_usd"], update=db.update_item_run)
            api = MetaApi(self.run_dir / "calls.jsonl", meter)
            try:
                photos = item_runs.scan_photos(conn, run["scan_id"])
                if not photos.paths:
                    raise ItemRunStopped("The survey's photos could not be downloaded from Mapillary")
                if run["labels"] is None:
                    result = custom.start(api, photos, run["target"], progress)
                else:
                    labels = {int(idx): answer for idx, answer in run["labels"].items()}
                    result = custom.resume(api, photos, run["state"], labels, progress)
                if result["status"] == "labels_needed":
                    db.update_item_run(conn, run_id, status="labels_needed", label_photos=result["photos"],
                                       state=result["state"], cost_usd=meter.spent_usd)
                elif result["status"] == "ready":
                    check, test = result["check"], dict(result.get("test") or {})
                    db.update_item_run(conn, run_id, check_def=asdict(check), test=test)
                    rows = item_runs.run_item_on_scan(api, photos, check, self.run_dir, run_id, on_progress=progress)
                    city = custom.city_check(photos, check, rows)
                    if city is not None:
                        test["city"] = city
                    db.update_item_run(conn, run_id, status="done", rows=rows, test=test, cost_usd=meter.spent_usd)
                else:
                    db.update_item_run(conn, run_id, status="failed", error=result.get("reason") or "failed",
                                       test=result.get("test"), cost_usd=meter.spent_usd)
            except CostCapReached:
                db.update_item_run(conn, run_id, status="failed", cost_usd=meter.spent_usd,
                                   error=f"The run stopped at its spending limit (${meter.cap_usd:.2f})")
            except ItemRunStopped as stop:
                db.update_item_run(conn, run_id, status="failed", error=str(stop), cost_usd=meter.spent_usd)
            except Exception as exc:
                log.exception("item run %s failed", run_id)
                db.update_item_run(conn, run_id, status="failed", error=error_text(exc), cost_usd=meter.spent_usd)
            return run["scan_id"]
        finally:
            conn.close()

    def judge(self):
        """Muse Glimmer for checks that name it (GLIMMER_URL set), unless today's GPU estimate has reached the daily
        cap (then Muse Spark judges every check until tomorrow, UTC) or the whole log's has reached the total cap
        (then Muse Spark judges from now on)."""
        model = second_opinion.glimmer_from_env(self.run_dir)
        if model is None:
            return None
        if second_opinion.gpu_usd_today(model.log_path) >= self.glimmer_daily_usd:
            log.info("today's Glimmer GPU estimate is over $%.2f; Muse Spark judges every check",
                     self.glimmer_daily_usd)
            return None
        if self.glimmer_total_usd is not None and second_opinion.gpu_usd_total(model.log_path) >= self.glimmer_total_usd:
            log.info("the Glimmer GPU estimate is over its total of $%.2f; Muse Spark judges every check",
                     self.glimmer_total_usd)
            return None
        return model

    def _write_report_text(self, conn, scan_id: int, api) -> None:
        """The report's sentences once a scan is done (assist.report.make_text), charged to the scan through api's
        meter. A failure only logs: the report then uses code's own sentences."""
        try:
            report.make_text(conn, self.run_dir, scan_id, api)
        except Exception:
            log.exception("report text failed for scan %s", scan_id)

    def _run_pipeline(self, scan_id: int) -> None:
        conn = db.connect(self.db_path)
        try:
            scan = db.get_scan(conn, scan_id)
            meter = meter_for(conn, scan, public_cap_usd=self.public_cap_usd)
            meter.on_spend = spend_saver(self.db_path, scan_id, scan["cost_usd"])
            api = MetaApi(self.run_dir / "calls.jsonl", meter)
            run_scan(Scan(conn, scan_id, api, self.run_dir, scan["bbox"],
                          second_opinion=second_opinion.from_env(self.run_dir), judge=self.judge()))
            if db.get_scan(conn, scan_id)["status"] == "done":
                self._write_report_text(conn, scan_id, api)
        finally:
            conn.close()
