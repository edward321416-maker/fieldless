import json
from types import SimpleNamespace

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
