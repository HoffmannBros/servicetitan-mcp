# Tool Surface Consolidation — Design Notes

_Status: whiteboard / discussion only. No code written. Captured 2026-06-16 to resume later._

## TL;DR

The server has grown to **88 tools** (CLAUDE.md/README still say "~60"). The worry is
bloat. After analysis: the *code* isn't bloated (81 of 88 are near-identical thin wrappers),
the **interface** is — too many low-distinctiveness, confusable tool schemas all loaded
eagerly into every consumer's context.

Recommended direction: **don't shrink capability, restructure the surface.** Collapse two
structurally-uniform families into generic accessors. Disciplined result is **88 → ~54**,
losing *zero* capability, with most of the work being deletion (one accessor already exists).

---

## Why this matters (and why it matters MORE here)

This server is **shipped** — coworkers (field managers, ops teams) `pip install` and run it
locally as a way to "chat with our ServiceTitan instance." Two consequences:

1. **No progressive disclosure available to us.** Per MCP's current official guidance
   (modelcontextprotocol.io "Client Best Practices", spec 2025-11-25), the recommended fix
   for tool sprawl is *progressive discovery* — a host-provided `search_tools` meta-tool that
   loads schemas on demand. **This is a HOST/CLIENT responsibility, not something a stdio
   server can implement.** Off-the-shelf Claude Desktop loads `tools/list` eagerly today. So
   all 88 schemas (~15–25k tokens) land in every consumer's context, every turn, and we can't
   design our way out of that server-side.
2. **The only server-side levers the spec endorses are: (a) keep the surface tight, and
   (b) group/name tools so the model can reason about them.** That's exactly what this plan does.

The official guidance also frames the problem in our exact words:
> "Loading every tool definition into the model's context window upfront wastes tokens,
> increases latency, and degrades model performance."
> "Group tools by ... related capabilities" / progressive discovery "improves tool selection
> accuracy by focusing the model on relevant tools."

Forward-looking hedge: these are internal users on (likely) one standardized client, so we
have more client control than a public shipper. If Claude Desktop (or our chosen client) ever
ships `search_tools`, the token problem largely dissolves — which is an argument *against*
deleting capability prematurely, and *for* the "collapse, don't delete" approach below.

---

## Guiding philosophy (the decisions we landed on)

1. **This is a learning instrument.** "The more coworkers use it, the more we learn what's
   possible." So breadth has real option value — aggressively *deleting* the long tail would
   foreclose undiscovered use cases. Goal is: **reduce eager schema surface WITHOUT reducing
   capability or discoverability.** Those are separable.

2. **Three-tier model:**
   - **Tier 1 — typed, first-class tools.** Proven, carry real filters + when-to-use docstrings.
   - **Tier 2 — generic accessors at ~1 schema each** that preserve full capability:
     `get_record(resource, id)`, `get_lookup_tables(kinds)` / reference accessor, and the
     existing `servicetitan_api_call` escape hatch.
   - **Promotion pipeline:** when usage shows a Tier-2 access pattern is common, *promote* it
     to a typed Tier-1 tool. "Escape-hatch usage → noticed pattern → paved road."

3. **Collapse on the right axis: collapsibility ≠ frequency.** Two independent axes:
   - *Frequency* (head vs tail) — how often used.
   - *Collapsibility* (structural uniformity) — does it fit a generic accessor cleanly?
   The collapse targets are tools that are **structurally uniform** (by-id fetches; paramless
   reference reads), regardless of frequency. A *rarely-used* tool that carries real filters
   (e.g. `list_journal_entries`, `list_gross_pay_items`) stays Tier 1 — collapsing it would
   throw away its typed filters. So "88 → ~54" falls out of *structure*, not a popularity cutoff.

4. **Splitting into multiple smaller MCP servers was considered and REJECTED.** Users want one
   "chat with all of ServiceTitan" surface, so they'd connect *all* sub-servers at once →
   identical token cost, plus 4× the config/auth/tenant-wiring burden and loss of cross-domain
   tools. Splitting only helps if users connect a *subset*, which fights the discovery mission.

---

## Why generic accessors stay discoverable (not lossy)

Two mechanisms keep the collapse safe rather than brittle:

1. **The enum lives in the docstring prose.** The model reading `get_record`'s schema sees the
   full `resource:` list inline — it knows `get_record(resource="invoice", id=…)` exists as
   well as it knew `get_invoice` existed. Discovery surface preserved; 18 schemas → 1.
2. **Unknown-value path echoes the valid set** (already done in `get_lookup_tables`:
   `"valid_kinds": sorted(...)`). A wrong guess like `resource="po"` returns the legal values
   and the model self-corrects in one round trip. Error-as-discovery = robust generic accessors.

Daily-driver feel: a reference lookup (`list_reference_data(kind="job_hold_reasons")` vs old
`list_job_hold_reasons`) is **identical output, identical latency, one round trip** — the field
manager notices nothing. Selection actually gets *easier* (pick from one enumerated list vs
15 confusable top-level names). Genuinely-unwrapped asks (writes, exotic endpoints) were
*always* escape-hatch territory — collapse doesn't change that path.

---

## The buckets (all 88, by the rule)

### Bucket A — COLLAPSE: get-by-id family → one `get_record(resource, id)`  (−17)
18 structurally identical "fetch one record by id" tools. Biggest, cleanest, safest collapse
(by-id fetch needs almost no per-resource docstring). Tools removed:
`get_customer, get_location, get_lead, get_booking, get_job, get_appointment, get_job_type*,
get_project, get_invoice, get_estimate, get_technician_shift, get_non_job_appointment,
get_purchase_order, get_membership, get_recurring_service, get_employee, get_technician,
get_call`.
(*`get_job_type` → fold into reference accessor instead; job_type is reference config, not a record.)

### Bucket B — COLLAPSE: reference/decoder tables → existing accessor  (≈ −17)
**Half-built already.** `get_lookup_tables(kinds=[...])` + `_LOOKUP_KINDS` registry + a cached
`@mcp.resource` mirror ALREADY exist and already cover: `business_units, job_types, zones,
warehouses, payment_types, tax_zones, payment_terms, membership_types, tag_types,
activity_categories, pricebook_categories, inventory_vendors, trucks, user_roles, campaigns`.
We currently ship BOTH `list_business_units` AND `get_lookup_tables(kinds=["business_units"])`
— that redundancy is part of the bloat. So Bucket B is **mostly deletion**:
- **Remove** the ~13 individual `list_*` reference tools already covered by `_LOOKUP_KINDS`.
- **Add 4 registry lines** for stragglers not yet in it: `job_cancel_reasons`,
  `job_hold_reasons`, `customer_custom_field_types`, `location_custom_field_types`.
- **Net: ~17 tools gone, ZERO new tools.**
- ⚠️ Tradeoff: `get_lookup_tables` takes no per-kind params, so `active_only` on
  hold/cancel reasons is lost. Fine for tiny static tables; note it in the docstring.
  (Note: `campaigns` is in `_LOOKUP_KINDS` but `list_campaigns` is really an entity list —
  see Bucket D. Minor existing semantic blend; leave as-is.)

### Bucket C — KEEP Tier 1: filtered list tools (34)
Anything with real filters earns its schema, frequent or not:
`list_customers, list_locations, list_leads, list_bookings, list_contacts, list_customer_notes,
list_location_notes, list_location_contacts, list_jobs, list_job_notes, list_projects,
list_invoices, list_payments, list_inventory_bills, list_journal_entries, list_estimates,
list_appointments, list_appointment_assignments, list_technician_shifts,
list_non_job_appointments, list_pricebook_services, list_pricebook_materials,
list_pricebook_equipment, list_purchase_orders, list_memberships, list_employees,
list_technicians, list_calls, list_form_submissions, list_campaign_costs,
list_installed_equipment, list_activities, list_employee_payrolls, list_gross_pay_items`.
(Note `list_*_notes` x3 take only an id and *could* collapse to `list_notes(entity, id)` for
another −2, but it's a smaller, more debatable win — defer.)

### Bucket D — KEEP Tier 1: paramless ENTITY lists (6)
Paramless *today* but they're records (will grow filters), not decoder tables — wrong to push
to Door A: `list_recurring_services, list_payrolls, list_forms, list_campaigns, list_tasks,
list_job_types`. (`list_job_types` is the borderline one — reference-ish; could go to Bucket B.)

### Bucket E — KEEP: workflow / escape / bulk (~10)
`list_report_categories, list_reports_in_category, get_report, get_report_parameter_values,
run_report, run_report_to_file, get_job_history` (sub-resource, non-standard shape — not a
by-id fetch), `export_feed`, `servicetitan_api_call`, `get_lookup_tables`, `list_tenants`.

### Net
| Change | Δ |
|---|---|
| Bucket A: 18 `get_*(id)` → 1 `get_record` | −17 |
| Bucket B: delete ~13 redundant reference lists + 4 registry lines (+ optional 4 stragglers) | −13 to −17 |
| **88 → ~54** (≈52 if also dropping the 4 straggler list tools) | |

To push to ~40 you'd have to collapse *filtered* families (e.g. the 4 scheduling tools
`appointments/appointment_assignments/technician_shifts/non_job_appointments` into one, or the
pricebook trio) — that starts trading away filter clarity. **~54 is the "lose nothing" floor;
40 has a cost.** Earlier "88 → 40" was optimistic; the rule-based number is ~54.

---

## Sketch: `get_record` (the only genuinely NEW tool)

```python
# registry next to _LOOKUP_KINDS
_RECORD_RESOURCES: dict[str, tuple[str, str]] = {
    "customer":            ("crm", "customers"),
    "location":            ("crm", "locations"),
    "lead":                ("crm", "leads"),
    "booking":             ("crm", "bookings"),
    "job":                 ("jpm", "jobs"),
    "appointment":         ("jpm", "appointments"),
    "project":             ("jpm", "projects"),
    "invoice":             ("accounting", "invoices"),
    "estimate":            ("sales", "estimates"),
    "purchase_order":      ("inventory", "purchase-orders"),
    "membership":          ("memberships", "memberships"),
    "recurring_service":   ("memberships", "recurring-services"),
    "employee":            ("settings", "employees"),
    "technician":          ("settings", "technicians"),
    "technician_shift":    ("dispatch", "technician-shifts"),
    "non_job_appointment": ("dispatch", "non-job-appointments"),
    "call":                ("telecom", "calls"),
    # job_type intentionally NOT here — it's reference, goes in get_lookup_tables
}

@mcp.tool()
async def get_record(tenant: str, resource: str, id: int) -> str:
    """Fetch one full record by numeric ID, for any core entity.

    When to use: you already have an ID (from a `list_*` result, an invoice,
    a job link) and need the complete record — contact blocks, custom fields,
    linked IDs not present in list responses.
    When NOT: if you only know a name/number, call the matching `list_*`
    tool first (e.g. `list_customers(name=...)`).

    resource: one of customer, location, lead, booking, job, appointment,
        project, invoice, estimate, purchase_order, membership,
        recurring_service, employee, technician, technician_shift,
        non_job_appointment, call.
    id: the numeric ServiceTitan ID of that record.

    tenant: name of a configured ServiceTitan tenant (call list_tenants)
    """
    client = _resolve(tenant)
    if resource not in _RECORD_RESOURCES:
        return json.dumps(
            {"error": f"unknown resource: {resource!r}",
             "valid_resources": sorted(_RECORD_RESOURCES)}, indent=2)
    category, path = _RECORD_RESOURCES[resource]
    data = await client.get_resource(category, path, id)
    return json.dumps(data, indent=2, default=str)
```

⚠️ **Verify each `(category, path)` against the live API before wiring** — the existing
per-getter tools are the source of truth for the correct resource strings; copy them exactly
(e.g. `get_estimate` currently uses whatever category/path is in its body — confirm `sales`
vs other). Don't trust the table above blindly; it was reconstructed from memory of the getters.

Bucket B needs **no new function** — just extend `_LOOKUP_KINDS` and delete the redundant
`list_*` reference tools.

---

## Open questions / judgment calls to settle next time

1. **`job_type` / `get_job_type`:** treat as reference-only (fold into `get_lookup_tables`,
   drop `get_job_type`)? Or does anyone fetch a single job type by id often enough to keep it?
2. **`list_*_notes` x3:** collapse to `list_notes(entity, id)` for −2, or leave (smaller win)?
3. **How far to push:** stop at the "lose nothing" ~54, or accept some filter-clarity cost to
   approach ~40 by collapsing scheduling/pricebook families?
4. **Instrumentation FIRST?** Strongly recommended: log which tools/endpoints actually fire
   (locally, tenant-aware). Turns head/tail + promote/demote into *data* decisions, and would
   validate the bucket boundaries empirically before any deletion. Highest-leverage small add.

---

## ⚠️ Tension with the existing expansion roadmap (reconcile before acting)

`next_steps.md` drives an **API-parity EXPANSION** roadmap (Phase 2 read coverage, Phase 3
writes) — i.e. *adding* tools. This consolidation *reduces* the surface and would partially
**supersede recent work**:
- Phase 2 slice 1 (the 11 single-record `get_*` getters, `9d319ce`) → absorbed into `get_record`.
- Phase 2 slice 3 reference tables (`job_cancel_reasons`, `job_hold_reasons`,
  custom-field-types, `eb74aa9`) → absorbed into `get_lookup_tables`.

These aren't contradictory IF framed through the promotion pipeline: keep *adding capability*,
but prefer generic accessors + escape hatch as the default intake, and promote to typed tools
based on real usage. But it does imply reworking some just-shipped slices. **Decide the overall
direction (keep expanding typed tools vs consolidate-then-promote) before touching code**, so
the two roadmaps don't fight each other.

---

## Recommended first slice (lowest risk)

1. **Instrument usage logging** (optional but recommended) — get real data on the head/tail.
2. **Bucket B deletion half** — remove the redundant reference `list_*` tools already covered
   by `_LOOKUP_KINDS`; the accessor is already proven/live. Update README catalog count + tests.
3. **Add 4 straggler kinds** to `_LOOKUP_KINDS`, then delete those 4 list tools.
4. **Build `get_record`**, verify every `(category, path)` live, delete the 18 getters, migrate
   tests. Update README.

Follow CLAUDE.md "Adding a new tool" rules; verify every endpoint path live
(`servicetitan_api_call`) before wiring — Phase 1 bugs proved doc snippets aren't reliable.
