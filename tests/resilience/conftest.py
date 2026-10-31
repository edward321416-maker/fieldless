"""Failure cases for the judging days, 2026-11-05 to 11-08: a restart, a spending cap, an upstream outage, a stuck worker,
a long-running container. Every test here uses fakes.

A plain test is a case that holds. A test marked xfail(strict=True) states what the live app should do and does not do
yet, so the suite stays green and the test turns red the day the fix lands: then the marker comes off."""
import socket

import pytest

from server import store


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A test that reaches past this machine fails at once. Loopback stays open: asyncio needs it on Windows."""
    real = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"a resilience test tried to reach {host}")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture(autouse=True)
def no_waits_between_restores(monkeypatch):
    monkeypatch.setattr(store, "RESTORE_WAIT_S", 0.0)


@pytest.fixture(autouse=True)
def plain_environment(monkeypatch):
    for name in ("INVITE_CODE", "ITEM_INVITE_CODE", "IP_HASH_SALT", "SCANS_PER_IP_PER_DAY", "DEMO_SCANS", "THRESHOLD_RUN",
                 "HF_DATA_REPO", "BACKUP_DIR", "PUBLIC_COST_CAP_USD"):
        monkeypatch.delenv(name, raising=False)
