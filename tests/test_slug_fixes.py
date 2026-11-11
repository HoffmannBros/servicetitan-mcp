"""Tests for W1-A: the two wrong-slug 404 tools and the installed-equipment filter.

Live-verified 2026-11-11:
  - `/equipmentsystems/v2/...` and `/taskmanagement/v2/...` return 200; the
    hyphenated slugs the tools used before return 404.
  - Installed equipment honors only `locationIds` (comma-separated, several IDs
    allowed). A singular `locationId` is silently ignored and returns every row.

Pattern mirrors `tests/test_slice4_tools.py`.
"""

from __future__ import annotations

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket


@pytest.fixture(autouse=True)
def _stub_token(monkeypatch):
    """Avoid real network auth. Every test gets a dummy bearer token."""

    async def fake_get_token(*_a, **_kw):
        return "fake-token"

    monkeypatch.setattr(auth.token_manager, "get_token", fake_get_token)
    yield


def _wire_client(monkeypatch, handler) -> ServiceTitanClient:
    """Build a MockTransport-backed client and patch `_get_client` to return it."""
    transport = httpx.MockTransport(handler)
    c = ServiceTitanClient(
        app_key="k",
        client_id="ci",
        client_secret="cs",
        tenant_id="123",
        transport=transport,
        retry=RetryConfig(max_retries=0, base_backoff=0.01),
        main_limiter=TokenBucket(rate=1000, capacity=1000),
        reporting_limiter=TokenBucket(rate=1000, capacity=1000),
    )
    monkeypatch.setattr(server, "_get_client", lambda _tenant: c)
    return c


def _capturing(captured: list):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"data": [{"id": 1}], "hasMore": False})

    return handler


async def test_list_installed_equipment_path(monkeypatch):
    captured: list = []
    _wire_client(monkeypatch, _capturing(captured))

    out = await server.list_installed_equipment("t")

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "GET"
    assert req.url.path == "/equipmentsystems/v2/tenant/123/installed-equipment"
    assert "locationIds" not in req.url.params
    assert "locationId" not in req.url.params
    assert "hasMore=False" in out


async def test_list_tasks_path(monkeypatch):
    captured: list = []
    _wire_client(monkeypatch, _capturing(captured))

    out = await server.list_tasks("t")

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "GET"
    assert req.url.path == "/taskmanagement/v2/tenant/123/tasks"
    assert "hasMore=False" in out


async def test_installed_equipment_single_location_id_sends_plural_param(monkeypatch):
    captured: list = []
    _wire_client(monkeypatch, _capturing(captured))

    await server.list_installed_equipment("t", location_id=555)

    params = captured[0].url.params
    assert params["locationIds"] == "555"
    assert "locationId" not in params


async def test_installed_equipment_several_location_ids_comma_joined(monkeypatch):
    captured: list = []
    _wire_client(monkeypatch, _capturing(captured))

    await server.list_installed_equipment("t", location_ids=[11, 22, 33])

    params = captured[0].url.params
    assert params.get_list("locationIds") == ["11,22,33"]
    assert "locationId" not in params


async def test_installed_equipment_combines_and_dedupes_both_filters(monkeypatch):
    captured: list = []
    _wire_client(monkeypatch, _capturing(captured))

    await server.list_installed_equipment("t", location_id=22, location_ids=[11, 22, 33])

    params = captured[0].url.params
    assert params.get_list("locationIds") == ["22,11,33"]
    assert "locationId" not in params


async def test_installed_equipment_empty_list_sends_no_filter(monkeypatch):
    captured: list = []
    _wire_client(monkeypatch, _capturing(captured))

    await server.list_installed_equipment("t", location_ids=[])

    assert "locationIds" not in captured[0].url.params
