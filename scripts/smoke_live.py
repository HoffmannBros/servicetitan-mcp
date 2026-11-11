"""Live smoke test: call every argument-free list tool once against a real tenant.

Usage:
    python scripts/smoke_live.py <tenant>

Why: the unit tests run against httpx.MockTransport, so a wrong URL slug still
passes them. This script hits the real API with page_size=1 for every typed
list tool that needs nothing but a tenant, and reports which ones fail.

Credentials come from the same env vars the server reads (ST_TENANTS plus
ST_TENANT_<NAME>_*), through servicetitan_mcp.config. No new configuration.

Guarantees:
  - Read-only. Only list tools are called; nothing sends POST/PATCH/PUT/DELETE.
  - Sequential. One call at a time, and at most one reporting-API call
    (list_report_categories), run last, because reporting is throttled to
    about 3 calls a minute.
  - No response bodies are printed. Output is PASS/FAIL, HTTP status, request
    path (no query string) and item counts only. Bodies hold customer data, and
    the client's HTTPStatusError message embeds the body, so it is never shown.

A 403 or 409 whose body says a feature is not enabled for the tenant (for
example Leads or CRM Contacts switched off) prints NOT-ENABLED, not FAIL, and
does not affect the exit status: it is tenant configuration, not a bug.

Exit status: 0 if no call FAILed, 1 if any did, 2 on bad usage.

Every @mcp.tool() in servicetitan_mcp.server must appear in REGISTRY or in
SKIPPED; tests/test_smoke_registry.py enforces that, so a new tool without a
smoke entry fails the test suite.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from pathlib import Path

# Allow running from a checkout without `pip install -e .`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402

from servicetitan_mcp import server  # noqa: E402

_ONE = {"page_size": 1}

# (tool_name, kwargs). `tenant` is added at run time for tools that take it.
# Main-API tools first; the single reporting-API call must stay last.
REGISTRY: list[tuple[str, dict]] = [
    ("list_tenants", {}),
    # CRM
    ("list_customers", _ONE),
    ("list_locations", _ONE),
    ("list_leads", _ONE),
    ("list_bookings", _ONE),
    ("list_contacts", _ONE),
    ("list_customer_custom_field_types", _ONE),
    ("list_location_custom_field_types", _ONE),
    # Jobs
    ("list_jobs", _ONE),
    ("list_job_cancel_reasons", _ONE),
    ("list_job_hold_reasons", _ONE),
    ("list_appointments", _ONE),
    ("list_job_types", _ONE),
    ("list_projects", _ONE),
    # Accounting
    ("list_invoices", _ONE),
    ("list_payments", _ONE),
    ("list_payment_types", _ONE),
    ("list_inventory_bills", _ONE),
    ("list_journal_entries", _ONE),
    ("list_payment_terms", _ONE),
    ("list_tax_zones", _ONE),
    # Sales
    ("list_estimates", _ONE),
    # Dispatch
    ("list_appointment_assignments", _ONE),
    ("list_technician_shifts", _ONE),
    ("list_zones", _ONE),
    ("list_non_job_appointments", _ONE),
    ("list_arrival_windows", _ONE),
    ("list_teams", _ONE),
    # Pricebook
    ("list_pricebook_services", _ONE),
    ("list_pricebook_materials", _ONE),
    ("list_pricebook_equipment", _ONE),
    ("list_pricebook_categories", _ONE),
    # Inventory
    ("list_purchase_orders", _ONE),
    ("list_warehouses", _ONE),
    ("list_inventory_vendors", _ONE),
    ("list_trucks", _ONE),
    ("list_inventory_adjustments", _ONE),
    ("list_inventory_transfers", _ONE),
    ("list_inventory_receipts", _ONE),
    ("list_inventory_returns", _ONE),
    # Customer interactions
    ("list_technician_ratings", _ONE),
    # Memberships
    ("list_memberships", _ONE),
    ("list_membership_types", _ONE),
    ("list_recurring_services", _ONE),
    # Settings / people
    ("list_employees", _ONE),
    ("list_technicians", _ONE),
    ("list_business_units", _ONE),
    ("list_tag_types", _ONE),
    ("list_user_roles", _ONE),
    # Payroll
    ("list_payrolls", _ONE),
    # Telecom
    ("list_calls", _ONE),
    # Forms
    ("list_forms", _ONE),
    ("list_form_submissions", _ONE),
    # Marketing
    ("list_campaigns", _ONE),
    ("list_campaign_costs", _ONE),
    ("list_reviews", _ONE),
    # Equipment systems / task management / CRM activities
    ("list_installed_equipment", _ONE),
    ("list_tasks", _ONE),
    ("list_activities", _ONE),
    ("list_activity_categories", _ONE),
    # Reporting API (about 3 calls a minute): keep exactly one, and keep it last.
    ("list_report_categories", {}),
]

REPORTING_TOOLS = frozenset({"list_report_categories"})

_NEEDS_ID = "needs an ID argument"
SKIPPED: dict[str, str] = {
    # get_* tools need a record ID.
    "get_customer": _NEEDS_ID,
    "get_location": _NEEDS_ID,
    "get_lead": _NEEDS_ID,
    "get_booking": _NEEDS_ID,
    "get_job": _NEEDS_ID,
    "get_job_history": _NEEDS_ID,
    "get_appointment": _NEEDS_ID,
    "get_job_type": _NEEDS_ID,
    "get_project": _NEEDS_ID,
    "get_invoice": _NEEDS_ID,
    "get_estimate": _NEEDS_ID,
    "get_technician_shift": _NEEDS_ID,
    "get_non_job_appointment": _NEEDS_ID,
    "get_purchase_order": _NEEDS_ID,
    "get_membership": _NEEDS_ID,
    "get_recurring_service": _NEEDS_ID,
    "get_employee": _NEEDS_ID,
    "get_technician": _NEEDS_ID,
    "get_call": _NEEDS_ID,
    # Sub-resources keyed by a parent ID.
    "list_customer_notes": "sub-resource; needs customer_id",
    "list_location_notes": "sub-resource; needs location_id",
    "list_location_contacts": "sub-resource; needs location_id",
    "list_job_notes": "sub-resource; needs job_id",
    "list_job_attachments": "sub-resource; needs job_id",
    "list_employee_payrolls": "sub-resource; needs payroll_id",
    "list_gross_pay_items": "sub-resource; needs payroll_id",
    # Reporting API: throttled, and these need a category / report ID.
    "list_reports_in_category": "reporting API; needs category",
    "get_report": "reporting API; needs category and report_id",
    "get_report_parameter_values": "reporting API; needs dynamic_set_id",
    "run_report": "reporting API; needs category and report_id",
    "run_report_to_file": "reporting API; needs category and report_id; writes a file",
    # Others.
    "download_job_photos": "needs job IDs; downloads files to disk",
    "export_feed": "needs category and feed; continuation-token export",
    "export_feed_to_file": "needs category and feed; writes a file",
    "servicetitan_api_call": "escape hatch; needs method and path",
    "get_lookup_tables": "fan-out over list tools already in the registry",
}

_FOOTER = re.compile(r"\(Showing (\d+) of (\S+) results")


def registered_tool_names() -> set[str]:
    """Names of every @mcp.tool() registered on servicetitan_mcp.server.mcp."""
    # mcp 1.x FastMCP keeps tools on its ToolManager; list_tools() is the
    # public accessor and returns Tool objects with a `.name`.
    return {t.name for t in server.mcp._tool_manager.list_tools()}


def _tool_fn(name: str):
    return server.mcp._tool_manager.get_tool(name).fn


def _summarize(result: str) -> str:
    """Item counts from a _fmt footer; never the body itself."""
    m = _FOOTER.search(result or "")
    if m:
        return f"items={m.group(1)} total={m.group(2)}"
    return f"chars={len(result or '')}"


PASS, FAIL, NOT_ENABLED = "PASS", "FAIL", "NOT-ENABLED"

# A 403 or 409 whose body says a ServiceTitan feature or package is switched
# off for the tenant (seen live: "Leads is not enabled.", "...feature that
# isn't available in your account", MarketingAdsOptimizerPackageIsNotEnabled).
# That is tenant configuration, not a code bug, so it must not turn a run red.
# Any other 403/409 (e.g. a missing API scope on the app) stays a FAIL.
_FEATURE_GATE_STATUSES = frozenset({403, 409})
_FEATURE_GATE_TEXT = re.compile(
    r"not enabled|isn'?t available in your account|"
    r"is not available in your account|IsNotEnabled",
    re.IGNORECASE,
)


def _is_feature_gate(response: httpx.Response) -> bool:
    """True for a tenant-feature-off response. Reads the body, never prints it."""
    if response.status_code not in _FEATURE_GATE_STATUSES:
        return False
    try:
        text = response.text
    except Exception:  # noqa: BLE001 - unreadable body: treat as a real failure
        return False
    return bool(_FEATURE_GATE_TEXT.search(text or ""))


async def _call(name: str, kwargs: dict, tenant: str) -> tuple[str, str]:
    """Returns (PASS | FAIL | NOT-ENABLED, detail). Detail never holds a body."""
    fn = _tool_fn(name)
    call_kwargs = dict(kwargs)
    if "tenant" in inspect.signature(fn).parameters:
        call_kwargs["tenant"] = tenant
    try:
        result = fn(**call_kwargs)
        if inspect.isawaitable(result):
            result = await result
    except httpx.HTTPStatusError as exc:
        # str(exc) embeds the response body; print status and path only.
        detail = f"HTTP {exc.response.status_code} GET {exc.request.url.path}"
        if _is_feature_gate(exc.response):
            return NOT_ENABLED, detail + " (feature not enabled on this tenant)"
        return FAIL, detail
    except httpx.HTTPError as exc:
        try:
            path = exc.request.url.path
        except RuntimeError:  # no request attached
            path = "?"
        return FAIL, f"{type(exc).__name__} GET {path}"
    except Exception as exc:  # noqa: BLE001 - report and keep going
        return FAIL, f"{type(exc).__name__} (message suppressed)"
    return PASS, _summarize(result)


async def run(tenant: str) -> int:
    tenant = tenant.strip().lower()
    try:
        server._get_client(tenant)
    except Exception as exc:  # noqa: BLE001
        # UnknownTenantError's message lists slugs only, never secrets.
        print(f"ERROR: {exc}")
        return 2

    ordered = [e for e in REGISTRY if e[0] not in REPORTING_TOOLS]
    ordered += [e for e in REGISTRY if e[0] in REPORTING_TOOLS]

    for name, reason in sorted(SKIPPED.items()):
        print(f"SKIP  {name}: {reason}")

    failures: list[str] = []
    not_enabled: list[str] = []
    for name, kwargs in ordered:
        outcome, detail = await _call(name, kwargs, tenant)
        print(f"{outcome}  {name}: {detail}")
        if outcome == FAIL:
            failures.append(name)
        elif outcome == NOT_ENABLED:
            not_enabled.append(name)

    passed = len(ordered) - len(failures) - len(not_enabled)
    print(
        f"\nSummary: {passed} passed, {len(failures)} failed, "
        f"{len(not_enabled)} not enabled, {len(SKIPPED)} skipped "
        f"(tenant={tenant})"
    )
    if not_enabled:
        print("Not enabled on this tenant: " + ", ".join(not_enabled))
    if failures:
        print("Failed: " + ", ".join(failures))
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print("usage: python scripts/smoke_live.py <tenant>")
        return 2
    return asyncio.run(run(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
