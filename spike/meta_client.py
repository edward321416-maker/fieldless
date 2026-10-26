import base64
import json
import random
import time
from pathlib import Path

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from spike import config

SAM_MIN_BILLED_FRAMES = 50


class SamStreamError(RuntimeError):
    """A SAM stream reported failure, was incomplete, or ended before response.completed."""


RETRYABLE = (RateLimitError, APIConnectionError, InternalServerError, SamStreamError)
_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        # SDK retries are off so they do not stack under _retry; timeout covers slow SAM streams.
        _client = OpenAI(base_url=config.META_BASE_URL, api_key=config.api_key(), max_retries=0, timeout=300)
    return _client


def log_call(kind: str, **fields) -> None:
    with open(config.OUT / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "ts": time.time(), **fields}, ensure_ascii=False) + "\n")


def _retry(fn, attempts: int = 5, retry_on: tuple = RETRYABLE):
    for i in range(attempts):
        try:
            return fn()
        except retry_on:
            if i == attempts - 1:
                raise
            time.sleep(min(30, 2 ** i) + random.random())


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
    _guard_model(model)
    t0 = time.time()
    resp = _retry(lambda: client().responses.create(
        model=model,
        input=[{"type": "message", "role": "user", "content": parts}],
        text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
    ))
    usage = resp.usage.model_dump() if resp.usage else {}
    log_call("spark", schema=schema_name, model=model, seconds=time.time() - t0, usage=usage)
    return json.loads(resp.output_text)


def sam(concept: str, media: dict, billed_frames: int) -> str:
    _guard_model(config.SAM_MODEL)
    requested_frames = billed_frames
    billed = max(SAM_MIN_BILLED_FRAMES, requested_frames)
    t0 = time.time()

    def call():
        chunks: list[str] = []
        completed = False
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
                elif event.type in ("response.failed", "response.incomplete", "error"):
                    raise SamStreamError(f"SAM stream failed: {_event_detail(event)}")
        if not completed:
            raise SamStreamError("SAM stream ended without response.completed")
        return "".join(chunks)

    text = _retry(call)
    log_call("sam", concept=concept, requested_frames=requested_frames, billed_frames=billed,
             seconds=time.time() - t0, chars=len(text), status="completed")
    return text
