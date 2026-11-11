"""Regression tests for pagination display.

Pre-fix bug: `_fmt` silently sliced the displayed list to 25 items regardless
of requested page_size, and showed `hasMore=False` from whatever ST returned —
causing callers with page_size=200 to see only 25 items and assume there were
no more. That produced ~40%-of-truth counts for attribution work.
"""

from __future__ import annotations

import json

import httpx
import pytest

from servicetitan_mcp import auth, client as client_mod, server
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


def _parse_footer(output: str) -> dict:
    """Pull the trailing `(Showing N of T ... hasMore=X)` into a dict."""
    assert "(Showing " in output, f"no footer in output: {output[-200:]}"
    tail = output[output.rindex("(Showing ") :].strip("()")
    # "Showing 71 of 71 results — page 1, pageSize 100, hasMore=False"
    parts = {}
    parts["shown"] = int(tail.split(" ", 2)[1])
    parts["total"] = tail.split(" of ", 1)[1].split(" ", 1)[0]
    parts["hasMore"] = "hasMore=True" in tail
    return parts


async def test_list_tool_with_page_size_100_returns_all_71_records(monkeypatch):
    """Repro of the reported bug: a page_size large enough to fit every record
    must return every record to the caller, not a silent 25-item slice."""

    # 71 records — more than the old hidden 25-item cap.
    jobs = [{"id": i, "summary": f"Job {i}"} for i in range(1, 72)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/jpm/v2/tenant/123/jobs" in request.url.path
        assert request.url.params.get("pageSize") == "100"
        # Mirror ST: full page fits, so hasMore is False on page 1.
        return httpx.Response(
            200,
            json={
                "data": jobs,
                "page": 1,
                "pageSize": 100,
                "totalCount": 71,
                "hasMore": False,
            },
        )

    _wire_client(monkeypatch, handler)
    output = await server.list_jobs(tenant="test", page=1, page_size=100)

    # Strip the footer to parse the JSON payload
    body, _, _ = output.partition("\n\n(Showing")
    returned = json.loads(body)
    assert len(returned) == 71, (
        f"expected all 71 jobs visible to caller, got {len(returned)}. "
        "The _fmt 25-item silent cap has regressed."
    )
    footer = _parse_footer(output)
    assert footer["shown"] == 71
    assert footer["total"] == "71"
    assert footer["hasMore"] is False


async def test_list_tool_hasmore_true_when_full_page_and_no_totalcount(monkeypatch):
    """If ST doesn't return totalCount and a full page comes back, the caller
    must see hasMore=True — the pre-fix inference silently set it False."""

    jobs = [{"id": i} for i in range(1, 51)]

    def handler(request: httpx.Request) -> httpx.Response:
        # ST sometimes omits totalCount AND hasMore; caller must still be told
        # to keep paging when a full page was returned.
        return httpx.Response(
            200,
            json={
                "data": jobs,
                "page": 1,
                "pageSize": 50,
                "totalCount": None,
            },
        )

    _wire_client(monkeypatch, handler)
    output = await server.list_jobs(tenant="test", page=1, page_size=50)

    footer = _parse_footer(output)
    assert footer["shown"] == 50
    assert footer["total"] == "unknown", (
        f"expected 'unknown' when ST gives totalCount=None, got {footer['total']!r}"
    )
    assert footer["hasMore"] is True, (
        "full page with no totalCount should infer hasMore=True"
    )


async def test_list_tool_hasmore_false_when_partial_page(monkeypatch):
    """A partial page (fewer items than page_size) with no totalCount should
    infer hasMore=False — that's the signal there's nothing left."""

    jobs = [{"id": i} for i in range(1, 22)]  # 21 items, page_size=50

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": jobs,
                "page": 3,
                "pageSize": 50,
                "totalCount": None,
            },
        )

    _wire_client(monkeypatch, handler)
    output = await server.list_jobs(tenant="test", page=3, page_size=50)

    footer = _parse_footer(output)
    assert footer["shown"] == 21
    assert footer["hasMore"] is False, (
        "partial page with no totalCount should infer hasMore=False"
    )


# ── Compact JSON body (W2-B) ─────────────────────────────────────────────

def _synthetic_items(n: int) -> list[dict]:
    return [
        {
            "id": i,
            "name": f"Customer {i}",
            "active": i % 2 == 0,
            "balance": 12.5 * i,
            "address": {"street": f"{i} Main St", "zip": "00000"},
            "tagTypeIds": [1, 2, 3],
            "note": None,
        }
        for i in range(n)
    ]


def test_fmt_body_is_compact_and_round_trips():
    items = _synthetic_items(200)
    output = server._fmt(
        {"data": items, "page": 1, "pageSize": 200, "totalCount": 500, "hasMore": True}
    )
    body, sep, footer = output.partition("\n\n(Showing")
    assert sep, "footer missing"
    assert json.loads(body) == items
    # Compact separators, one record per line, no indentation.
    assert '": ' not in body and ", " not in body.replace("Customer ", "")
    lines = body.split("\n")
    assert lines[0] == "[" and lines[-1] == "]"
    assert len(lines) == len(items) + 2
    assert not any(line.startswith(" ") for line in lines)
    # Footer text is byte-for-byte unchanged.
    assert "(Showing" + footer == (
        "(Showing 200 of 500 results — page 1, pageSize 200, hasMore=True)"
    )
    # Smaller than the old indent=2 rendering.
    assert len(body) < len(json.dumps(items, indent=2, default=str))


def test_fmt_empty_page_round_trips():
    output = server._fmt({"data": [], "page": 1, "pageSize": 50, "totalCount": 0})
    body, _, _ = output.partition("\n\n(Showing")
    assert json.loads(body) == []
    assert output.endswith("(Showing 0 of 0 results — page 1, pageSize 50, hasMore=False)")


def test_fmt_uses_default_str_for_non_json_values():
    import datetime as dt

    stamp = dt.datetime(2026, 1, 2, 3, 4, 5)
    output = server._fmt({"data": [{"id": 1, "at": stamp}], "pageSize": 10})
    body, _, _ = output.partition("\n\n(Showing")
    assert json.loads(body) == [{"id": 1, "at": str(stamp)}]


def test_fmt_export_body_is_compact_and_round_trips():
    items = _synthetic_items(200)
    output = server._fmt_export({"data": items, "hasMore": True, "continueFrom": "tok"})
    body, sep, footer = output.partition("\n\n(Export feed")
    assert sep, "footer missing"
    assert json.loads(body) == items
    lines = body.split("\n")
    assert len(lines) == len(items) + 2
    assert not any(line.startswith(" ") for line in lines)
    assert "(Export feed" + footer == (
        "(Export feed — 200 items this page, hasMore=True, continueFrom='tok'. "
        'hasMore=True — more data available NOW. Call export_feed again '
        'immediately with from_token="tok".)'
    )


def test_fmt_export_empty_page_round_trips():
    output = server._fmt_export({"data": [], "hasMore": False, "continueFrom": "tok"})
    body, _, _ = output.partition("\n\n(Export feed")
    assert json.loads(body) == []


def test_fmt_zero_total_with_items_reports_unknown():
    """Forms endpoints send totalCount=0 alongside a non-empty page and
    hasMore=True. The footer must not claim "1 of 0"; hasMore stays as sent."""
    out = server._fmt(
        {"data": [{"id": 1}], "page": 1, "pageSize": 1, "totalCount": 0, "hasMore": True}
    )
    footer = _parse_footer(out)
    assert footer["shown"] == 1
    assert footer["total"] == "unknown"
    assert footer["hasMore"] is True


def test_fmt_zero_total_with_items_and_no_hasmore_infers_from_full_page():
    out = server._fmt({"data": [{"id": 1}], "page": 1, "pageSize": 1, "totalCount": 0})
    footer = _parse_footer(out)
    assert footer["total"] == "unknown"
    assert footer["hasMore"] is True


def test_fmt_zero_total_on_empty_page_stays_zero():
    out = server._fmt(
        {"data": [], "page": 1, "pageSize": 50, "totalCount": 0, "hasMore": False}
    )
    footer = _parse_footer(out)
    assert footer["shown"] == 0
    assert footer["total"] == "0"
    assert footer["hasMore"] is False


@pytest.mark.parametrize("returned, expect_more", [(3, True), (2, False)])
async def test_inventory_bills_bare_list_gets_footer(monkeypatch, returned, expect_more):
    """/accounting/inventory-bills returns a bare JSON array (live-verified).
    The tool must still emit the pagination footer, inferring hasMore from
    whether a full page came back."""
    bills = [{"id": i} for i in range(returned)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/accounting/v2/tenant/123/inventory-bills"
        assert request.url.params.get("pageSize") == "3"
        return httpx.Response(200, json=bills)

    _wire_client(monkeypatch, handler)
    out = await server.list_inventory_bills(tenant="test", page=2, page_size=3)

    body, _, _ = out.partition("\n\n(Showing")
    assert json.loads(body) == bills
    footer = _parse_footer(out)
    assert footer["shown"] == returned
    assert footer["total"] == "unknown"
    assert footer["hasMore"] is expect_more
    assert "page 2, pageSize 3" in out
