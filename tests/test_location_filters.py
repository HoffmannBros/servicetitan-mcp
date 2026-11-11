"""Tests for the address / ID filters on `list_locations`.

Each filter was live-verified against `GET /crm/v2/tenant/{id}/locations`
(a nonsense value returned 0 rows, or a real value narrowed the total).
`customerIds` was checked too and is ignored by ServiceTitan, so the tool
does not offer it. These tests lock in the exact path and query-param names.
"""

from __future__ import annotations

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket

LOCATIONS_PATH = "/crm/v2/tenant/123/locations"
FILTER_KEYS = {
    "customerId", "ids", "name", "street", "unit", "city", "state", "zip", "country",
}


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


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)
    return captured


@pytest.mark.parametrize(
    "kwargs, param, expected",
    [
        ({"street": "100 Example Ave"}, "street", "100 Example Ave"),
        ({"unit": "Apt 2"}, "unit", "Apt 2"),
        ({"city": "Springfield"}, "city", "Springfield"),
        ({"state": "ZZ"}, "state", "ZZ"),
        ({"zip": "00000"}, "zip", "00000"),
        ({"country": "USA"}, "country", "USA"),
        ({"name": "Test Customer"}, "name", "Test Customer"),
        ({"customer_id": 42}, "customerId", "42"),
        ({"ids": [1, 2, 3]}, "ids", "1,2,3"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
async def test_list_locations_filter_param_name(monkeypatch, kwargs, param, expected):
    captured = _capture(monkeypatch)
    await server.list_locations("t", **kwargs)
    assert captured["path"] == LOCATIONS_PATH
    assert captured["params"][param] == expected
    # Only the one filter we asked for goes out.
    assert FILTER_KEYS & captured["params"].keys() == {param}


async def test_list_locations_no_filters_sends_no_filter_params(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_locations("t")
    assert captured["path"] == LOCATIONS_PATH
    assert not (FILTER_KEYS & captured["params"].keys())
    assert captured["params"]["page"] == "1"
    assert captured["params"]["pageSize"] == "200"


async def test_list_locations_filters_combine(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_locations("t", street="100 Example", zip="00000", customer_id=7)
    p = captured["params"]
    assert (p["street"], p["zip"], p["customerId"]) == ("100 Example", "00000", "7")


async def test_list_locations_never_sends_customer_ids(monkeypatch):
    """ServiceTitan ignores `customerIds` on this endpoint; don't send it."""
    captured = _capture(monkeypatch)
    await server.list_locations("t", customer_id=7, ids=[1])
    assert "customerIds" not in captured["params"]
