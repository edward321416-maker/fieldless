import json
from types import SimpleNamespace

import httpx
import pytest

from spike import meta_client as mc


def test_parts_have_expected_shapes(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    assert mc.text_part("hi") == {"type": "input_text", "text": "hi"}
    assert mc.file_part("file-1") == {"type": "input_file", "file_id": "file-1"}
    assert mc.video_part("file-1") == {"type": "input_video", "file_id": "file-1"}
    part = mc.image_part(img)
    assert part["type"] == "input_image"
    assert part["image_url"].startswith("data:image/jpeg;base64,")


def test_retry_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    assert mc._retry(flaky, retry_on=(ValueError,)) == "ok"
    assert calls["n"] == 3


# ---- offline contract tests: fake client, call log redirected to tmp_path, no sleeps ----


class FakeStream:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return iter(self.events)

    def __exit__(self, *exc):
        return False


class FakeClient:
    def __init__(self, stream_events=None, spark_text='{"answer": "ok"}', delete_exc=None):
        self.stream_events = stream_events or []
        self.stream_calls = []
        self.spark_text = spark_text
        self.delete_exc = delete_exc
        self.responses = SimpleNamespace(create=self._create, stream=self._stream)
        self.files = SimpleNamespace(delete=self._delete)

    def _create(self, **kwargs):
        return SimpleNamespace(
            output_text=self.spark_text,
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 5}),
        )

    def _stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        events = self.stream_events
        if callable(events):
            events = events(len(self.stream_calls))
        return FakeStream(events)

    def _delete(self, file_id):
        if self.delete_exc:
            raise self.delete_exc
        return None


def delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def completed():
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(status="completed"))


def read_log(tmp_path):
    lines = (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(mc.config, "OUT", tmp_path)
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    monkeypatch.setattr(mc, "_consecutive_failures", 0)  # circuit breaker state must not leak between tests
    monkeypatch.setattr(mc.config, "SAM_MODEL", "sam-3.1")
    monkeypatch.setattr(mc.config, "SPARK_MODEL", "muse-spark-1.3")

    def install(fake):
        monkeypatch.setattr(mc, "client", lambda: fake)
        return fake

    return install


def test_retry_reraises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise ValueError("still down")

    with pytest.raises(ValueError, match="still down"):
        mc._retry(always_fails, attempts=3, retry_on=(ValueError,))
    assert calls["n"] == 3


def test_retry_non_retryable_propagates_after_one_call(monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        mc._retry(bad)
    assert calls["n"] == 1


def test_spark_json_returns_parsed_and_logs(offline, tmp_path):
    offline(FakeClient(spark_text='{"answer": "ok"}'))
    result = mc.spark_json([mc.text_part("hi")], "smoke", {"type": "object"})
    assert result == {"answer": "ok"}
    (entry,) = read_log(tmp_path)
    assert entry["kind"] == "spark"
    assert entry["usage"] == {"total_tokens": 5}
    assert isinstance(entry["seconds"], float)


def test_sam_assembles_deltas_and_logs_clamped_frames(offline, tmp_path):
    fake = offline(FakeClient(stream_events=[delta("<0f>"), delta("ab"), completed()]))
    text = mc.sam("tactile paving", {"type": "input_image", "image_url": "x"}, billed_frames=3)
    assert text == "<0f>ab"
    assert fake.stream_calls[0]["metadata"] == {"include_confidence": "true"}
    (entry,) = read_log(tmp_path)
    assert entry["kind"] == "sam"
    assert entry["concept"] == "tactile paving"
    assert entry["billed_frames"] == 50
    assert entry["requested_frames"] == 3
    assert entry["status"] == "completed"
    assert entry["chars"] == len("<0f>ab")


def test_sam_image_request_is_logged_per_image_without_frame_minimum(offline, tmp_path):
    offline(FakeClient(stream_events=[delta("<0f>"), completed()]))
    mc.sam("stairs", {"type": "input_image", "image_url": "x"}, images=1)
    (entry,) = read_log(tmp_path)
    assert entry["billed_images"] == 1
    assert entry["billed_frames"] == 0


def test_sam_raises_on_failed_event(offline):
    failed = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(status="failed", error=SimpleNamespace(message="gpu oom")),
    )
    offline(FakeClient(stream_events=[delta("<0f>"), failed]))
    with pytest.raises(mc.SamStreamError, match="gpu oom"):
        mc.sam("tactile paving", {}, billed_frames=50)


def test_sam_raises_when_stream_ends_without_completed(offline):
    offline(FakeClient(stream_events=[delta("<0f>")]))
    with pytest.raises(mc.SamStreamError, match="without response.completed"):
        mc.sam("tactile paving", {}, billed_frames=50)


def test_sam_retries_transient_stream_error_then_succeeds(offline):
    failed = SimpleNamespace(type="error", message="transient")
    offline(FakeClient(stream_events=lambda n: [failed] if n == 1 else [delta("ok"), completed()]))
    assert mc.sam("tactile paving", {}, billed_frames=50) == "ok"


def test_delete_file_swallows_error_and_logs(offline, tmp_path):
    offline(FakeClient(delete_exc=RuntimeError("down")))
    mc.delete_file("file-9")  # must not raise
    (entry,) = read_log(tmp_path)
    assert entry["kind"] == "delete_failed"
    assert entry["file_id"] == "file-9"


def test_contributor_models_are_rejected(offline, monkeypatch):
    offline(FakeClient())
    with pytest.raises(ValueError, match="contributor"):
        mc.spark_json([mc.text_part("hi")], "smoke", {"type": "object"}, model="muse-contributor-1")
    monkeypatch.setattr(mc.config, "SAM_MODEL", "sam-contributor-2")
    with pytest.raises(ValueError, match="contributor"):
        mc.sam("tactile paving", {}, billed_frames=50)


# ---- retry caps, failed-attempt log, circuit breaker, SAM usage ----

def _connection_error():
    return mc.APIConnectionError(request=httpx.Request("POST", "https://api.meta.ai/v1/responses"))


def stream_failed(message="gpu oom"):
    return SimpleNamespace(type="response.failed",
                           response=SimpleNamespace(status="failed", error=SimpleNamespace(message=message)))


def test_retry_gives_sam_stream_failures_one_retry_only(monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def stream_fails():
        calls["n"] += 1
        raise mc.SamStreamError("SAM stream failed: event=response.failed")

    with pytest.raises(mc.SamStreamError):
        mc._retry(stream_fails)
    assert calls["n"] == mc.STREAM_FAILURE_ATTEMPTS == 2


def test_retry_keeps_five_attempts_for_transport_errors(monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def down():
        calls["n"] += 1
        raise _connection_error()

    with pytest.raises(mc.APIConnectionError):
        mc._retry(down)
    assert calls["n"] == mc.ATTEMPTS == 5


def test_retry_counts_stream_failures_separately_from_transport_errors(monkeypatch):
    monkeypatch.setattr(mc.time, "sleep", lambda s: None)
    errors = [_connection_error(), mc.SamStreamError("cut off"), _connection_error(), mc.SamStreamError("cut off")]
    calls = {"n": 0}

    def mixed():
        calls["n"] += 1
        raise errors[calls["n"] - 1]

    with pytest.raises(mc.SamStreamError):
        mc._retry(mixed)
    assert calls["n"] == 4  # the second stream failure stops it, before the 5-attempt cap


def test_sam_stream_failure_is_retried_once_and_every_attempt_is_logged(offline, tmp_path):
    fake = offline(FakeClient(stream_events=[delta("<0f>"), stream_failed()]))
    with pytest.raises(mc.SamStreamError, match="gpu oom"):
        mc.sam("tactile paving", {}, billed_frames=7)
    assert len(fake.stream_calls) == 2
    entries = read_log(tmp_path)
    assert [e["kind"] for e in entries] == ["sam_failed", "sam_failed"]
    for e in entries:
        assert e["concept"] == "tactile paving"
        assert e["billed_frames"] == 50 and e["requested_frames"] == 7  # clamped, as billed
        assert isinstance(e["seconds"], float)
        assert e["error"].startswith("SamStreamError: SAM stream failed") and "gpu oom" in e["error"]


def test_sam_logs_failed_attempt_then_success(offline, tmp_path):
    offline(FakeClient(stream_events=lambda n: [stream_failed()] if n == 1 else [delta("ok"), completed()]))
    assert mc.sam("stairs", {}, billed_frames=60) == "ok"
    assert [e["kind"] for e in read_log(tmp_path)] == ["sam_failed", "sam"]


def test_spark_failed_attempts_are_logged(offline, tmp_path):
    fake = offline(FakeClient())

    def down(**kwargs):
        raise _connection_error()

    fake.responses.create = down
    with pytest.raises(mc.APIConnectionError):
        mc.spark_json([mc.text_part("hi")], "smoke", {"type": "object"})
    entries = read_log(tmp_path)
    assert [e["kind"] for e in entries] == ["spark_failed"] * mc.ATTEMPTS
    assert entries[0]["schema"] == "smoke"
    assert entries[0]["error"].startswith("APIConnectionError")
    assert isinstance(entries[0]["seconds"], float)


def test_sam_logs_usage_when_completed_event_carries_it(offline, tmp_path):
    done = SimpleNamespace(type="response.completed", response=SimpleNamespace(
        status="completed", usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 12, "output_tokens": 3})))
    offline(FakeClient(stream_events=[delta("x"), done]))
    mc.sam("stairs", {}, billed_frames=50)
    (entry,) = read_log(tmp_path)
    assert entry["usage"] == {"input_tokens": 12, "output_tokens": 3}


def test_sam_without_usage_has_no_usage_field(offline, tmp_path):
    offline(FakeClient(stream_events=[delta("x"), completed()]))
    mc.sam("stairs", {}, billed_frames=50)
    (entry,) = read_log(tmp_path)
    assert "usage" not in entry


def test_run_aborted_is_not_an_exception():
    # run scripts catch Exception per request; RunAborted has to get past them
    assert issubclass(mc.RunAborted, BaseException)
    assert not issubclass(mc.RunAborted, Exception)


class ScriptedSpark(FakeClient):
    """responses.create follows a script: "fail" raises a non-retryable error, "ok" answers."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)

    def _create(self, **kwargs):
        if self.script.pop(0) == "fail":
            raise ValueError("400 bad request")
        return super()._create(**kwargs)


def call_spark_like_a_run_script() -> str:
    try:
        mc.spark_json([mc.text_part("hi")], "smoke", {"type": "object"})
        return "ok"
    except Exception:  # what every run script does per request
        return "error"


def test_five_consecutive_failed_requests_abort_the_run(offline, tmp_path):
    offline(ScriptedSpark(["fail"] * 5))
    assert [call_spark_like_a_run_script() for _ in range(4)] == ["error"] * 4
    with pytest.raises(mc.RunAborted, match="5 requests in a row failed") as err:
        call_spark_like_a_run_script()
    assert isinstance(err.value.__cause__, ValueError)
    assert read_log(tmp_path)[-1]["kind"] == "run_aborted"


def test_a_success_resets_the_failure_count(offline):
    offline(ScriptedSpark(["fail"] * 4 + ["ok"] + ["fail"] * 4 + ["fail"]))
    results = [call_spark_like_a_run_script() for _ in range(9)]
    assert results == ["error"] * 4 + ["ok"] + ["error"] * 4  # 4 + 4 failures, never 5 in a row
    with pytest.raises(mc.RunAborted):
        call_spark_like_a_run_script()  # the fifth in a row after the success


def test_sam_requests_count_toward_the_breaker(offline):
    offline(FakeClient(stream_events=[stream_failed()]))
    for _ in range(mc.MAX_CONSECUTIVE_FAILURES - 1):
        with pytest.raises(mc.SamStreamError):
            mc.sam("stairs", {}, billed_frames=50)
    with pytest.raises(mc.RunAborted):
        mc.sam("stairs", {}, billed_frames=50)


def test_model_guard_does_not_count_as_a_failed_request(offline, monkeypatch):
    offline(FakeClient())
    for _ in range(mc.MAX_CONSECUTIVE_FAILURES + 1):
        with pytest.raises(ValueError, match="contributor"):
            mc.spark_json([mc.text_part("hi")], "smoke", {"type": "object"}, model="muse-contributor-1")
    assert mc._consecutive_failures == 0
