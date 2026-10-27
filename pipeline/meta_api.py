"""Meta Model API client for the pipeline: Muse Spark JSON answers and SAM 3.1 segmentation.

Every attempt is logged to the run's calls.jsonl with its cost, and every request first reserves an estimate on
the run's CostMeter, so a run stops before it overspends (spec section 13). Ported from the D1 gate client
(spike/meta_client.py) with these changes: the log path and meter are passed in, SAM images are billed per image,
a Spark answer that is not JSON is retried once (seen once in the D1 re-test), and the client is thread-safe.
"""
import base64
import json
import random
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from pipeline import config
from pipeline.cost import CostMeter, sam_image_cost, sam_video_cost, spark_cost

ATTEMPTS = 5  # rate limits, 5xx and transport errors
MAX_CONSECUTIVE_FAILURES = 5  # this many failed requests in a row stop the run


class SamStreamError(RuntimeError):
    """A SAM stream reported failure, was incomplete, or ended before response.completed."""


class SparkFormatError(RuntimeError):
    """Spark answered with text that is not JSON. The answer was billed; cost_usd says how much."""

    def __init__(self, message: str, cost_usd: float):
        super().__init__(message)
        self.cost_usd = cost_usd


class RunAborted(BaseException):
    """MAX_CONSECUTIVE_FAILURES requests in a row failed. A BaseException, so stage code that catches Exception
    around one request cannot swallow it."""


RETRYABLE = (RateLimitError, APIConnectionError, InternalServerError, SamStreamError, SparkFormatError)
# A failed SAM stream may already be billed and a non-JSON Spark answer was billed: one retry each.
ATTEMPT_LIMITS = {SamStreamError: 2, SparkFormatError: 2}


def text_part(text: str) -> dict:
    return {"type": "input_text", "text": text}


def image_part(path: Path) -> dict:
    b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}


def file_part(file_id: str) -> dict:
    return {"type": "input_file", "file_id": file_id}


def video_part(file_id: str) -> dict:
    return {"type": "input_video", "file_id": file_id}


def error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def _usage_dict(usage) -> dict:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    dump = getattr(usage, "model_dump", None)
    return dump() if callable(dump) else {}


def _event_detail(event) -> str:
    """The error message, status and incomplete reason of a stream event, where present."""
    resp = getattr(event, "response", None)
    parts = [f"event={event.type}"]
    status = getattr(resp, "status", None)
    if status:
        parts.append(f"status={status}")
    err = getattr(resp, "error", None) or getattr(event, "error", None)
    if err:
        parts.append(f"error={getattr(err, 'message', err)}")
    details = getattr(resp, "incomplete_details", None)
    if details:
        parts.append(f"incomplete={getattr(details, 'reason', details)}")
    return ", ".join(parts)


class MetaApi:
    def __init__(self, log_path: Path, meter: CostMeter, client=None, sleep=time.sleep):
        for model in (config.SPARK_MODEL, config.SAM_MODEL):
            if "contributor" in model.lower():
                raise ValueError(f"contributor models are not allowed (their input trains Meta models): {model}")
        self.log_path = Path(log_path)
        self.meter = meter
        self._client = client
        self._sleep = sleep
        self._lock = threading.Lock()
        self._failures = 0

    @property
    def client(self):
        if self._client is None:
            # SDK retries are off so they do not stack under _retry; the timeout covers slow SAM streams.
            self._client = OpenAI(base_url=config.META_BASE_URL, api_key=config.api_key(), max_retries=0,
                                  timeout=300)
        return self._client

    def log(self, kind: str, **fields) -> None:
        line = json.dumps({"kind": kind, "ts": time.time(), **fields}, ensure_ascii=False)
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _retry(self, attempt):
        seen: dict[type, int] = {}
        for i in range(ATTEMPTS):
            try:
                return attempt()
            except RETRYABLE as exc:
                limited = next((t for t in ATTEMPT_LIMITS if isinstance(exc, t)), None)
                if limited is not None:
                    seen[limited] = seen.get(limited, 0) + 1
                    if seen[limited] >= ATTEMPT_LIMITS[limited]:
                        raise
                if i == ATTEMPTS - 1:
                    raise
                self._sleep(min(30, 2 ** i) + random.random())

    def _counted(self, request):
        """Run one request (with its retries) through the circuit breaker; a success resets the count."""
        try:
            result = request()
        except Exception as exc:
            with self._lock:
                self._failures += 1
                failures = self._failures
            if failures >= MAX_CONSECUTIVE_FAILURES:
                self.log("run_aborted", consecutive_failures=failures, error=error_text(exc))
                raise RunAborted(f"{failures} requests in a row failed; last error: {error_text(exc)}") from exc
            raise
        with self._lock:
            self._failures = 0
        return result

    def _request(self, kind: str, estimate_usd: float, attempt, fail_cost_usd: float = 0.0, **fields):
        """Reserve estimate_usd, run attempt() with retries and the circuit breaker, then settle the real cost.

        attempt() returns (result, cost_usd, extra_log_fields). Every attempt is logged. A failed attempt is
        charged the exception's own cost_usd when it has one, else fail_cost_usd.
        """
        self.meter.reserve(estimate_usd)
        spent = 0.0

        def one():
            nonlocal spent
            t0 = time.time()
            try:
                result, cost, extra = attempt()
            except Exception as exc:
                cost = getattr(exc, "cost_usd", fail_cost_usd)
                spent += cost
                self.log(f"{kind}_failed", seconds=time.time() - t0, error=error_text(exc), cost_usd=cost, **fields)
                raise
            spent += cost
            self.log(kind, seconds=time.time() - t0, cost_usd=cost, **fields, **extra)
            return result

        try:
            return self._counted(lambda: self._retry(one))
        finally:
            self.meter.settle(estimate_usd, spent)

    def spark_json(self, parts: list[dict], schema_name: str, schema: dict,
                   estimate_usd: float = config.SPARK_IMAGE_ESTIMATE_USD) -> dict:
        def attempt():
            resp = self.client.responses.create(
                model=config.SPARK_MODEL,
                input=[{"type": "message", "role": "user", "content": parts}],
                text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
            )
            usage = _usage_dict(resp.usage)
            cost = spark_cost(usage)
            try:
                return json.loads(resp.output_text), cost, {"usage": usage}
            except ValueError:
                raise SparkFormatError(f"Spark answer is not JSON: {resp.output_text[:200]!r}", cost) from None

        return self._request("spark", estimate_usd, attempt, schema=schema_name)

    def spark_video_json(self, prompt: str, file_id: str, schema_name: str, schema: dict) -> dict:
        return self.spark_json([text_part(prompt), file_part(file_id)], schema_name, schema,
                               estimate_usd=config.SPARK_VIDEO_ESTIMATE_USD)

    def _stream(self, prompt: str, media: dict) -> str:
        chunks: list[str] = []
        completed = False
        with self.client.responses.stream(
            model=config.SAM_MODEL,
            input=[{"type": "message", "role": "user", "content": [text_part(prompt), media]}],
            metadata={"include_confidence": "true"},
        ) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    chunks.append(event.delta)
                elif event.type == "response.completed":
                    completed = True
                elif event.type in ("response.failed", "response.incomplete", "error"):
                    raise SamStreamError(f"SAM stream failed: {_event_detail(event)}")
        if not completed:
            raise SamStreamError("SAM stream ended without response.completed")
        return "".join(chunks)

    def sam_image(self, prompt: str, image: Path) -> str:
        cost = sam_image_cost(1)
        media = image_part(image)

        def attempt():
            text = self._stream(prompt, media)
            return text, cost, {"chars": len(text), "images": 1}

        return self._request("sam", cost, attempt, fail_cost_usd=cost, prompt=prompt[:120])

    def sam_video(self, prompt: str, file_id: str, frames: int) -> str:
        cost = sam_video_cost(frames)

        def attempt():
            text = self._stream(prompt, video_part(file_id))
            return text, cost, {"chars": len(text), "frames": frames}

        return self._request("sam", cost, attempt, fail_cost_usd=cost, prompt=prompt[:120])

    def upload(self, path: Path) -> str:
        def attempt():
            with open(path, "rb") as f:
                file = self.client.files.create(purpose="user_data", file=f)
            return file.id, 0.0, {"bytes": Path(path).stat().st_size}

        return self._request("upload", 0.0, attempt, path=Path(path).name)

    def delete_file(self, file_id: str) -> None:
        try:
            self.client.files.delete(file_id)
        except Exception as exc:  # cleanup must never stop a run
            self.log("delete_failed", file_id=file_id, error=error_text(exc))

    @contextmanager
    def uploaded(self, path: Path):
        """Upload path for the duration of the block; the file is deleted afterwards whatever happens."""
        file_id = self.upload(path)
        try:
            yield file_id
        finally:
            self.delete_file(file_id)
