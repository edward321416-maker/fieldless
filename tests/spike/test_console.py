import io
import sys

import pytest

from spike.console import utf8_stdout


def cp949_stdout() -> io.TextIOWrapper:
    """What a piped stdout looks like on this Windows machine."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp949")


def test_cp949_stream_cannot_print_cafe():
    # the failure the helper exists for
    with pytest.raises(UnicodeEncodeError):
        print("Café", file=cp949_stdout())


def test_utf8_stdout_makes_any_name_printable(monkeypatch):
    fake = cp949_stdout()
    monkeypatch.setattr(sys, "stdout", fake)
    utf8_stdout()
    print("Café ⚪ 카페")
    fake.flush()
    assert fake.buffer.getvalue().decode("utf-8").strip() == "Café ⚪ 카페"


def test_stream_without_reconfigure_is_left_alone(monkeypatch):
    fake = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake)
    utf8_stdout()  # must not raise
    print("Café")
    assert fake.getvalue() == "Café\n"
