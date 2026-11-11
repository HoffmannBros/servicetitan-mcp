"""Tests for W1-H new API coverage.

  - `list_reviews`: Marketing Reputation reviews
    (/marketingreputation/v2/tenant/{id}/reviews).
  - `call_reasons` lookup kind: Job Booking call reasons
    (/jbce/v2/tenant/{id}/call-reasons).

Both paths and every filter name below were live-verified (GET, 200) before
the code was written. Array filters must go out as repeated keys: the live API
returns 400 for a comma-joined value.
"""

from __future__ import annotations

import json

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


@pytest.fixture(autouse=True)
def _clear_resource_cache():
    server._resource_cache.clear()
    yield
    server._resource_cache.clear()


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


# ── list_reviews ─────────────────────────────────────────────────────────


async def test_list_reviews_hits_verified_path_with_no_filters(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"data": [{"internalId": 1, "rating": 5}], "hasMore": False}
        )

    _wire_client(monkeypatch, handler)
    out = await server.list_reviews("t")

    assert captured["path"] == "/marketingreputation/v2/tenant/123/reviews"
    assert captured["params"] == {"page": "1", "pageSize": "200"}
    assert '"internalId":1' in out
    assert "hasMore=" in out  # _fmt footer


async def test_list_reviews_scalar_filters_map_to_query_params(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)
    await server.list_reviews(
        "t",
        page=2,
        page_size=5,
        from_date="2026-10-01T00:00:00Z",
        to_date="2026-11-01T00:00:00Z",
        created_on_or_after="2026-09-01T00:00:00Z",
        created_before="2026-12-01T00:00:00Z",
        from_rating=1,
        to_rating=2,
    )

    assert captured["params"] == {
        "page": "2",
        "pageSize": "5",
        "fromDate": "2026-10-01T00:00:00Z",
        "toDate": "2026-11-01T00:00:00Z",
        "createdOnOrAfter": "2026-09-01T00:00:00Z",
        "createdBefore": "2026-12-01T00:00:00Z",
        "fromRating": "1",
        "toRating": "2",
    }


async def test_list_reviews_array_filters_are_repeated_keys(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        captured["technicianIds"] = q.get_list("technicianIds")
        captured["campaignIds"] = q.get_list("campaignIds")
        captured["locationIds"] = q.get_list("locationIds")
        captured["sources"] = q.get_list("sources")
        captured["keys"] = set(q.keys())
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)
    await server.list_reviews(
        "t",
        technician_ids=[11, 12],
        campaign_ids=[21],
        location_ids=[31, 32],
        sources=["Google"],
    )

    assert captured["technicianIds"] == ["11", "12"]
    assert captured["campaignIds"] == ["21"]
    assert captured["locationIds"] == ["31", "32"]
    assert captured["sources"] == ["Google"]
    assert captured["keys"] == {
        "page",
        "pageSize",
        "technicianIds",
        "campaignIds",
        "locationIds",
        "sources",
    }


async def test_list_reviews_zero_rating_bound_is_sent(monkeypatch):
    """A 0 rating bound is a real value, not 'unset'."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)
    await server.list_reviews("t", from_rating=0)
    assert captured["params"]["fromRating"] == "0"
    assert "toRating" not in captured["params"]


# ── call_reasons lookup kind ─────────────────────────────────────────────


def test_call_reasons_lookup_kind_maps_to_jbce():
    assert server._LOOKUP_KINDS["call_reasons"] == ("jbce", "call-reasons")


async def test_get_lookup_tables_call_reasons_hits_verified_path(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "data": [{"id": 7, "name": "Made-up reason", "isLead": True}],
                "hasMore": False,
                "totalCount": 1,
            },
        )

    _wire_client(monkeypatch, handler)
    out = json.loads(await server.get_lookup_tables("t", kinds=["call_reasons"]))

    assert captured["path"] == "/jbce/v2/tenant/123/call-reasons"
    assert captured["params"] == {"page": "1", "pageSize": "200"}
    assert out["call_reasons"]["items"][0]["name"] == "Made-up reason"


async def test_resource_lookup_call_reasons_hits_verified_path(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"data": [{"id": 7}], "hasMore": False})

    _wire_client(monkeypatch, handler)
    out = json.loads(await server.resource_lookup(tenant="t", kind="call_reasons"))

    assert captured["path"] == "/jbce/v2/tenant/123/call-reasons"
    assert out["kind"] == "call_reasons"
    assert out["items"] == [{"id": 7}]


def test_lookup_docstrings_list_call_reasons():
    assert "call_reasons" in server.get_lookup_tables.__doc__
