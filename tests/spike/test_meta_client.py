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
