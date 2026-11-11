"""W2-A filter-name audit: lock in the query-param names ServiceTitan honors.

ServiceTitan silently ignores unknown query params, so a misspelled filter
returns unfiltered data. Every assertion here pins a name that was checked
against a live tenant (or the API docs where the tenant had no rows), and the
"not sent" assertions pin names that were proven to be ignored.
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


def _capture(monkeypatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [], "hasMore": False})

    _wire_client(monkeypatch, handler)
    return seen


def _params(req: httpx.Request) -> dict:
    return dict(req.url.params)


# ── Inventory feeds + technician ratings: createdOnOrBefore → createdBefore ──

CREATED_UPPER_TOOLS = [
    ("adjustments", server.list_inventory_adjustments,
     "/inventory/v2/tenant/123/adjustments"),
    ("transfers", server.list_inventory_transfers,
     "/inventory/v2/tenant/123/transfers"),
    ("receipts", server.list_inventory_receipts,
     "/inventory/v2/tenant/123/receipts"),
    ("returns", server.list_inventory_returns,
     "/inventory/v2/tenant/123/returns"),
    ("technician_ratings", server.list_technician_ratings,
     "/customer-interactions/v2/tenant/123/technician-ratings"),
]


@pytest.mark.parametrize(
    "fn,path", [(f, p) for _, f, p in CREATED_UPPER_TOOLS],
    ids=[i for i, _, _ in CREATED_UPPER_TOOLS],
)
async def test_created_on_or_before_bare_date_becomes_next_day_created_before(
    monkeypatch, fn, path
):
    seen = _capture(monkeypatch)
    await fn("t", created_on_or_after="2026-11-01", created_on_or_before="2026-11-10")
    req = seen[0]
    assert req.url.path == path
    params = _params(req)
    assert params["createdOnOrAfter"] == "2026-11-01"
    assert params["createdBefore"] == "2026-11-11"
    assert "createdOnOrBefore" not in params


@pytest.mark.parametrize(
    "fn", [f for _, f, _ in CREATED_UPPER_TOOLS],
    ids=[i for i, _, _ in CREATED_UPPER_TOOLS],
)
async def test_created_on_or_before_timestamp_passes_through(monkeypatch, fn):
    seen = _capture(monkeypatch)
    await fn("t", created_on_or_before="2026-11-10T06:00:00Z")
    params = _params(seen[0])
    assert params["createdBefore"] == "2026-11-10T06:00:00Z"
    assert "createdOnOrBefore" not in params


@pytest.mark.parametrize(
    "fn", [f for _, f, _ in CREATED_UPPER_TOOLS],
    ids=[i for i, _, _ in CREATED_UPPER_TOOLS],
)
async def test_created_before_is_sent_as_is(monkeypatch, fn):
    seen = _capture(monkeypatch)
    await fn("t", created_before="2026-11-10")
    assert _params(seen[0])["createdBefore"] == "2026-11-10"


@pytest.mark.parametrize(
    "fn", [f for _, f, _ in CREATED_UPPER_TOOLS],
    ids=[i for i, _, _ in CREATED_UPPER_TOOLS],
)
async def test_both_created_upper_bounds_raise(monkeypatch, fn):
    seen = _capture(monkeypatch)
    with pytest.raises(ValueError, match="only one"):
        await fn("t", created_before="2026-11-10", created_on_or_before="2026-11-10")
    assert seen == []


async def test_inventory_bool_active_and_business_unit_names(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_inventory_receipts("t", business_unit_id=7, active=False)
    params = _params(seen[0])
    assert params["businessUnitIds"] == "7"
    assert params["active"] == "false"


# ── Dispatch ────────────────────────────────────────────────────────────────

async def test_appointment_assignments_job_and_appointment_filters(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_appointment_assignments("t", job_id=11, appointment_id=22)
    req = seen[0]
    assert req.url.path == "/dispatch/v2/tenant/123/appointment-assignments"
    params = _params(req)
    assert params["jobId"] == "11"
    assert params["appointmentIds"] == "22"
    assert not any(k.startswith("starts") for k in params)


@pytest.mark.parametrize(
    "kwargs",
    [{"starts_on_or_after": "2026-11-10"}, {"starts_on_or_before": "2026-11-10"}],
    ids=["on_or_after", "on_or_before"],
)
async def test_appointment_assignments_start_filters_raise(monkeypatch, kwargs):
    seen = _capture(monkeypatch)
    with pytest.raises(ValueError, match="cannot filter by start time"):
        await server.list_appointment_assignments("t", **kwargs)
    assert seen == []


async def test_technician_shifts_window_param_names(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_technician_shifts(
        "t", starts_on_or_after="2026-11-10", ends_on_or_before="2026-11-11T12:00:00Z"
    )
    req = seen[0]
    assert req.url.path == "/dispatch/v2/tenant/123/technician-shifts"
    params = _params(req)
    assert params["startsOnOrAfter"] == "2026-11-10"
    assert params["endsOnOrBefore"] == "2026-11-11T12:00:00Z"
    assert "startsOnOrBefore" not in params


async def test_technician_shifts_starts_on_or_before_raises(monkeypatch):
    seen = _capture(monkeypatch)
    with pytest.raises(ValueError, match="ends_on_or_before"):
        await server.list_technician_shifts("t", starts_on_or_before="2026-11-10")
    assert seen == []


async def test_non_job_appointments_starts_on_or_after(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_non_job_appointments("t", starts_on_or_after="2026-11-01")
    assert _params(seen[0])["startsOnOrAfter"] == "2026-11-01"


# ── CRM ─────────────────────────────────────────────────────────────────────

async def test_bookings_status_raises(monkeypatch):
    seen = _capture(monkeypatch)
    with pytest.raises(ValueError, match="status"):
        await server.list_bookings("t", status="Pending")
    assert seen == []


async def test_bookings_without_status_sends_no_status(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_bookings("t")
    req = seen[0]
    assert req.url.path == "/crm/v2/tenant/123/bookings"
    assert "status" not in _params(req)


async def test_contacts_with_customer_id_uses_customer_subresource(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_contacts("t", customer_id=555)
    req = seen[0]
    assert req.url.path == "/crm/v2/tenant/123/customers/555/contacts"
    assert "customerId" not in _params(req)


async def test_contacts_without_customer_id_uses_tenant_list(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_contacts("t")
    assert seen[0].url.path == "/crm/v2/tenant/123/contacts"


# ── Payroll / Forms ─────────────────────────────────────────────────────────

async def test_gross_pay_items_sends_payroll_ids(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_gross_pay_items("t", payroll_id=9001, employee_id=42)
    req = seen[0]
    assert req.url.path == "/payroll/v2/tenant/123/gross-pay-items"
    params = _params(req)
    assert params["payrollIds"] == "9001"
    assert params["employeeId"] == "42"


async def test_form_submissions_date_maps_to_submitted_on_or_after(monkeypatch):
    seen = _capture(monkeypatch)
    await server.list_form_submissions("t", created_on_or_after="2026-11-01")
    req = seen[0]
    assert req.url.path == "/forms/v2/tenant/123/submissions"
    params = _params(req)
    assert params["submittedOnOrAfter"] == "2026-11-01"
    assert "createdOnOrAfter" not in params
