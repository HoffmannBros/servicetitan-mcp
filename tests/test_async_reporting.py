"""Tests for the async report query flow (ST-78: sync `/data` deprecated).

`run_report` / `run_report_to_file` POST to `.../data/query`. ServiceTitan
answers 200 inline or 202 + token; the token is polled at
`data-queries/{token}` and cancelled with DELETE on timeout.
"""

from __future__ import annotations

import json

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket

_REPORT = "/reporting/v2/tenant/123/report-category/biz/reports/42"
_POLL = "/reporting/v2/tenant/123/data-queries/tok-1"


def _payload(rows, page=1, has_more=False, total=None):
    return {
        "page": page,
        "pageSize": 50,
        "hasMore": has_more,
        "totalCount": total if total is not None else len(rows),
        "fields": [{"name": "id", "label": "ID"}],
        "data": rows,
    }


@pytest.fixture(autouse=True)
def _stub_token_and_sleep(monkeypatch):
    async def fake_get_token(*_a, **_kw):
        return "fake-token"

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(auth.token_manager, "get_token", fake_get_token)
    monkeypatch.setattr(server.asyncio, "sleep", no_sleep)
    yield


def _wire_client(monkeypatch, handler) -> ServiceTitanClient:
    c = ServiceTitanClient(
        app_key="k",
        client_id="ci",
        client_secret="cs",
        tenant_id="123",
        transport=httpx.MockTransport(handler),
        retry=RetryConfig(max_retries=0, base_backoff=0.01),
        main_limiter=TokenBucket(rate=1000, capacity=1000),
        reporting_limiter=TokenBucket(rate=1000, capacity=1000),
    )
    monkeypatch.setattr(server, "_get_client", lambda _tenant: c)
    return c


async def test_inline_200_uses_data_query(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=_payload([[1], [2]]))

    _wire_client(monkeypatch, handler)
    out = await server.run_report(tenant="t", report_id=42, category="biz")

    assert seen == [f"POST {_REPORT}/data/query"]
    assert "Error" not in out, out


async def test_202_polls_until_ready(monkeypatch):
    seen: list[str] = []
    polls = iter([
        httpx.Response(202, json={"status": "InProgress", "token": "tok-1"}),
        httpx.Response(200, json=_payload([[7]])),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(202, json={"status": "NotStarted", "token": "tok-1"})
        return next(polls)

    _wire_client(monkeypatch, handler)
    data = await server._fetch_report_page(
        server._get_client("t"), "biz", 42, {"parameters": []}, 1, 50
    )

    assert data["data"] == [[7]]
    assert seen == [
        f"POST {_REPORT}/data/query",
        f"GET {_POLL}",
        f"GET {_POLL}",
    ]


async def test_timeout_cancels_query(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(202, json={"status": "InProgress", "token": "tok-1"})

    monkeypatch.setattr(server, "REPORT_QUERY_TIMEOUT_S", 0)
    _wire_client(monkeypatch, handler)

    with pytest.raises(TimeoutError):
        await server._fetch_report_page(
            server._get_client("t"), "biz", 42, {"parameters": []}, 1, 50
        )
    assert seen[-1] == f"DELETE {_POLL}"


async def test_404_falls_back_to_sync_data(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/data/query"):
            return httpx.Response(404, json={"title": "Not Found"})
        return httpx.Response(200, json=_payload([[3]]))

    _wire_client(monkeypatch, handler)
    data = await server._fetch_report_page(
        server._get_client("t"), "biz", 42, {"parameters": []}, 1, 50
    )

    assert data["data"] == [[3]]
    assert seen == [f"POST {_REPORT}/data/query", f"POST {_REPORT}/data"]


async def test_report_to_file_multi_page_async(monkeypatch, tmp_path):
    pages = {
        1: _payload([[1], [2]], page=1, has_more=True, total=3),
        2: _payload([[3]], page=2, has_more=False, total=3),
    }
    pending_page: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            pending_page["page"] = int(request.url.params["page"])
            return httpx.Response(202, json={"status": "NotStarted", "token": "tok-1"})
        return httpx.Response(200, json=pages[pending_page["page"]])

    _wire_client(monkeypatch, handler)
    out_file = tmp_path / "r.csv"
    out = await server.run_report_to_file(
        tenant="t", report_id=42, category="biz", output_path=str(out_file)
    )

    assert "Error" not in out, out
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["ID", "1", "2", "3"]


async def test_client_request_handles_204():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    c = ServiceTitanClient(
        app_key="k",
        client_id="ci",
        client_secret="cs",
        tenant_id="123",
        transport=httpx.MockTransport(handler),
        main_limiter=TokenBucket(rate=1000, capacity=1000),
        reporting_limiter=TokenBucket(rate=1000, capacity=1000),
    )
    assert await c.delete("/reporting/v2/tenant/123/data-queries/x") == {}


# ── Live-observed shape: PascalCase keys, page/pageSize ignored ──────────


def _pascal_full(rows, has_more=False):
    """What `/data/query` actually returned against a real tenant (2026-09)."""
    return {
        "Page": 1,
        "PageSize": len(rows),
        "HasMore": has_more,
        "Fields": [{"Name": "id", "Label": "ID"}],
        "Data": rows,
        "TotalCount": len(rows),
        "TimeZone": "America/Chicago",
    }


async def test_pascal_case_payload_is_normalized(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pascal_full([[1], [2]]))

    _wire_client(monkeypatch, handler)
    data = await server._fetch_report_page(
        server._get_client("t"), "biz", 42, {"parameters": []}, 1, 50
    )
    assert data["data"] == [[1], [2]]
    assert data["fields"] == [{"name": "id", "label": "ID"}]
    assert data["hasMore"] is False and data["totalCount"] == 2


async def test_run_report_slices_full_result_locally(monkeypatch):
    rows = [[i] for i in range(12)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pascal_full(rows))

    _wire_client(monkeypatch, handler)
    out = await server.run_report(
        tenant="t", report_id=42, category="biz", page=2, page_size=5
    )
    body, footer = out.split("\n\n(", 1)
    assert json.loads(body) == [[5], [6], [7], [8], [9]]
    assert "hasMore=True" in footer and "of 12" in footer


async def test_report_to_file_no_duplicates_when_paging_ignored(monkeypatch, tmp_path):
    # Worst case: server claims HasMore but re-sends the same full result.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pascal_full([[1], [2], [3]], has_more=True))

    _wire_client(monkeypatch, handler)
    out_file = tmp_path / "r.csv"
    out = await server.run_report_to_file(
        tenant="t", report_id=42, category="biz", output_path=str(out_file)
    )
    assert "Error" not in out, out
    assert out_file.read_text(encoding="utf-8").splitlines() == ["ID", "1", "2", "3"]
