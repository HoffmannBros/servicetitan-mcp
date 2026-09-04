# AGENTS.md

## Session start: check `next_steps.md`

`next_steps.md` is a per-user session-handoff file at the repo root. **It is gitignored**, because it may reference personal paths, in-flight work, or private plan files, so it will not exist on a fresh clone.

1. If it exists with content under "Active work", treat that as the user's intent for this session and pick up from there, following any plan file it points to.
2. If it exists but holds only the `(no pending work — start fresh)` sentinel, ignore it and wait for instructions.
3. If it does not exist, offer to create one from the template below, then wait for instructions. Do not populate it until the user has pending work worth handing off.

Empty template:

```markdown
# Next Steps

_(no pending work — start fresh)_
```

Populated template:

```markdown
# Next Steps

## Active work
<one paragraph describing the task, brainstorm, or plan to resume>

## Plan file (if any)
<absolute path to a plan file, or "none">

## Notes
<anything the next session needs that isn't in the plan file>
```

When a session ends with pending work, offer to update `next_steps.md` with a short handoff. When the goal was fully achieved, reset it to the empty sentinel.

## Project

Python MCP server wrapping ServiceTitan's REST API. It exposes 95 tools (count `@mcp.tool()` in `server.py`) across CRM, Jobs, Dispatch, Estimates, Invoicing, Pricebook, Inventory, Memberships, Payroll, Reporting, Forms, and Marketing. The full tool catalog and user-facing setup live in [README.md](README.md).

## ServiceTitan API docs

For any ServiceTitan API question (endpoints, auth, schemas, rate limits, webhooks), fetch docs through Context7 with this exact library ID, skipping the `ctx7 library` lookup:

```
ctx7 docs /websites/developer_servicetitan_io "<specific question>"
```

## Stack and entry point

- Python 3.10+. Dependencies: `mcp[cli]`, `httpx[socks]`, `pydantic`.
- The console script `servicetitan-mcp` runs `servicetitan_mcp.server:main` (see [pyproject.toml](pyproject.toml)).
- Transport is stdio by default; set `MCP_TRANSPORT=sse` for HTTP/SSE deployments.

## Layout

Five source files matter:

- [`servicetitan_mcp/auth.py`](servicetitan_mcp/auth.py): `TokenManager` for OAuth2 client credentials. Tokens are cached per `tenant_id` and refreshed about 14 minutes into a 15-minute lifetime.
- [`servicetitan_mcp/config.py`](servicetitan_mcp/config.py): tenant registry. Reads `ST_TENANTS` plus namespaced per-tenant env vars into `TenantCredentials` records, validates slugs as strict lowercase, and fails fast with a migration hint if the legacy single-tenant vars are set without `ST_TENANTS`.
- [`servicetitan_mcp/client.py`](servicetitan_mcp/client.py): `ServiceTitanClient`. A shared `httpx.AsyncClient`, per-tenant token buckets from the `main_limiter_for(name)` and `reporting_limiter_for(name)` factories, a process-wide concurrency semaphore, and retry with exponential backoff that honors `Retry-After`.
- [`servicetitan_mcp/report_export.py`](servicetitan_mcp/report_export.py): path resolution and CSV/JSONL serialization for `run_report_to_file`.
- [`servicetitan_mcp/server.py`](servicetitan_mcp/server.py): the `FastMCP` instance, every `@mcp.tool()` handler, the `_fmt()` pagination formatter, the `_get_client(tenant)` and `_resolve(tenant)` factories, and the `list_tenants` discovery tool.

Tests live in [`tests/`](tests/) (pytest-asyncio) and cover the token bucket, retry and concurrency, pagination, input coercion, config, and the tool surfaces.

## Commands

- Install: `pip install -e .`
- Run: `python -m servicetitan_mcp.server` (or the `servicetitan-mcp` script)
- Test: `pytest` (`pytest tests/test_pagination.py` targets one file)
- No linter or formatter is configured. Do not invent one.

## Required env vars

Multi-tenant; configure one or more tenants:

- `ST_TENANTS`: comma-separated tenant slugs (lowercase, `^[a-z][a-z0-9_-]*$`).
- For each slug `<NAME>` (uppercased in the key): `ST_TENANT_<NAME>_ID`, `ST_TENANT_<NAME>_CLIENT_ID`, `ST_TENANT_<NAME>_CLIENT_SECRET`, `ST_TENANT_<NAME>_APP_KEY`.

The legacy single-tenant vars (`ST_APP_KEY`, `ST_CLIENT_ID`, `ST_CLIENT_SECRET`, `ST_TENANT_ID`) are no longer read. If they are present without `ST_TENANTS`, startup raises a `RuntimeError` with a migration hint.

Optional tuning, defaults in parentheses: `ST_RATE_LIMIT_RPS` (30, per tenant), `ST_REPORTING_RPM` (3, per tenant), `ST_MAX_CONCURRENCY` (10, process-wide), and `ST_OUTPUTS_DIR`, the default output directory for `run_report_to_file` (unset, exports land in the gitignored `report_exports/`). The export tool streams pages to a `.partial` file and `os.replace`s it only on success.

## Adding a new tool

Follow the existing pattern. `list_customers` in [server.py](servicetitan_mcp/server.py) is a clean reference:

```python
@mcp.tool()
async def list_<resource>(tenant: str, page: int = 1, page_size: int = 200, ...) -> str:
    """Short purpose. When to use: ... When NOT: ...

    tenant: name of a configured ServiceTitan tenant (call list_tenants)
    """
    client = _resolve(tenant)
    params: dict = {}
    if some_filter:
        params["someFilter"] = some_filter
    data = await client.list_resource("<category>", "<resource>", page, page_size, params)
    return _fmt(data)
```

- `tenant: str` is always the first parameter, and the docstring always ends with the `tenant: name of a configured ServiceTitan tenant (call list_tenants)` line. FastMCP derives the tool schema from the signature, and that docstring line is how the LLM knows to call `list_tenants` first.
- Use `_resolve(tenant)` rather than `_get_client` directly. It translates `UnknownTenantError` into a friendly LLM-facing message listing the configured tenants.
- Use `client.list_resource()` and `client.get_resource()`; they handle auth, rate limits, retries, and pooling. Do not build your own request.
- Return `_fmt(data)` for every listing. It appends the pagination footer the LLM needs to decide whether to fetch more.
- Include "when to use" and "when NOT" guidance in the docstring; existing tools model this.
- For non-standard endpoints use `client.get/post/patch/put`, or the `servicetitan_api_call` escape hatch for fully ad-hoc paths.

## Gotchas and invariants

- **The reporting API is aggressively throttled** (3 rpm versus 30 rps for the main API). Reporting calls block each other, so do not parallelize them.
- **The pagination footer is load-bearing.** `_fmt()` appends `page/pageSize/totalCount/hasMore`, inferring `hasMore` when ServiceTitan omits it. A silent 25-item cap bug was fixed in commit `169bce0`; [`tests/test_pagination.py`](tests/test_pagination.py) is the regression test. Do not bypass `_fmt()`.
- **Errors surface on purpose.** After the final retry, `client.py` raises with the first 2000 characters of the response body so the LLM can see the reason. Do not swallow exceptions in tool handlers; let them propagate to MCP.
- **Token refresh is automatic.** Tool code never touches `TokenManager` directly.
- **One shared HTTP client.** Do not create `httpx.AsyncClient` instances inside handlers; go through `_resolve(tenant)`.
- **Per-tenant rate limiters.** `main_limiter_for(name)` and `reporting_limiter_for(name)` return per-tenant singletons. The process-wide concurrency semaphore is separate and shared across tenants; it guards local fan-out, not ServiceTitan quota.
- **No silent default tenant.** Never add a fallback that picks a tenant when `tenant` is absent. A silent wrong-tenant answer is worse than any UX inconvenience.

## Git remote

`origin` is `github.com/HoffmannBros/servicetitan-mcp`; push there. The glassdoc upstream is not configured as a remote in this clone.

## Where to look next

- [`README.md`](README.md): end-user setup and the full tool catalog
- [`claude_desktop_config_example.json`](claude_desktop_config_example.json): Claude Desktop config template
