"""Tests for DataMasqueClient _paginate shapes and wait_for_run transitions."""

import pytest

from masque_bricks.config import DataMasqueConfig
from masque_bricks.datamasque_client import DataMasqueClient, DataMasqueError


def _client(verify_ssl=True):
    cfg = DataMasqueConfig(
        host="https://dm.example.com",
        username="u",
        password="p",
        verify_ssl=verify_ssl,
    )
    client = DataMasqueClient(cfg)
    client._authenticated = True  # skip the login round-trip
    return client


def test_verify_ssl_propagates_to_session():
    assert _client(verify_ssl=True).session.verify is True
    assert _client(verify_ssl=False).session.verify is False


def test_paginate_bare_list(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_request", lambda m, u: [{"id": 1}, {"id": 2}])
    assert client._paginate("/api/connections/") == [{"id": 1}, {"id": 2}]


def test_paginate_follows_next(monkeypatch):
    client = _client()
    pages = {
        "/api/connections/": {
            "results": [{"id": 1}],
            "next": "https://dm.example.com/api/connections/?page=2",
        },
        "/api/connections/?page=2": {"results": [{"id": 2}], "next": None},
    }
    monkeypatch.setattr(client, "_request", lambda m, u: pages[u])
    assert client._paginate("/api/connections/") == [{"id": 1}, {"id": 2}]


def test_wait_for_run_finished(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "get_run", lambda rid: {"status": "finished", "id": rid})
    assert client.wait_for_run(7, poll_interval=0)["status"] == "finished"


def test_wait_for_run_failed(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        client, "get_run", lambda rid: {"status": "failed", "error_message": "boom"}
    )
    with pytest.raises(DataMasqueError, match="boom"):
        client.wait_for_run(7, poll_interval=0)


def test_wait_for_run_cancelled(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "get_run", lambda rid: {"status": "cancelled"})
    with pytest.raises(DataMasqueError, match="cancelled"):
        client.wait_for_run(7, poll_interval=0)


def test_wait_for_run_times_out(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "get_run", lambda rid: {"status": "running"})
    with pytest.raises(DataMasqueError, match="timed out"):
        client.wait_for_run(7, timeout=0, poll_interval=0)


def test_wait_for_run_tolerates_transient_error(monkeypatch):
    client = _client()
    calls = {"n": 0}

    def flaky(rid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DataMasqueError("transient")
        return {"status": "finished", "id": rid}

    monkeypatch.setattr(client, "get_run", flaky)
    assert client.wait_for_run(7, timeout=5, poll_interval=0)["status"] == "finished"
    assert calls["n"] == 2
