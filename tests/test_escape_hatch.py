"""Tests for the `servicetitan_api_call` escape hatch and client write helpers.

Locks in:
  - API errors propagate as `httpx.HTTPStatusError` (no "API Error" string).
  - POST, PATCH and PUT send both `query_params` and the JSON body.
  - GET still sends query params.
  - An unsupported method still returns the friendly error string.
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


async def test_404_propagates_with_body(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text='{"title":"No such widget"}')

    _wire_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await server.servicetitan_api_call(
            tenant="t",
            method="GET",
            path="/nope/v2/tenant/{tenant_id}/widgets",
        )
    assert excinfo.value.response.status_code == 404
    assert "No such widget" in str(excinfo.value)


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT"])
async def test_write_methods_send_params_and_body(monkeypatch, method):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"ok": True})

    _wire_client(monkeypatch, handler)
    out = await server.servicetitan_api_call(
        tenant="t",
        method=method.lower(),
        path="/crm/v2/tenant/{tenant_id}/customers/7",
        query_params={"dryRun": "true", "source": "mcp"},
        body={"name": "Example Co"},
    )

    assert '"ok": true' in out
    assert captured["method"] == method
    assert captured["path"] == "/crm/v2/tenant/123/customers/7"
    assert captured["params"] == {"dryRun": "true", "source": "mcp"}
    assert captured["body"] == {"name": "Example Co"}


async def test_get_sends_query_params(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"data": [], "page": 1, "pageSize": 5, "totalCount": 0, "hasMore": False}
        )

    _wire_client(monkeypatch, handler)
    await server.servicetitan_api_call(
        tenant="t",
        method="GET",
        path="/crm/v2/tenant/{tenant_id}/locations",
        query_params={"pageSize": 5, "street": "1 Main"},
    )

    assert captured["method"] == "GET"
    assert captured["path"] == "/crm/v2/tenant/123/locations"
    assert captured["params"] == {"pageSize": "5", "street": "1 Main"}


async def test_unsupported_method_returns_error_string(monkeypatch):
    _wire_client(monkeypatch, lambda _: pytest.fail("handler should not be called"))
    out = await server.servicetitan_api_call(
        tenant="t",
        method="DELETE",
        path="/crm/v2/tenant/{tenant_id}/customers/7",
    )
    assert out == "Unsupported method: DELETE. Use GET, POST, PATCH, or PUT."
