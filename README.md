# ServiceTitan MCP Server

Connect Claude to ServiceTitan via API. 97 tools covering CRM, Jobs, Accounting, Estimates, Dispatch, Pricebook, Inventory, Memberships, Payroll, Marketing, Reporting, Timesheets, Telecom, Customer Interactions, and more.

Multi-tenant: one server instance can query any number of ServiceTitan tenants on a per-call basis, including cross-tenant aggregation.

## Credits

This project is a fork of [glassdoc/servicetitan-mcp](https://github.com/glassdoc/servicetitan-mcp), created by the [glassdoc](https://github.com/glassdoc) team. All credit for the original design and the bulk of the implementation goes to them. This fork adds fixes for production auth, improved error surfacing, a corrected `run_report` endpoint, and multi-tenant support.

## Quick Start

```bash
# Clone and install
git clone https://github.com/HoffmannBros/servicetitan-mcp.git
cd servicetitan-mcp
pip install -e .
```

## Install as a Claude Desktop extension (.mcpb)

The easiest way to use this server — no Python, pip, or git required. Claude Desktop
manages the runtime and dependencies automatically (via its bundled uv).

1. Download `servicetitan-mcp-<version>.mcpb` from the
   [latest release](https://github.com/HoffmannBros/servicetitan-mcp/releases/latest)
   (or build it yourself — see [Building the bundle](#building-the-mcpb-bundle)).
2. Double-click it, or drag it into **Claude Desktop → Settings → Extensions**.
3. Fill in **Tenant 1** (all five fields are required):
   - **Name** — a short lowercase nickname (e.g. `acme`); you'll use it when talking
     to Claude ("list open jobs for acme").
   - **Tenant ID / Client ID / Client Secret / App Key** — see
     [Getting Your Credentials](#getting-your-credentials).
4. Have more than one ServiceTitan tenant? Save Tenant 1 first, then reopen the
   extension's settings and fill in Tenants 2–4 the same way, at most two per
   save. Otherwise leave them blank. (Claude Desktop rejects a save that changes
   more than 12 settings and shows "error saving".)
5. Optionally change the **Report export folder** (where `run_report_to_file`
   saves CSVs; defaults to `Documents/ServiceTitan Reports`).

Secrets (client secret, app key) are stored in your OS keychain, not on disk.
The first launch downloads Python dependencies, so it needs a network connection
and can take a minute; later launches are instant. Settings survive upgrades —
installing a newer `.mcpb` keeps your credentials.

## Configuration

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, or `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "servicetitan": {
      "command": "python",
      "args": ["-m", "servicetitan_mcp.server"],
      "env": {
        "ST_TENANTS": "acme,other",

        "ST_TENANT_ACME_ID": "your-tenant-id",
        "ST_TENANT_ACME_CLIENT_ID": "cid.your-client-id",
        "ST_TENANT_ACME_CLIENT_SECRET": "cs1.your-client-secret",
        "ST_TENANT_ACME_APP_KEY": "your-app-key",

        "ST_TENANT_OTHER_ID": "your-tenant-id",
        "ST_TENANT_OTHER_CLIENT_ID": "cid.your-client-id",
        "ST_TENANT_OTHER_CLIENT_SECRET": "cs1.your-client-secret",
        "ST_TENANT_OTHER_APP_KEY": "your-app-key"
      }
    }
  }
}
```

### Env var schema

- `ST_TENANTS` — comma-separated list of tenant names. Names must be lowercase and match `^[a-z][a-z0-9_-]*$`. These are the names the LLM uses in tool calls, so pick slugs that are recognizable when phrased naturally ("st_louis" rather than "t1").
- For each name in `ST_TENANTS`, set four namespaced vars where `<NAME>` is the uppercase form of the slug:
  - `ST_TENANT_<NAME>_ID` — numeric ServiceTitan tenant id (used in API URLs).
  - `ST_TENANT_<NAME>_CLIENT_ID` — OAuth2 Client ID.
  - `ST_TENANT_<NAME>_CLIENT_SECRET` — OAuth2 Client Secret.
  - `ST_TENANT_<NAME>_APP_KEY` — `ST-App-Key` header value from the Developer Portal.

If one ServiceTitan app is authorized against multiple tenants, the `CLIENT_ID` / `CLIENT_SECRET` / `APP_KEY` will be identical across tenants and only `ID` differs. If each tenant has its own app registration, all four vary. The schema supports both.

For a complete ready-to-edit example, see [`claude_desktop_config_example.json`](claude_desktop_config_example.json).

## Getting Your Credentials

For each tenant:

1. Go to **Settings > Integrations > API Application Access** in that ServiceTitan tenant.
2. Find the **Claude MCP Integration** app and click **Connect**.
3. Fill in the restriction fields (booking_provider, gps_provider, report_category).
4. Accept Terms and Conditions.
5. Copy your **Client ID** and generate a **Client Secret**.
6. Your **Tenant ID** is shown in the top-right corner of any ServiceTitan page.
7. Your **App Key** is provided by ServiceTitan when your integration app is registered — obtain it directly from your own ServiceTitan account/integration setup.

## Multi-Tenant Usage

### The `tenant` argument

Every tool takes a required `tenant: str` as its first argument naming one of the configured tenants. There is no default — calling any tool without `tenant` raises a schema error from FastMCP. This is intentional: a silent wrong-tenant answer ("most recent job" on the wrong business) is worse than any UX inconvenience.

### Discovery: `list_tenants`

A new no-arg tool, `list_tenants`, returns the configured tenant names. The LLM typically calls this first when the user names a business by name, so it can map phrasing → tenant slug correctly.

### Cross-tenant queries

The server itself is always single-tenant per call. Cross-tenant aggregation is LLM-orchestrated: when the user asks something like *"total jobs booked across all businesses last month"*, Claude fans out — one tool call per tenant — and aggregates the results in its reply. No server-side join, no cross-tenant auth, no shared rate-limiter contention.

Practically, this means:

- **Per-tenant rate limiting is independent.** The 30 rps main / 3 rpm reporting limits apply *per tenant*, so four tenants have 4× the headroom of a single tenant in burst scenarios.
- **OAuth tokens cache per tenant.** The `TokenManager` is keyed on `tenant_id`, so sequential cross-tenant use does not invalidate any one tenant's token.
- **Concurrent cross-tenant fan-out works.** The process-wide concurrency semaphore (`ST_MAX_CONCURRENCY`, default 10) caps total parallelism, not per-tenant.

Example flows:

- *"Most recent job in St. Louis"* → single `list_jobs(tenant="st_louis", ...)` call.
- *"Show me revenue by business last month"* → Claude calls `list_invoices(tenant="...", created_on_or_after=...)` once per configured tenant and sums.

### Unknown tenant errors

Calling a tool with a tenant slug that isn't configured returns a structured error listing the valid names:

```
Unknown tenant 'foo'. Configured: acme, other. Call list_tenants for the authoritative list.
```

## Migrating from single-tenant

If you're upgrading from a pre-multi-tenant version of this server, your previous env vars (`ST_APP_KEY`, `ST_CLIENT_ID`, `ST_CLIENT_SECRET`, `ST_TENANT_ID`) are no longer read. On startup with the old config you'll see:

```
RuntimeError: ST_TENANTS is not set, but legacy single-tenant vars are
present: ST_TENANT_ID, ST_APP_KEY, ST_CLIENT_ID, ST_CLIENT_SECRET. This
server is now multi-tenant. Set ST_TENANTS=<comma-separated names> and
namespaced vars ST_TENANT_<NAME>_ID / _CLIENT_ID / _CLIENT_SECRET /
_APP_KEY per tenant. See README for the full migration.
```

Migration takes ~5 minutes per tenant: rename the four old vars with an `ST_TENANT_<NAME>_` prefix, and add `ST_TENANTS=<name>` naming that tenant. To add additional tenants, repeat the four-var block with new names and extend `ST_TENANTS`.

## Available Tools (96 + `list_tenants`)

Every tool below takes `tenant: str` as its first required argument. See "Multi-Tenant Usage" above.

### CRM
- `list_customers` — Search and list customers
- `get_customer` — Get customer details by ID
- `list_locations` — List service locations; filter by street, unit, city, state, zip, country, name (case-insensitive "contains" matches), customer, or a batch of `ids`
- `get_location` — Get location details
- `list_leads` — List leads/opportunities (status: Open, Dismissed, Converted)
- `get_lead` — Get lead details by ID
- `list_bookings` — List bookings (no status filter: ServiceTitan has none, so passing `status` raises)
- `get_booking` — Get booking details by ID
- `list_contacts` — List contacts; with `customer_id`, reads that customer's own contact list
- `list_customer_notes` — List a customer's free-text notes
- `list_location_notes` — List a location's free-text notes
- `list_location_contacts` — List a location's own contacts (distinct from the customer's)
- `list_customer_custom_field_types` — Customer custom-field definitions (the catalog, not per-record values)
- `list_location_custom_field_types` — Location custom-field definitions

### Job Planning & Management
- `list_jobs` — List jobs: status, customer, closed created/completed/modified date windows (`*_on_or_after` inclusive, `*_before` exclusive), `job_number`, business unit, job type, campaign, technician, project, or `ids` (alone)
- `get_job` — Get full job details
- `list_job_notes` — List a job's free-text notes
- `get_job_history` — Full job lifecycle event timeline (booked/dispatched/completed/…)
- `list_job_cancel_reasons` — Cancellation-reason definitions (`active_only` filter)
- `list_job_hold_reasons` — Hold-reason definitions (`active_only` filter)
- `list_appointments` — List appointments by start window (prefer `starts_before`, exclusive), created/modified window, or `job_id`
- `get_appointment` — Get appointment details by ID
- `list_job_types` — List configured job types
- `get_job_type` — Get one job-type definition by ID
- `list_projects` — List projects
- `get_project` — Get project details

### Accounting
- `list_invoices` — List invoices (filter by job, customer, date)
- `get_invoice` — Get invoice with line items
- `list_payments` — List payments received
- `list_payment_types` — List payment types
- `list_inventory_bills` — List AP/inventory bills
- `list_journal_entries` — List journal entries
- `list_payment_terms` — List payment terms
- `list_tax_zones` — List tax zones

### Sales & Estimates
- `list_estimates` — List estimates (filter by job, status, sold date)
- `get_estimate` — Get estimate with line items

### Dispatch
- `list_appointment_assignments` — Technician-to-appointment assignments, by `job_id` or `appointment_id` (the endpoint has no start-time filter)
- `list_technician_shifts` — Shift schedules; window with `starts_on_or_after` and `ends_on_or_before` (shifts have no start upper bound)
- `get_technician_shift` — Get one shift by ID
- `list_zones` — Dispatch zones
- `list_non_job_appointments` — Meetings, training, etc.
- `get_non_job_appointment` — Get one non-job appointment by ID
- `list_arrival_windows` — Bookable arrival-window templates
- `list_teams` — Dispatch teams (technician groupings)

### Pricebook
- `list_pricebook_services` — Sellable services
- `list_pricebook_materials` — Materials
- `list_pricebook_equipment` — Equipment
- `list_pricebook_categories` — Categories

### Inventory
- `list_purchase_orders` — Purchase orders
- `get_purchase_order` — Get one purchase order (with line items) by ID
- `list_warehouses` — Warehouses
- `list_inventory_vendors` — Vendors/suppliers
- `list_trucks` — Vehicle inventory
- `list_inventory_adjustments` — Manual stock corrections (with line items)
- `list_inventory_transfers` — Stock moved between locations/trucks
- `list_inventory_receipts` — Items received against POs
- `list_inventory_returns` — Items returned to vendors for credit

The four inventory feeds and `list_technician_ratings` take `created_before` (exclusive); the older `created_on_or_before` still works and keeps the named day included.

### Customer Interactions
- `list_technician_ratings` — Customer satisfaction scores per tech/job

### Memberships
- `list_memberships` — Customer memberships
- `get_membership` — Get one customer membership by ID
- `list_membership_types` — Membership type definitions
- `list_recurring_services` — Recurring services
- `get_recurring_service` — Get one recurring service by ID

### Settings
- `list_tenants` — Configured tenant names (no `tenant` arg)
- `list_employees` — Employees
- `get_employee` — Employee details
- `list_technicians` — Technicians
- `get_technician` — Get one technician by ID
- `list_business_units` — Business units
- `list_tag_types` — Tag types
- `list_user_roles` — User roles

### Reporting
- `list_report_categories` — Report categories
- `list_reports_in_category` — Reports in a category (by category slug)
- `get_report` — One report's parameter schema + output fields (by category slug + id)
- `get_report_parameter_values` — Resolve a parameter's allowed values (dynamic value set)
- `run_report` — Run a dynamic report (returns rows inline; for small/interactive pulls)
- `run_report_to_file` — Auto-paginate a whole report and write it to a CSV/JSONL file, returning only metadata (for reports too large to return inline)

### Payroll
- `list_payrolls` — Payroll runs
- `list_employee_payrolls` — Employee payroll details
- `list_gross_pay_items` — Gross pay line items

### Telecom
- `list_calls` — Phone calls (prefer `created_before`, exclusive, for the upper bound)
- `get_call` — Get one call (with recording links) by ID

### Forms
- `list_forms` — Form templates
- `list_form_submissions` — Submitted forms (the date filter is on the submitted date)
- `list_job_attachments` — Files attached to a job (metadata only; ServiceTitan returns no image URL)
- `download_job_photos` — Download a job's photos to disk, for one job or every job completed in a date range. Field pro photos by default; files are named `{tenant}__{business unit}__{job type}__{job number}__{completed date}__{seq}_{attachment id}.{ext}` and logged to `manifest.jsonl`

### Marketing
- `list_campaigns` — Marketing campaigns
- `list_campaign_costs` — Campaign costs
- `list_reviews` — Online reviews from Marketing Reputation (rating, text, matched job/technician), filterable by publish or import date, rating range, technicians, campaigns, locations, and source

Attributed leads (`/marketingads/v2/.../attributed-leads`) are not supported: the endpoint needs ServiceTitan's Marketing Pro package and returns 403 on every configured tenant.

### Equipment, Tasks, Timesheets
- `list_installed_equipment` — Installed equipment at locations; `location_ids` fetches a whole batch of locations in one call
- `list_tasks` — Task management tasks
- `list_activities` — Timesheet activities
- `list_activity_categories` — Activity categories

### Power User
- `export_feed` — Bulk-export an entire feed (customers, jobs, invoices, …) via continuation tokens; cheap on quota for full-table syncs and incremental change pulls. Returns one page at a time.
- `export_feed_to_file` — Walk a whole export feed (or everything since a date) to a JSONL or CSV file and return only metadata plus a short preview and the token to resume from. Use it for anything over a page or two.
- `servicetitan_api_call` — Make any arbitrary API call (GET/POST/PATCH/PUT; `query_params` are sent with every method). API errors surface as tool errors with the response body.

Static lookups (`get_lookup_tables` and the `servicetitan://lookups/{tenant}/{kind}` resource) now include `call_reasons`, the Job Booking reasons a CSR picks for a call that did not book.

## Authentication

Uses OAuth 2.0 Client Credentials flow. Tokens auto-refresh every 15 minutes. Tokens cache per `tenant_id`, so cross-tenant use does not invalidate each other. No user interaction required.

## Common Workflows

Recipes for frequent questions, with the tool sequence Claude should pick. Every tool takes `tenant` as the first argument — omitted below for brevity, but always required.

### Find a customer by name (then drill down)
1. `list_customers(tenant=..., name="Smith")` → returns candidates with IDs, contact blocks, primary location.
2. Optional `get_customer(tenant=..., customer_id=...)` only if you need custom fields not in the list response.

### Find a customer by phone or email
The `list_customers` endpoint has no phone/email filter. Two options:
- `servicetitan_api_call(tenant=..., method="GET", path="/crm/v2/tenant/{tenant_id}/customers", query_params='{"phone":"555-0100"}')` — works if ST exposes `phone` on this tenant.
- Fallback: `list_contacts` with date range and match locally.

### Full job timeline for a customer
1. `list_customers(tenant=..., name=...)` → customerId
2. `list_jobs(tenant=..., customer_id=...)` → jobIds (status, dates)
3. For a specific job: `list_appointments(tenant=..., job_id=...)`, or `get_job(tenant=..., job_id=...)` for full detail
4. `list_invoices(tenant=..., job_id=...)` for billing side
5. `list_estimates(tenant=..., job_id=...)` if a quote exists

### Revenue for a date range
- Simple total: `list_invoices(tenant=..., created_on_or_after="2024-01-01")` — sum client-side.
- Broken down by Business Unit or campaign: use `run_report` (reporting quota applies, slower). Discover the report first via `list_report_categories` → `list_reports_in_category(category)` (the category arg is the string slug, e.g. `"operations"`), or jump straight to `get_report(category, report_id)` if you already know the id. Then resolve any parameter that has an `acceptValues.dynamicSetId` (e.g. a "Filter by"/DateType code) with `get_report_parameter_values(dynamicSetId)`, and finally call `run_report`.

### Cross-tenant aggregation
- *"Revenue across all businesses last month"* → call `list_invoices` once per tenant from `list_tenants`, sum client-side.
- *"Which business has the most open estimates right now?"* → call `list_estimates(status="Open")` once per tenant, compare counts.

Claude orchestrates the fan-out; there's no server-side aggregation tool and no need to call `list_tenants` in the query path if the tenant set is already known.

### Technician's schedule today
1. `list_appointments(tenant=..., starts_on_or_after="2024-04-22", starts_before="2024-04-23")` → appointment IDs for the day.
2. `list_appointment_assignments(tenant=..., appointment_id=...)` per appointment (the assignments endpoint has no date filter).
3. Cross-reference with `list_technicians(tenant=...)` (cache this — small and static) to map IDs → names.

### Open estimates by business unit
1. `list_business_units(tenant=...)` — cache the BU ID → name map.
2. `list_estimates(tenant=..., status="Open")` — note `businessUnitId` per estimate, group client-side.

### Membership renewals due this month
1. `list_memberships(tenant=..., status="Active")` — look at `nextScheduledDate` / `to` expiry.
2. For the tune-up visits themselves: `list_recurring_services(tenant=...)`.

### Marketing channel ROI
1. `list_campaigns(tenant=...)` — cache campaign ID → name.
2. `list_campaign_costs(tenant=..., campaign_id=...)` for spend.
3. `list_invoices(tenant=..., created_on_or_after=...)` then group by each job's campaignId for revenue attribution.

## Rate limits and error handling

ServiceTitan enforces **per app per tenant**:
- **Main API:** 60 requests/second.
- **Reporting API:** 5 requests/minute per report.

This server layers the following in front of those limits:

- **Per-tenant token buckets.** Each tenant gets its own pair (main + reporting) — they don't share quota. Four tenants have 4× the aggregate burst headroom of a single tenant. Defaults: `30 rps` main, `3 rpm` reporting (half the hard caps, leaving room for bursts). Override via:
  - `ST_RATE_LIMIT_RPS` (default `30`)
  - `ST_REPORTING_RPM` (default `3`)
  - `ST_MAX_CONCURRENCY` (default `10`) — process-wide cap on in-flight requests across all tenants
  - `ST_REPORT_QUERY_TIMEOUT_S` (default `600`) — how long `run_report` / `run_report_to_file` poll one page of an async report query before cancelling it
  - `ST_OUTPUTS_DIR` (optional) — default directory for `run_report_to_file` and `export_feed_to_file` output, and the parent of the `job_photos/` folder that `download_job_photos` writes to; falls back to the in-repo `report_exports/` if unset. Must be readable by whatever consumes the file (e.g. your sandbox/file tools).
- **Async report queries.** ServiceTitan deprecated the synchronous report `/data` endpoint in ST-78 (July 2026). `run_report` and `run_report_to_file` use `POST .../data/query`: small reports come back inline, and longer ones return a token that the server polls at `data-queries/{token}` (polls count against the reporting bucket). A query that runs past `ST_REPORT_QUERY_TIMEOUT_S` is cancelled. If a tenant returns 404 for `/data/query`, the server falls back to the legacy `/data` endpoint.
- **Automatic retry** on `429`, `502`, `503`, `504`:
  - Honors `Retry-After` header if the server sends one.
  - Otherwise exponential backoff: 1s → 2s → 4s, max 3 retries.
  - On final failure, the HTTP error (including response body) is raised so Claude sees what went wrong.
- **Shared connection pool**: one `httpx.AsyncClient` is reused across all tool calls and all tenants to avoid per-request TCP handshakes. Pooling is per-host; ST is one host, so splitting per tenant would gain nothing.

Retries log to stderr at the `[servicetitan-mcp]` prefix — tail the Claude Desktop logs to observe backoff behavior when investigating slowness.

## Live smoke check

The unit tests run against a mocked transport, so they cannot catch a wrong URL slug or a filter name ServiceTitan ignores. Before each release, run the live smoke check against at least one tenant (it needs the same `ST_TENANTS` / `ST_TENANT_<NAME>_*` env vars the server uses):

```bash
python scripts/smoke_live.py <tenant>
```

It calls every list tool that needs only a tenant, with `page_size=1`, one at a time and read-only, and prints PASS/FAIL per tool with no response bodies. A 403/409 that says a feature is not enabled on the tenant (for example Leads or CRM Contacts) prints NOT-ENABLED and does not fail the run. It exits 1 if any tool fails. Tools that need arguments are listed in `SKIPPED` inside the script, and `tests/test_smoke_registry.py` fails if a new tool is in neither list.

## Building the .mcpb bundle

For maintainers who want to distribute the server as a Claude Desktop extension:

```bash
npm install -g @anthropic-ai/mcpb   # once

mcpb validate manifest.json
mcpb pack . dist/servicetitan-mcp-<version>.mcpb
```

`.mcpbignore` keeps private and dev-only files (`.env`, `next_steps.md`,
`report_exports/`, `tests/`, …) out of the archive — the pack output lists every
included file; it should be exactly `manifest.json`, `pyproject.toml`, `README.md`,
`LICENSE`, and the eight `servicetitan_mcp/*.py` modules (12 files).

Before sharing, always run the secret pre-flight:

```bash
unzip -l dist/servicetitan-mcp-<version>.mcpb        # eyeball the file list
unzip -p dist/servicetitan-mcp-<version>.mcpb | grep -aE "cs1\.|cs2\."  # only README placeholders may match
```

Keep `version` in `manifest.json` and `pyproject.toml` in lockstep. The bundle's
settings form maps to `ST_TENANT_SLOT1..4_*` env vars (see `config.py`); the
classic `ST_TENANTS` scheme is unaffected and takes precedence when set.

## License

MIT — see [LICENSE](LICENSE). Copyright is held jointly by the original
[glassdoc](https://github.com/glassdoc) authors and the maintainers of this fork.
