"""Tests for the date-window and ID filters on list_jobs, list_appointments and
list_calls.

Locks in the exact request path and query-param names. Each name was checked
against a live tenant by comparing a filtered call with an unfiltered one:

  - jobs: createdBefore, completedBefore, modifiedOnOrAfter, modifiedBefore,
    number (NOT jobNumber, which ST silently ignores), ids, businessUnitId,
    jobTypeId, campaignId, technicianId, projectId.
  - appointments: startsBefore (startsOnOrBefore is silently ignored),
    createdOnOrAfter, createdBefore, modifiedOnOrAfter, modifiedBefore, jobId.
  - calls: createdBefore (createdOnOrBefore is silently ignored).

Fixture pattern copied from `tests/test_slice4_tools.py`.
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


def _capture(monkeypatch) -> dict:
    """Wire a client that records the last request's path and params."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)
    return captured


def _filters(params: dict) -> dict:
    """Drop the pagination params every list call sends."""
    return {k: v for k, v in params.items() if k not in ("page", "pageSize", "includeTotal")}


# ── list_jobs ──────────────────────────────────────────────────────────────

JOB_FILTERS = [
    ("created_before", "2026-11-01", "createdBefore", "2026-11-01"),
    ("completed_before", "2026-11-01", "completedBefore", "2026-11-01"),
    ("modified_on_or_after", "2026-10-01", "modifiedOnOrAfter", "2026-10-01"),
    ("modified_before", "2026-11-01", "modifiedBefore", "2026-11-01"),
    ("job_number", "100200", "number", "100200"),
    ("ids", "11,22", "ids", "11,22"),
    ("business_unit_id", 7, "businessUnitId", "7"),
    ("job_type_id", 8, "jobTypeId", "8"),
    ("campaign_id", 9, "campaignId", "9"),
    ("technician_id", 10, "technicianId", "10"),
    ("project_id", 11, "projectId", "11"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwarg,value,param,expected", JOB_FILTERS, ids=[f[0] for f in JOB_FILTERS]
)
async def test_list_jobs_new_filter_param_names(monkeypatch, kwarg, value, param, expected):
    captured = _capture(monkeypatch)
    await server.list_jobs("t", **{kwarg: value})
    assert captured["path"] == "/jpm/v2/tenant/123/jobs"
    assert _filters(captured["params"]) == {param: expected}


@pytest.mark.asyncio
async def test_list_jobs_job_number_does_not_send_jobNumber(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_jobs("t", job_number="100200")
    assert "jobNumber" not in captured["params"]


@pytest.mark.asyncio
async def test_list_jobs_closed_created_window(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_jobs(
        "t", created_on_or_after="2026-10-01", created_before="2026-11-01"
    )
    assert _filters(captured["params"]) == {
        "createdOnOrAfter": "2026-10-01",
        "createdBefore": "2026-11-01",
    }


@pytest.mark.asyncio
async def test_list_jobs_existing_params_unchanged(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_jobs(
        "t",
        status="Completed",
        customer_id=5,
        created_on_or_after="2026-10-01",
        completed_on_or_after="2026-10-02",
    )
    assert _filters(captured["params"]) == {
        "jobStatus": "Completed",
        "customerId": "5",
        "createdOnOrAfter": "2026-10-01",
        "completedOnOrAfter": "2026-10-02",
    }


# ── list_appointments ──────────────────────────────────────────────────────

APPT_FILTERS = [
    ("starts_before", "2026-11-11", "startsBefore", "2026-11-11"),
    ("created_on_or_after", "2026-11-01", "createdOnOrAfter", "2026-11-01"),
    ("created_before", "2026-11-02", "createdBefore", "2026-11-02"),
    ("modified_on_or_after", "2026-11-01", "modifiedOnOrAfter", "2026-11-01"),
    ("modified_before", "2026-11-02", "modifiedBefore", "2026-11-02"),
    ("job_id", 42, "jobId", "42"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwarg,value,param,expected", APPT_FILTERS, ids=[f[0] for f in APPT_FILTERS]
)
async def test_list_appointments_new_filter_param_names(
    monkeypatch, kwarg, value, param, expected
):
    captured = _capture(monkeypatch)
    await server.list_appointments("t", **{kwarg: value})
    assert captured["path"] == "/jpm/v2/tenant/123/appointments"
    assert _filters(captured["params"]) == {param: expected}


@pytest.mark.asyncio
async def test_list_appointments_starts_on_or_before_date_sends_next_day_startsBefore(
    monkeypatch,
):
    captured = _capture(monkeypatch)
    await server.list_appointments(
        "t", starts_on_or_after="2026-11-10", starts_on_or_before="2026-11-10"
    )
    assert _filters(captured["params"]) == {
        "startsOnOrAfter": "2026-11-10",
        "startsBefore": "2026-11-11",
    }
    assert "startsOnOrBefore" not in captured["params"]


@pytest.mark.asyncio
async def test_list_appointments_starts_on_or_before_month_end_rolls_over(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_appointments("t", starts_on_or_before="2026-12-31")
    assert _filters(captured["params"]) == {"startsBefore": "2027-01-01"}


@pytest.mark.asyncio
async def test_list_appointments_starts_on_or_before_timestamp_passes_through(
    monkeypatch,
):
    captured = _capture(monkeypatch)
    await server.list_appointments("t", starts_on_or_before="2026-11-10T18:00:00Z")
    assert _filters(captured["params"]) == {"startsBefore": "2026-11-10T18:00:00Z"}


@pytest.mark.asyncio
async def test_list_appointments_rejects_both_upper_bounds(monkeypatch):
    _capture(monkeypatch)
    with pytest.raises(ValueError):
        await server.list_appointments(
            "t", starts_before="2026-11-11", starts_on_or_before="2026-11-10"
        )


# ── list_calls ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_calls_created_before(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_calls(
        "t", created_on_or_after="2026-11-01", created_before="2026-11-02"
    )
    assert captured["path"] == "/telecom/v2/tenant/123/calls"
    assert _filters(captured["params"]) == {
        "createdOnOrAfter": "2026-11-01",
        "createdBefore": "2026-11-02",
    }


@pytest.mark.asyncio
async def test_list_calls_created_on_or_before_sends_next_day_createdBefore(monkeypatch):
    captured = _capture(monkeypatch)
    await server.list_calls("t", created_on_or_before="2026-11-01")
    assert _filters(captured["params"]) == {"createdBefore": "2026-11-02"}
    assert "createdOnOrBefore" not in captured["params"]


@pytest.mark.asyncio
async def test_list_calls_rejects_both_upper_bounds(monkeypatch):
    _capture(monkeypatch)
    with pytest.raises(ValueError):
        await server.list_calls(
            "t", created_before="2026-11-02", created_on_or_before="2026-11-01"
        )
