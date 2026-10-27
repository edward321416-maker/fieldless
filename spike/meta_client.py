import base64
import json
import random
import time
from pathlib import Path

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from spike import config

SAM_MIN_BILLED_FRAMES = 50
ATTEMPTS = 5  # rate limits, 5xx and transport errors: retried patiently
STREAM_FAILURE_ATTEMPTS = 2  # a SAM stream that failed or was cut off may already be billed: one retry only
MAX_CONSECUTIVE_FAILURES = 5  # this many failed requests in a row stop the whole run


class SamStreamError(RuntimeError):
    """A SAM stream reported failure, was incomplete, or ended before response.completed."""


class RunAborted(BaseException):
    """MAX_CONSECUTIVE_FAILURES requests in a row failed, so the run stops instead of paying for more failures.

    It subclasses BaseException on purpose: the run scripts catch Exception around every request, so that one
    bad request cannot discard the paid results before it. RunAborted must escape those handlers, the way
    KeyboardInterrupt does, while their finally blocks still delete the uploaded files.
    """


RETRYABLE = (RateLimitError, APIConnectionError, InternalServerError, SamStreamError)
ATTEMPT_LIMITS = {SamStreamError: STREAM_FAILURE_ATTEMPTS}
_client: OpenAI | None = None
_consecutive_failures = 0  # failed sam()/spark_json() requests since the last success


def client() -> OpenAI:
    global _client
    if _client is None:
        # SDK retries are off so they do not stack under _retry; timeout covers slow SAM streams.
        _client = OpenAI(base_url=config.META_BASE_URL, api_key=config.api_key(), max_retries=0, timeout=300)
    return _client


def log_call(kind: str, **fields) -> None:
    with open(config.OUT / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "ts": time.time(), **fields}, ensure_ascii=False) + "\n")


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def _retry(fn, attempts: int = ATTEMPTS, retry_on: tuple = RETRYABLE, limits: dict | None = None):
    """Call fn, retrying retry_on errors with exponential backoff, at most `attempts` calls in total.

    limits maps an exception type to a lower cap on how many times errors of that type may occur before the
    error is re-raised. The default (ATTEMPT_LIMITS) gives SAM stream failures a single retry, because a stream
    that failed or was cut off may already have been billed; transport, rate-limit and 5xx errors keep the full
    `attempts`.
    """
    limits = ATTEMPT_LIMITS if limits is None else limits
    seen: dict[type, int] = {}
    for i in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            limited = next((t for t in limits if isinstance(exc, t)), None)
            if limited is not None:
                seen[limited] = seen.get(limited, 0) + 1
                if seen[limited] >= limits[limited]:
                    raise
            if i == attempts - 1:
                raise
            time.sleep(min(30, 2 ** i) + random.random())


def _log_failures(fn, kind: str, **fields):
    """Wrap one attempt so that a failure is logged to calls.jsonl (kind, seconds, error, fields), then re-raised.

    Failed attempts may still be billed, so the gate report counts them as an upper bound on spend.
    """
    def attempt():
        t0 = time.time()
        try:
            return fn()
        except Exception as exc:
            log_call(kind, seconds=time.time() - t0, error=_error_text(exc), **fields)
            raise
    return attempt


def _counted(request):
    """Run one request (with its retries) through the run-level circuit breaker.

    A success resets the failure counter. The MAX_CONSECUTIVE_FAILURES-th failure in a row raises RunAborted,
    chained to the request's own error. The counter is not reset by the abort, so until a request succeeds every
    further failure aborts as well.
    """
    global _consecutive_failures
    try:
        result = request()
    except Exception as exc:
        _consecutive_failures += 1
        if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            log_call("run_aborted", consecutive_failures=_consecutive_failures, error=_error_text(exc))
            raise RunAborted(f"{_consecutive_failures} requests in a row failed, stopping the run; "
                             f"last error: {_error_text(exc)}") from exc
        raise
    _consecutive_failures = 0
    return result


def reset_failures() -> None:
    """Reset the circuit breaker (for tests, or a caller that deliberately continues after RunAborted)."""
    global _consecutive_failures
    _consecutive_failures = 0


def _usage_dict(usage) -> dict | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage or None
    dump = getattr(usage, "model_dump", None)
    return dump() if callable(dump) else None


def _guard_model(model: str) -> None:
    if "contributor" in model.lower():
        raise ValueError(f"contributor models are not allowed in this client: {model}")


def _event_detail(event) -> str:
    """Pull the error message, status and incomplete reason out of a stream event, if present."""
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
    message = getattr(event, "message", None)
    if message:
        parts.append(f"message={message}")
    return ", ".join(parts)


def text_part(text: str) -> dict:
    return {"type": "input_text", "text": text}


def file_part(file_id: str) -> dict:
    return {"type": "input_file", "file_id": file_id}


def video_part(file_id: str) -> dict:
    return {"type": "input_video", "file_id": file_id}


def image_part(path: Path) -> dict:
    b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}


def upload(path: Path) -> str:
    t0 = time.time()

    def call():
        with open(path, "rb") as f:
            return client().files.create(purpose="user_data", file=f)

    file = _retry(call)
    log_call("upload", path=str(path), bytes=Path(path).stat().st_size, seconds=time.time() - t0)
    return file.id


def delete_file(file_id: str) -> None:
    try:
        client().files.delete(file_id)
    except Exception as exc:  # cleanup must never abort a run
        log_call("delete_failed", file_id=file_id, error=str(exc))


def spark_json(parts: list[dict], schema_name: str, schema: dict, model: str = config.SPARK_MODEL) -> dict:
    """One structured Spark request. Failed attempts are logged as "spark_failed"; the request counts toward
    the circuit breaker (see _counted)."""
    _guard_model(model)

    def call():
        return client().responses.create(
            model=model,
            input=[{"type": "message", "role": "user", "content": parts}],
            text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        )

    def request() -> dict:
        t0 = time.time()
        resp = _retry(_log_failures(call, "spark_failed", schema=schema_name, model=model))
        usage = resp.usage.model_dump() if resp.usage else {}
        log_call("spark", schema=schema_name, model=model, seconds=time.time() - t0, usage=usage)
        return json.loads(resp.output_text)

    return _counted(request)


def sam(concept: str, media: dict, billed_frames: int = 0, images: int = 0) -> str:
    """One SAM request. Failed attempts are logged as "sam_failed" with the clamped billed_frames; a stream
    failure is retried once only; the request counts toward the circuit breaker (see _counted).

    For an image request pass images=1: images are billed per image (billed_images in the log), without the
    50-frame minimum that applies to video."""
    _guard_model(config.SAM_MODEL)
    requested_frames = billed_frames
    billed = 0 if images else max(SAM_MIN_BILLED_FRAMES, requested_frames)
    image_fields = {"billed_images": images} if images else {}

    def call() -> tuple[str, dict | None]:
        chunks: list[str] = []
        completed = False
        usage = None
        with client().responses.stream(
            model=config.SAM_MODEL,
            input=[{"type": "message", "role": "user", "content": [text_part(concept), media]}],
            metadata={"include_confidence": "true"},
        ) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    chunks.append(event.delta)
                elif event.type == "response.completed":
                    completed = True
                    usage = _usage_dict(getattr(getattr(event, "response", None), "usage", None))
                elif event.type in ("response.failed", "response.incomplete", "error"):
                    raise SamStreamError(f"SAM stream failed: {_event_detail(event)}")
        if not completed:
            raise SamStreamError("SAM stream ended without response.completed")
        return "".join(chunks), usage

    def request() -> str:
        t0 = time.time()
        text, usage = _retry(_log_failures(call, "sam_failed", concept=concept,
                                           requested_frames=requested_frames, billed_frames=billed, **image_fields))
        log_call("sam", concept=concept, requested_frames=requested_frames, billed_frames=billed,
                 seconds=time.time() - t0, chars=len(text), status="completed", **image_fields,
                 **({"usage": usage} if usage else {}))
        return text

    return _counted(request)
