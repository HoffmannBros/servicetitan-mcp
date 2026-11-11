"""Registry coverage for scripts/smoke_live.py. No network.

Every @mcp.tool() must be either smoke-tested (REGISTRY) or explicitly skipped
with a reason (SKIPPED), so a tool added later without a smoke entry fails here.
Also exercises the runner against a MockTransport to check it reports
PASS/FAIL, exits 1 on a non-2xx, and never prints response bodies.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_live.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("smoke_live", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


smoke = _load_smoke()


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
    monkeypatch.setattr(server, "tenant_names", lambda: ["acme"])
    return c


def _registry_names() -> list[str]:
    return [name for name, _kw in smoke.REGISTRY]


def test_registered_tools_found():
    names = smoke.registered_tool_names()
    assert "list_customers" in names
    assert len(names) >= 90


def test_every_tool_is_registered_or_skipped():
    covered = set(_registry_names()) | set(smoke.SKIPPED)
    missing = smoke.registered_tool_names() - covered
    assert not missing, f"tools with no smoke entry or SKIPPED reason: {sorted(missing)}"


def test_no_stale_or_duplicate_entries():
    names = _registry_names()
    assert len(names) == len(set(names)), "duplicate REGISTRY entries"
    assert not set(names) & set(smoke.SKIPPED), "tool both registered and skipped"
    stale = (set(names) | set(smoke.SKIPPED)) - smoke.registered_tool_names()
    assert not stale, f"entries for tools that do not exist: {sorted(stale)}"
    assert all(reason.strip() for reason in smoke.SKIPPED.values())


def test_registry_is_list_tools_with_no_required_args():
    for name, kwargs in smoke.REGISTRY:
        assert name.startswith("list_"), name
        fn = server.mcp._tool_manager.get_tool(name).fn
        params = inspect.signature(fn).parameters
        required = {
            p for p, v in params.items()
            if v.default is inspect.Parameter.empty and p != "tenant"
        }
        assert not required - set(kwargs), f"{name} needs {required}"
        if "page_size" in params:
            assert kwargs.get("page_size") == 1, name


def test_one_reporting_call_and_it_runs_last():
    names = _registry_names()
    reporting = [n for n in names if n in smoke.REPORTING_TOOLS]
    assert reporting == ["list_report_categories"]
    assert names[-1] == "list_report_categories"


def test_missing_entry_is_detected(monkeypatch):
    """Dropping a tool from the registry must make the coverage check fail."""
    monkeypatch.setattr(
        smoke, "REGISTRY", [e for e in smoke.REGISTRY if e[0] != "list_customers"]
    )
    with pytest.raises(AssertionError, match="list_customers"):
        test_every_tool_is_registered_or_skipped()


async def test_run_reports_failure_and_hides_bodies(monkeypatch, capsys):
    secret = "Jane Customer 555-0100"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/customers"):
            return httpx.Response(404, json={"detail": secret})
        return httpx.Response(
            200, json={"data": [{"name": secret}], "page": 1, "pageSize": 1,
                       "totalCount": 7, "hasMore": True}
        )

    _wire_client(monkeypatch, handler)
    rc = await smoke.run("acme")
    out = capsys.readouterr().out

    assert rc == 1
    assert secret not in out
    assert "FAIL  list_customers: HTTP 404 GET /crm/v2/tenant/123/customers" in out
    assert "PASS  list_jobs: items=1 total=7" in out
    assert "Failed: list_customers" in out
    lines = [ln for ln in out.splitlines() if ln.startswith(("PASS", "FAIL"))]
    assert lines[-1].split()[1] == "list_report_categories:"


async def test_run_all_pass_exits_zero(monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json={"data": [], "page": 1, "pageSize": 1,
                                         "hasMore": False})

    _wire_client(monkeypatch, handler)
    assert await smoke.run("acme") == 0
    assert "0 failed" in capsys.readouterr().out
