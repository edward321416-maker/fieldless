"""A second opinion (plan 4 Task 3b): Muse Spark Open, the open-weight Muse Spark served on Baseten, answers the
same check on the same photo whenever Muse Spark reported a defect. Agreement is recorded; a disagreement puts the
row first in line for a person ("models_disagree"). Probe (spike/run_open_probe.py, 2026-10-28): JSON-schema answers
on two Seattle ramps, both right, 11-15 s each.

Off unless BASETEN_API_KEY is set (pipeline.config loads it from .env; it is never logged). Baseten is billed to its
own credit, so these calls are not counted against the Meta cost caps; their token use is logged in calls_open.jsonl.

OpenModel is any OpenAI-compatible server that answers a check with the scan's own prompt and JSON schema: Spark Open
on Baseten, or Muse Glimmer served on Modal by deploy/modal_glimmer.py (docs/specs/2026-10-28-glimmer-modal-design.md
2.2). Glimmer's key is derived from MODEL_API_KEY, so it needs no new secret; GLIMMER_URL (not secret) turns it on.
"""
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pipeline import config

from pipeline.meta_api import error_text, image_part
from pipeline.parallel import run_parallel
from pipeline.survey_checks import SCHEMA, prompt
from pipeline.survey_items import Check

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open-1026"
MAX_TOKENS = 4000  # Spark Open reasons before it answers (probe: 700-2000 completion tokens)

GLIMMER_MODEL = "meta-models/Muse-Glimmer-30B"
GLIMMER_SYSTEM = "Reasoning strength: low"  # design 2.2: raised to medium if stage 1 finds too many "unclear"
GLIMMER_MAX_TOKENS = 2000
GLIMMER_TIMEOUT_S = 300  # a cold container takes a few minutes; a request that waits longer falls back to Spark
GPU_USD_PER_HOUR = 3.95  # Modal H100, the one GPU deploy/modal_glimmer.py uses; the Modal dashboard is the real figure
GLIMMER_IDLE_S = 120  # the GPU stays on this long after the last call (modal_glimmer.py scaledown_window)
GLIMMER_DAILY_USD = 3.0  # design 4 risk 3: past this estimate the live server judges with Muse Spark for the day


class OpenModel:
    """answer(check, photo) on an OpenAI-compatible server with the scan's prompt and schema. The key is held, never
    logged or shown; the default is Spark Open on Baseten with BASETEN_API_KEY."""

    def __init__(self, log_path: Path, *, base_url: str = BASE_URL, model: str = MODEL, api_key: str | None = None,
                 max_tokens: int = MAX_TOKENS, system: str | None = None, kind: str = "spark_open",
                 timeout: float = 120, retries: int = 2, client=None):
        self.log_path = Path(log_path)
        self.base_url, self.model, self.max_tokens, self.system, self.kind = base_url, model, max_tokens, system, kind
        self.api_key = api_key
        self.timeout, self.retries = timeout, retries
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key or os.environ["BASETEN_API_KEY"],
                                  timeout=self.timeout, max_retries=self.retries)
        return self._client

    def answer(self, check: Check, photo: Path) -> dict:
        """The check's answer in survey_checks.SCHEMA, plus "model": who answered."""
        started = time.time()
        messages = [{"role": "system", "content": self.system}] if self.system else []
        messages.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": image_part(photo)["image_url"]}},
            {"type": "text", "text": prompt(check)}]})
        resp = self.client.chat.completions.create(
            model=self.model, max_tokens=self.max_tokens, messages=messages,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "survey_check", "schema": SCHEMA, "strict": True}})
        self._log({"kind": self.kind, "model": self.model, "check": check.id, "photo": Path(photo).name,
                   "ts": round(started, 1), "seconds": round(time.time() - started, 1),
                   "usage": resp.usage.model_dump() if resp.usage else {}})
        answer = json.loads(resp.choices[0].message.content)
        return {**answer, "model": self.model} if isinstance(answer, dict) else answer

    def wake(self) -> None:
        """Start the server's container with one light request, on a background thread: the caller never waits and
        a failure is only logged."""
        def ping():
            started = time.time()
            record = {"kind": f"{self.kind}_wake", "model": self.model, "ts": round(started, 1)}
            try:
                self.client.models.list()
            except Exception as exc:
                record["error"] = error_text(exc)
            record["seconds"] = round(time.time() - started, 1)
            try:
                self._log(record)
            except OSError:
                pass

        threading.Thread(target=ping, name=f"{self.kind}-wake", daemon=True).start()

    def _log(self, record: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def from_env(run_dir: Path) -> OpenModel | None:
    """The second opinion when BASETEN_API_KEY is set, else None (the scan runs without it)."""
    return OpenModel(Path(run_dir) / "calls_open.jsonl") if os.environ.get("BASETEN_API_KEY") else None


def glimmer_key() -> str:
    """Muse Glimmer's server key: derived from MODEL_API_KEY (deploy/modal_glimmer.py derives the same), so it is secret
    and needs no new entry in .env."""
    return hmac.new(config.api_key().encode(), b"fieldless-glimmer-key", hashlib.sha256).hexdigest()


def glimmer_from_env(run_dir: Path) -> OpenModel | None:
    """Muse Glimmer on Modal when GLIMMER_URL is set, else None (checks are judged by Muse Spark as before)."""
    url = os.environ.get("GLIMMER_URL")
    if not url:
        return None
    return OpenModel(Path(run_dir) / "calls_glimmer.jsonl", base_url=url.rstrip("/") + "/v1", model=GLIMMER_MODEL,
                     api_key=glimmer_key(), max_tokens=GLIMMER_MAX_TOKENS, system=GLIMMER_SYSTEM, kind="glimmer",
                     timeout=GLIMMER_TIMEOUT_S, retries=0)  # a failure falls back to Spark at once


def apply(model, check: Check, rows: list[dict], photos: dict[int, Path], workers: int) -> list[dict]:
    """Ask the open model on every row whose answer is the check's report_when; record its answer and whether the two
    models agree. Anything but the same answer is a disagreement, so "unclear" (or a photo where the open model sees
    nothing) also puts the row first for a person. A failed request, or a reply of the wrong shape, is recorded on
    that row and changes nothing else."""
    todo = [r for r in rows if r["object_label"] == check.report_when]
    name = getattr(model, "model", MODEL)

    def one(row):
        try:
            answer = model.answer(check, photos[row["frame_idx"]])
            if not isinstance(answer, dict) or answer.get("answer") not in ("yes", "no", "unclear"):
                raise ValueError(f"unexpected answer from {name}: {str(answer)[:200]}")
            return answer
        except Exception as exc:  # this row keeps Spark's answer alone
            return {"error": error_text(exc)}

    for row, answer in zip(todo, run_parallel(one, todo, workers)):
        if "error" in answer:
            row["model_outputs"]["second_opinion"] = {"model": name, "error": answer["error"]}
            continue
        current = answer["answer"] if answer.get("visible", True) else "unclear"
        agree = current == row["object_label"]
        row["model_outputs"]["second_opinion"] = {"model": name, "answer": current, "agree": agree,
                                                  "confidence": answer.get("confidence"),
                                                  "note": answer.get("note"), "box": answer.get("box")}
        if not agree:
            row["review_reasons"] = [*row["review_reasons"], "models_disagree"]
    return rows


def gpu_usd_today(log_path: Path, now: float | None = None) -> float:
    """Estimated GPU spend today (UTC) from an OpenModel log: calls within GLIMMER_IDLE_S of each other are one session
    of the container, which costs from its first call (or wake) to its last answer plus GLIMMER_IDLE_S."""
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, timezone.utc).date()
    try:
        lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0.0
    spans = []
    for line in lines:
        try:
            record = json.loads(line)
            start = float(record["ts"])
            end = start + float(record.get("seconds") or 0)
        except (ValueError, KeyError, TypeError):
            continue
        if datetime.fromtimestamp(start, timezone.utc).date() == day:
            spans.append((start, end))
    seconds, session = 0.0, None
    for start, end in sorted(spans):
        if session is not None and start <= session[1] + GLIMMER_IDLE_S:
            session[1] = max(session[1], end)
            continue
        if session is not None:
            seconds += session[1] - session[0] + GLIMMER_IDLE_S
        session = [start, end]
    if session is not None:
        seconds += session[1] - session[0] + GLIMMER_IDLE_S
    return seconds / 3600 * GPU_USD_PER_HOUR
