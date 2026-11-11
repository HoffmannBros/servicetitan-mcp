# Usage-Driven Improvements: Orchestration Plan

_Written 2026-11-11. Status: not started. No code has been changed._

This plan is meant to be run by one **parent session** that hands each work
item to its own **sub-session** and coordinates the results. If you are the
parent, read the whole file. If you are a sub-session, read
[Rules for every sub-session](#rules-for-every-sub-session) and your own work
item, nothing else is required.

## Contents

1. [Why this work exists](#why-this-work-exists)
2. [What is already known](#what-is-already-known)
3. [Parent session: how to run this](#parent-session-how-to-run-this)
4. [Rules for every sub-session](#rules-for-every-sub-session)
5. [Wave 1 work items (parallel)](#wave-1-work-items-parallel)
6. [Wave 2 work items (after Wave 1 is merged)](#wave-2-work-items-after-wave-1-is-merged)
7. [Wave 3: docs and release (parent only)](#wave-3-docs-and-release-parent-only)
8. [Not in this plan](#not-in-this-plan)

---

## Why this work exists

A review on 2026-11-11 counted every ServiceTitan tool call in past Claude
session transcripts on this machine: 1,482 calls in total.

- Only 26 of the 95 tools were ever called.
- Two generic tools made up 70% of all calls: `export_feed` (614) and
  `servicetitan_api_call` (425).
- Most of those generic calls repeat a handful of patterns that a typed tool
  or one extra filter would cover.
- Two typed tools return 404 on every call because of a wrong URL slug.

The goal is to fix what is broken, turn the repeated generic-call patterns
into typed tools or filters, and add a check that would have caught the broken
tools. Capability must not shrink.

## What is already known

### Confirmed by a live call (2026-11-11, one tenant)

| Fact | Evidence |
|---|---|
| `list_installed_equipment` returns 404 | It requests `/equipment-systems/v2/tenant/{id}/installed-equipment`. |
| The working path is `/equipmentsystems/v2/tenant/{id}/installed-equipment` | Returned 200 with data. |
| `list_tasks` returns 404 | It requests `/task-management/v2/tenant/{id}/tasks`. |
| The working path is `/taskmanagement/v2/tenant/{id}/tasks` | Returned 200 with data. |

### Observed in past usage (worked at the time, not re-verified)

| Pattern | Calls | Query params seen |
|---|---|---|
| `export_feed` walked one page at a time | 614 | feeds: `jpm/jobs` 276, `crm/customers/contacts` 128, `crm/locations` 69, `crm/customers` 64, `memberships/memberships` 20, `memberships/membership-status-changes` 13 |
| Raw GET `/crm/v2/tenant/{tenant_id}/locations` | 112 | `street` (109), `zip` (2) |
| Raw GET `/equipmentsystems/v2/tenant/{tenant_id}/installed-equipment` | 100 | `locationIds` (99) |
| Raw GET `/jpm/v2/tenant/{tenant_id}/jobs` | 63 | `createdOnOrAfter` + `createdBefore` (44 across jobs and appointments) |
| Raw GET `/jpm/v2/tenant/{tenant_id}/appointments` | 34 | same pair |
| Raw GET `/marketingads/v2/tenant/{tenant_id}/attributed-leads` | 30 | `fromUtc`, `toUtc`, `leadType` |
| Raw GET `/marketing-ads/v2/tenant/{tenant_id}/attributed-leads` | 10 | probably a wrong guess at the slug; verify |
| `list_locations(customer_id=…)` called once per customer | 99 | n/a |

### Suspected, not verified

Treat each of these as a question to answer, not a fact.

- `list_installed_equipment` sends `locationId` (singular). Past raw calls used
  `locationIds`. One of them may be silently ignored.
- `list_calls` sends `createdOnOrBefore`. ServiceTitan's usual name is
  `createdBefore`. The filter may be a silent no-op.
- `list_appointments` sends `startsOnOrBefore`. ServiceTitan's usual name is
  `startsBefore`. Same risk.
- Other typed tools may have the same kind of wrong filter name. Wave 2 item
  W2-A audits all of them.

### Found by reading the code

- `servicetitan_api_call` wraps its body in `try/except Exception` and returns
  `"API Error: …"` plus a full Python traceback as a normal result. AGENTS.md
  says tool handlers must let exceptions propagate.
- `servicetitan_api_call` ignores `query_params` for POST, PATCH and PUT. The
  client's `post`, `patch` and `put` methods take no `params` argument.
- `client.get`, `post`, `patch` and `put` accept a `timeout` argument and never
  use it.
- `_fmt` and `_fmt_export` pretty-print with `indent=2`. Compact JSON would be
  noticeably smaller for large pages.
- AGENTS.md says `MCP_TRANSPORT=sse` switches the transport. Nothing in
  `servicetitan_mcp/` reads that variable, and `main()` hardcodes stdio.
- Tests use `httpx.MockTransport`, so a wrong slug still passes. Nothing checks
  the paths against the real API.
- README says "~90 tools". AGENTS.md says 95.

---

## Parent session: how to run this

### Your job

You coordinate. You do not implement Wave 1 or Wave 2 items yourself. You:

1. Prepare an integration branch.
2. Start one sub-session per work item.
3. Check each sub-session's result yourself before merging it.
4. Merge in a fixed order and run the full test suite after every merge.
5. Do Wave 3 (docs, counts, version) yourself.
6. Stop and ask the user before anything is pushed or released.

### Step 0: baseline

Run these from the repo root on a clean `main` and record the results. You
will compare against them later.

```bash
git status --short
```

```bash
git pull --ff-only
```

```bash
pytest -q
```

```bash
grep -c "@mcp.tool()" servicetitan_mcp/server.py
```

If `pytest` fails on a clean `main`, stop and tell the user. Do not start
sub-sessions on a red baseline.

Then create the integration branch:

```bash
git checkout -b feat/usage-driven-improvements
```

### Step 1: start the Wave 1 sub-sessions

Start all eight Wave 1 items (W1-A to W1-H) at once. Use whatever sub-session
or subagent mechanism your environment provides. If you can only run a few at
a time, start with W1-A, W1-B and W1-F. Each one needs:

- **Its own git worktree**, created from `feat/usage-driven-improvements`,
  under `.worktrees/<branch-name>` (that folder is gitignored). Two
  sub-sessions must never write in the same checkout.
- **Its own branch**, named as listed in the work item.
- **Access to the ServiceTitan MCP tools** for read-only live checks. Items
  W1-A, W1-C, W1-D, W1-E, W1-G and W1-H cannot be completed without them. If your
  sub-sessions cannot reach those tools, do the live checks yourself first and
  pass the answers in the brief.

Use this brief, filling in the three placeholders:

```text
You are implementing one work item from a larger plan. Work only in the git
worktree at <WORKTREE PATH>, on branch <BRANCH NAME>.

1. Read AGENTS.md at the repo root.
2. Read docs/plans/2026-11-11-usage-driven-improvements.md: the sections
   "What is already known", "Rules for every sub-session", and the work item
   "<WORK ITEM ID AND TITLE>". Ignore the other work items.
3. Do exactly that work item. Follow the rules section without exception.
4. Finish with the report format given in the rules section.

Do not push. Do not merge. Do not edit files the rules section reserves for
the parent.
```

### Step 2: review each result before merging

A sub-session's report is a claim. Before merging a branch:

1. Read the diff: `git diff feat/usage-driven-improvements...<branch>`.
2. Confirm it only touches the files its work item allows.
3. Confirm none of the parent-only files changed (see the rules section).
4. Run `pytest -q` inside that worktree.
5. Check each acceptance criterion in the work item against the diff.
6. If the report lists a live check, confirm the result is stated, not assumed.

If something is wrong, send the sub-session a specific correction. Do not fix
it yourself and do not merge a branch that fails a criterion.

### Step 3: merge order

Merge into `feat/usage-driven-improvements` in this order, one at a time, with
`git merge --no-ff <branch>`, and run `pytest -q` after each merge:

1. W1-A `fix/broken-slugs`
2. W1-F `fix/escape-hatch`
3. W1-C `feat/location-address-filters`
4. W1-D `feat/job-appointment-date-filters`
5. W1-E `feat/attributed-leads`
6. W1-H `feat/reviews-and-call-reasons` (skip if it reported nothing to build)
7. W1-B `feat/export-feed-to-file`
8. W1-G `chore/live-smoke-script`

After merging W1-G, add a smoke-registry entry for every tool the other
branches added (`list_attributed_leads`, `export_feed_to_file`, anything from
W1-H), because W1-G branched before they existed and its registry test will
fail without them. This small edit to `scripts/smoke_live.py` is yours to make.

Seven of the eight edit `servicetitan_mcp/server.py`, but in different
functions, so merges should be clean. The likeliest conflict is W1-E and W1-H
both adding a tool in the marketing section, and W1-B and W1-F both editing
near `export_feed` and `servicetitan_api_call`. If a merge conflicts, resolve it by keeping both
sides' changes, then re-run the tests. If you cannot tell how to keep both,
stop and ask the user.

After the last merge, run the smoke script from W1-G against one tenant. Every
typed tool it covers should return 200. A failure here is a real bug: open a
follow-up sub-session for it rather than editing the script to pass.

### Step 4: Wave 2

Start W2-A and W2-B only after all of Wave 1 is merged. They touch many
functions and would conflict with Wave 1 if run earlier. Run W2-A first, merge
it, then run W2-B. Do not run them in parallel.

### Step 5: Wave 3 and hand-back

Do Wave 3 yourself. Then stop and report to the user:

- what was merged, by work item;
- the final tool count and test result;
- every live check that failed or could not be run;
- every deviation a sub-session reported;
- anything from W1-H that needs a ServiceTitan scope enabled.

**Do not push to `origin`, do not merge into `main`, and do not build a
`.mcpb` bundle until the user says so.**

Finally, update `next_steps.md` at the repo root (gitignored) as AGENTS.md
describes: reset it to the empty sentinel if everything is done, otherwise
describe what is left.

---

## Rules for every sub-session

1. **Scope.** Do your work item and nothing else. If you find another bug,
   put it in your report. Do not fix it.
2. **Follow AGENTS.md**, especially "Adding a new tool" and "Gotchas and
   invariants". In short: `tenant: str` is the first parameter; use
   `_resolve(tenant)`; use `client.list_resource` / `client.get_resource`;
   return `_fmt(data)` for listings; docstrings carry "When to use" and "When
   NOT" and end with the line
   `tenant: name of a configured ServiceTitan tenant (call list_tenants)`;
   never swallow exceptions in a handler; never create an
   `httpx.AsyncClient`; never add a default tenant.
3. **Files reserved for the parent.** Do not edit these:
   `README.md`, `AGENTS.md`, `CLAUDE.md`, `pyproject.toml`, `manifest.json`,
   `tool_consolidation_notes.md`, `next_steps.md`, and the `instructions=`
   string at the top of `servicetitan_mcp/server.py`. If your change needs one
   of them updated, say exactly what in your report.
4. **Tests go in a new file** named in your work item. Do not add tests to an
   existing test file unless your item says so. This keeps merges clean.
   Copy the fixture pattern from `tests/test_slice4_tools.py`: the autouse
   `_stub_token` fixture and the `_wire_client(monkeypatch, handler)` helper.
   Every new or changed tool needs a test that asserts the exact request path
   and the exact query-param names.
5. **Verify against the real API before you write the code.** Documentation
   snippets have been wrong before.
   - For docs, use Context7 with library ID
     `/websites/developer_servicetitan_io`.
   - For live checks, use the ServiceTitan MCP tool `servicetitan_api_call`
     with `method: "GET"`. Call `list_tenants` first to get a tenant name.
   - **Live calls are read-only. Never send POST, PATCH, PUT or DELETE to a
     live tenant.**
   - Use `pageSize` of 1 to 5. You are checking that the path and the filter
     work, not pulling data.
   - To prove a filter is honored, compare a filtered call with an unfiltered
     one, or use a value that should return nothing. A 200 response alone does
     not prove the filter did anything: ServiceTitan ignores unknown params.
   - The installed MCP server may be older than this repo. A typed tool
     failing live does not mean your branch is wrong. Use
     `servicetitan_api_call` for checks.
6. **Privacy.** Live responses contain customer names, phones and addresses.
   Do not copy them into code, tests, commit messages or your report. Tests use
   made-up data.
7. **No linter or formatter.** None is configured. Do not add one.
8. **Run `pytest -q`** before you finish. All tests must pass, old and new.
9. **Commit on your branch** with a conventional message
   (`fix(...)`, `feat(...)`, `chore(...)`). Do not push. Do not merge.
10. **If the plan and the API disagree**, the API wins. Implement what the API
    actually does and state the difference in your report. If you cannot
    complete the item (for example a 403 because a scope is not enabled), stop,
    commit nothing that is half-working, and report why.

### Report format

End with exactly these headings:

```text
## Work item
<ID and title>

## Branch and commit
<branch name>, <commit sha>

## Files changed
<list>

## Live checks
<each check: the path and params, the HTTP status, and what it proved.
 Write "not run" with the reason if you could not run one.>

## Tests
<pytest summary line, and the names of the tests you added>

## Deviations from the plan
<anything you did differently and why, or "none">

## Needed from the parent
<exact edits needed in parent-only files, or "none">

## Other findings
<bugs or oddities you noticed and did not fix, or "none">
```

---

## Wave 1 work items (parallel)

### W1-A: Fix the two 404 tools and the installed-equipment filter

- **Branch:** `fix/broken-slugs`
- **Test file:** `tests/test_slug_fixes.py`
- **Files:** `servicetitan_mcp/server.py`

**Change**

1. In `list_installed_equipment`, change the category passed to
   `client.list_resource` from `"equipment-systems"` to `"equipmentsystems"`.
2. In `list_tasks`, change the category from `"task-management"` to
   `"taskmanagement"`.
3. Settle the installed-equipment location filter. Check live whether
   `locationId`, `locationIds`, or both are honored. Then:
   - keep `location_id: int | None` working, mapped to whichever param name
     is honored;
   - add `location_ids: list[int] | None`, sent as one comma-separated
     `locationIds` value, if the API honors a multi-ID filter. This is what
     the 99 past raw calls needed;
   - if both `location_id` and `location_ids` are passed, combine them.
4. Update the `list_installed_equipment` docstring to describe the batch
   filter and say it replaces one call per location.

**Live checks**

- Both corrected paths return 200.
- Which location filter names are honored, proven by comparison with an
  unfiltered call.
- Whether `locationIds` accepts several comma-separated IDs.

**Acceptance**

- Tests assert the exact paths `/equipmentsystems/v2/tenant/123/installed-equipment`
  and `/taskmanagement/v2/tenant/123/tasks`.
- Tests assert the exact query-param name and format for one ID and for
  several IDs.
- No other tool is touched.

**Out of scope:** adding filters to `list_tasks`. Mention useful ones in your
report.

---

### W1-B: `export_feed_to_file`

- **Branch:** `feat/export-feed-to-file`
- **Test file:** `tests/test_feed_to_file.py`
- **Files:** new `servicetitan_mcp/feed_export.py`; `servicetitan_mcp/server.py`

**Why:** `export_feed` was called 614 times, up to 108 times in one session,
because the model has to walk continuation tokens page by page with every page
landing in its context. `run_report_to_file` already solves the same problem
for reports. Read it first and mirror its behaviour.

**New tool signature**

```python
@mcp.tool()
async def export_feed_to_file(
    tenant: str,
    category: str,
    feed: str,
    from_token: str | None = None,
    format: str = "jsonl",
    fields: list[str] | None = None,
    output_path: str | None = None,
    output_dir: str | None = None,
    overwrite: bool = False,
    include_recent_changes: bool = False,
    max_pages: int | None = None,
) -> str:
```

**Behaviour**

1. Validate inputs the way `run_report_to_file` does: return an `"Error: …"`
   string for a bad `format`, for `output_path` plus `output_dir` together,
   and for an existing file without `overwrite=True`.
2. Loop on `client.export_resource(category, feed, token,
   include_recent_changes)`. Start with `from_token`. After each page, take
   `continueFrom` as the next token. Stop when `hasMore` is false.
3. Stop early, and say so in the result, when:
   - `max_pages` pages have been written; or
   - a page returns `hasMore=true` with a `continueFrom` equal to the token
     just used (a loop guard, so a bad feed cannot spin forever).
4. Write to `<final>.partial` and `os.replace` it onto the final name only on
   success. On any exception, close the file, delete the partial, and
   re-raise. Do not swallow the exception.
5. Formats:
   - `jsonl` (default): one JSON object per line, the record as returned, or
     only the selected `fields` if given.
   - `csv`: allowed only when `fields` is given. Return an `"Error: …"`
     string otherwise. Export records are nested objects with no fixed column
     set, so CSV needs the caller to choose columns. Nested values (dicts,
     lists) are written as compact JSON text in the cell.
6. `fields`: a list of key names. Support dotted paths for nested objects, for
   example `"address.zip"`. A missing key yields `null` in JSONL and an empty
   cell in CSV. The output key or column header is the dotted path as given.
7. Default location and name: same precedence as `resolve_output_path`
   (`output_path`, then `output_dir`, then `ST_OUTPUTS_DIR`, then the in-repo
   `report_exports/`). Auto-name the file
   `feed_<category>_<feed>_<start>.<ext>`, where `<feed>` has `/` replaced
   through `_sanitize`, and `<start>` is the sanitized `from_token` when it
   looks like a date, else `full`. If `from_token` is a continuation token
   (not a date), use `resume` instead of the token text: tokens are long and
   opaque.
8. Return compact JSON metadata, never the rows:
   `file_path`, `format`, `row_count`, `pages_fetched`, `continue_from` (the
   last token, so a later call can resume), `caught_up` (true when the loop
   ended on `hasMore=false`), `stopped_reason` (`"caught_up"`, `"max_pages"`
   or `"token_did_not_advance"`), `columns` (the `fields` list, or the keys of
   the first record), and `preview` (the first 3 records after field
   selection).

**Where the code goes**

- Pure helpers in `servicetitan_mcp/feed_export.py`: output-path resolution,
  dotted-path selection, and a `FeedFileWriter` class. No async and no network
  in that module, so it can be unit-tested directly. Reuse `_expand_path`,
  `_sanitize` and `_DEFAULT_EXPORT_DIR` from `report_export.py`, as
  `attachment_export.py` does. Do not modify `report_export.py`.
- The tool handler in `server.py`, placed directly after `export_feed`.
- The docstring must carry the same feed catalog that `export_feed` has, or
  point to it explicitly, plus "When to use" (whole-feed or since-a-date
  pulls, anything over a page or two) and "When NOT" (a quick peek at one
  page: use `export_feed`; a filtered slice: use the typed `list_*` tool).
- In the `export_feed` docstring, add one "When NOT" sentence pointing whole
  feed pulls to `export_feed_to_file`. Change nothing else in `export_feed`.

**Live checks:** none required. Optionally run one small pull with
`max_pages=1` if your environment runs the server from this branch.

**Acceptance (each needs a test)**

- Multi-page feed: all pages written, row count correct, final token returned.
- `hasMore=false` on the first page: one page, `caught_up` true.
- `max_pages` stop: `stopped_reason` is `"max_pages"`, `continue_from` is the
  resume token.
- Token-did-not-advance stop.
- An exception mid-stream leaves no `.partial` and no final file, and the
  exception propagates.
- Existing file without `overwrite`: error string, file untouched.
- `csv` without `fields`: error string.
- `fields` with a dotted path and a missing key.
- Unit tests for the pure helpers in `feed_export.py`.

---

### W1-C: Address filters on `list_locations`

- **Branch:** `feat/location-address-filters`
- **Test file:** `tests/test_location_filters.py`
- **Files:** `servicetitan_mcp/server.py`

**Why:** 112 raw calls hit `/crm/v2/tenant/{tenant_id}/locations`, 109 of them
with `street`. The typed tool only filters by `customer_id`. Separately,
`list_locations(customer_id=…)` was called 99 times, once per customer.

**Change**

1. Look up the full query-param list for the locations list endpoint in the
   docs. Confirm live which address params are honored. `street` and `zip`
   were used in the past; check for `city`, `state`, `unit`, `name`, `ids`
   and `customerIds` style params too.
2. Add an optional parameter to `list_locations` for each address filter that
   is confirmed honored. Use snake_case names mapped to the API's camelCase.
3. If the API honors a multi-customer filter, add
   `customer_ids: list[int] | None`. If it honors `ids`, add
   `ids: list[int] | None`. Keep `customer_id` working.
4. Rewrite the docstring: "When to use" must include finding a location by
   street address. Say plainly how the match behaves (exact, prefix or
   contains), based on what you observed live.

**Live checks**

- For each param you add: proof it filters (comparison or a
  should-return-nothing value).
- How `street` matches: exact, prefix or contains; case-sensitive or not.

**Acceptance**

- A test per new parameter asserting the exact query-param name.
- A test that no filter params are sent when none are passed.
- Only params proven live are added. List the ones you rejected in your
  report.

---

### W1-D: Date-window filters on `list_jobs` and `list_appointments`

- **Branch:** `feat/job-appointment-date-filters`
- **Test file:** `tests/test_job_appointment_filters.py`
- **Files:** `servicetitan_mcp/server.py` (`list_jobs`, `list_appointments`
  and `list_calls` only)

**Why:** 97 raw calls hit jobs and appointments, almost all to get a closed
date window (`createdOnOrAfter` plus `createdBefore`). `list_jobs` has no
upper bound on any date. `list_appointments` has no created-date filter.

**Change**

1. `list_jobs`: look up the endpoint's full param list. Add, for each that is
   confirmed live: `created_before`, `completed_before`,
   `modified_on_or_after`, `modified_before`. Also add any of these the docs
   list and the API honors, because they are common asks: `job_number`,
   `ids`, `business_unit_id`, `job_type_id`, `campaign_id`, `technician_id`,
   `project_id`. Keep the four existing parameters and their names.
2. `list_appointments`: add `created_on_or_after`, `created_before`,
   `modified_on_or_after`, `modified_before` and `job_id`, each only if
   confirmed live.
3. `list_appointments` currently sends `startsOnOrBefore`. Check live whether
   that name is honored. If it is not and `startsBefore` is, send the honored
   name. Keep the Python parameter `starts_on_or_before` so existing callers
   do not break, add `starts_before` as the accurately named parameter, and
   say in the docstring which one to use.
4. `list_calls` currently sends `createdOnOrBefore`. Run the same check and
   apply the same fix if the name is wrong.
5. Update the docstrings: state the date format, that upper bounds are
   exclusive ("before") where that is the case, and that times are UTC.

**Live checks**

- Each new param filters (comparison or should-return-nothing).
- The verdict on `startsOnOrBefore` and `createdOnOrBefore`: honored or
  silently ignored. State the evidence.

**Acceptance**

- A test per new parameter asserting the exact query-param name.
- Existing tests still pass with no edits. If one must change because it
  locked in a wrong param name, say so in your report.

**Out of scope:** auditing filters on other tools. That is W2-A.

---

### W1-E: `list_attributed_leads` (Marketing Ads)

- **Branch:** `feat/attributed-leads`
- **Test file:** `tests/test_attributed_leads.py`
- **Files:** `servicetitan_mcp/server.py`

**Why:** 40 raw calls, 10 of them to a guessed slug (`marketing-ads`) that is
probably wrong. A typed tool removes the guess.

**Change**

1. Confirm the path live. Expected:
   `/marketingads/v2/tenant/{tenant_id}/attributed-leads`. Also check whether
   `/marketing-ads/…` returns 404, so the docstring can warn against it if
   the escape hatch is used.
2. Look up the endpoint's params in the docs. Past calls used `fromUtc`,
   `toUtc` and `leadType`. Find the legal `leadType` values and which params
   are required.
3. Add `list_attributed_leads` in the marketing section of `server.py`, next
   to `list_campaign_costs`. Follow the "Adding a new tool" pattern. Suggested
   parameters: `tenant`, `from_utc`, `to_utc`, `lead_type`, `page`,
   `page_size`. Make `from_utc` and `to_utc` required if the API requires
   them.
4. Check the response envelope. If it is the standard `{data, hasMore, …}`
   shape, return `_fmt(data)`. If it is not, handle it the way
   `get_job_history` handles its non-standard shape, and explain in a comment.
5. Docstring: what an attributed lead is, "When to use" (tying calls, forms
   and bookings back to an ad campaign or web session), "When NOT"
   (`list_campaigns` for campaign definitions, `list_campaign_costs` for
   spend, `list_calls` for raw call records), the legal `lead_type` values,
   and the UTC date-time format.

**Live checks**

- The correct slug, and the status the other slug returns.
- Required params and legal `leadType` values.
- The response envelope shape.
- If the call returns 403, the scope is not enabled on the app: stop and
  report it. Do not ship an untested tool.

**Acceptance**

- A test asserting the exact path and param names.
- A test for the pagination footer (or the non-standard shape handling).

---

### W1-F: Escape hatch and client cleanups

- **Branch:** `fix/escape-hatch`
- **Test file:** `tests/test_escape_hatch.py`
- **Files:** `servicetitan_mcp/client.py`; `servicetitan_mcp/server.py`
  (`servicetitan_api_call` only)

**Change**

1. **Stop swallowing errors.** Remove the `try/except Exception` around the
   body of `servicetitan_api_call` so API errors propagate to MCP like every
   other tool. The input-validation early returns (`query_params` not a dict,
   `body` not a dict or list, unsupported method) stay as they are. Remove
   the `traceback` import from `server.py` if nothing else uses it.
2. **Honor `query_params` on writes.** Add an optional `params` argument to
   `client.post`, `client.patch` and `client.put`, passed through to
   `_request`. Pass `query_params` from `servicetitan_api_call` for all four
   methods.
3. **Remove the dead `timeout` argument** from `client.get`, `post`, `patch`
   and `put`. First search the whole repo, including tests, for callers that
   pass `timeout=`. If any exist, update them. Do not implement per-call
   timeouts: nothing needs them.
4. Leave `client.delete` unexposed. Do not add DELETE to the escape hatch.

**Before changing anything**, search `tests/` for assertions on the string
`"API Error"`. If a test expects the old swallow behaviour, update it to
expect the exception and list it in your report.

**Live checks:** none. Do not send write requests to a live tenant.

**Acceptance (each needs a test)**

- A 404 from the transport raises `httpx.HTTPStatusError` out of
  `servicetitan_api_call`, with the response body in the message.
- POST, PATCH and PUT each send both the query params and the JSON body.
- GET still sends query params.
- An unsupported method still returns the existing error string.

---

### W1-G: Live smoke script

- **Branch:** `chore/live-smoke-script`
- **Test file:** `tests/test_smoke_registry.py`
- **Files:** new `scripts/smoke_live.py`

**Why:** the mocked tests cannot catch a wrong slug. Two tools shipped broken
because of that. This script is the check that would have caught them.

**Change**

1. Create `scripts/smoke_live.py`, runnable as
   `python scripts/smoke_live.py <tenant>`. It reads tenant credentials from
   the same environment variables the server uses (through
   `servicetitan_mcp.config`), so it needs no new configuration.
2. The script holds a registry: a list of `(tool_name, kwargs)` entries, one
   for every typed **list** tool that can be called with only `tenant` plus
   small paging arguments. Call each tool function from
   `servicetitan_mcp.server` directly with `page_size=1`.
3. Skip, with a printed reason, tools that need an ID or other required
   argument (`get_*`, the notes and contacts sub-resources, report tools,
   `download_job_photos`, `export_feed`, `servicetitan_api_call`). List them
   in a `SKIPPED` dict with the reason, so the gap is visible.
4. Reporting tools are throttled to about 3 calls a minute. Include at most
   one reporting call (`list_report_categories`), and run it last.
5. Run the calls one after another, not in parallel. Print one line per tool:
   `PASS`, or `FAIL` with the HTTP status and path. Print a summary line. Exit
   with status 1 if anything failed.
6. Never print response bodies: they contain customer data. Print only status
   and item counts.
7. The script must be read-only. It calls list tools only.

**The unit test** (`tests/test_smoke_registry.py`) does not hit the network.
It imports the registry and asserts that every `@mcp.tool()` function in
`servicetitan_mcp.server` appears in either the registry or `SKIPPED`. That
way a tool added later without a smoke entry fails the test suite. Find the
registered tools through the FastMCP instance (`server.mcp`); check how the
installed `mcp` version exposes them rather than assuming an attribute name.

**Note for this branch:** you are branching before W1-A is merged, so
`list_installed_equipment` and `list_tasks` will FAIL when you run the script.
That is expected and proves the script works. Say so in your report.

**Live checks:** run the script once against one tenant and report the
PASS/FAIL counts and the names of the failing tools. No customer data.

**Acceptance**

- The script exits 1 when a tool returns a non-2xx status.
- The registry test passes and fails correctly when a tool is missing (prove
  it by temporarily removing an entry; do not commit that).
- No response bodies are printed.

**Needed from the parent:** a README line documenting the script. Give the
exact text in your report.

---

### W1-H: New API coverage (research first, then build)

- **Branch:** `feat/reviews-and-call-reasons`
- **Test file:** `tests/test_new_coverage.py`
- **Files:** `servicetitan_mcp/server.py`

This item depends on API scopes that may not be enabled. **Do the live checks
first. Build only what returns 200.**

**Candidates**

1. **Marketing Reputation reviews.** Path from the docs:
   `/marketingreputation/v2/tenant/{tenant}/reviews`. Filters in the docs
   include `fromDate`, `toDate`, `technicianIds`, `campaignIds`,
   `locationIds`, `sources`, `fromRating`, `toRating`, `reviewStatuses`,
   `createdOnOrAfter`, `createdBefore`. If it returns 200, add `list_reviews`
   with the filters you prove are honored. This supports technician coaching
   and campaign analysis.
2. **Job Booking call reasons.** The docs list a `call-reasons` endpoint under
   the Job Booking (JBCE) module. Find the full path in the docs and check it
   live. If it returns 200 and is a small static table, add it to
   `_LOOKUP_KINDS` as `call_reasons` instead of creating a new tool. Then
   update the two docstrings that list the kinds (`get_lookup_tables` and the
   `servicetitan://lookups/{tenant}/{kind}` resource description). The
   `instructions=` string is parent-only: report the needed edit.
3. **Telecom v3 calls.** `list_calls` uses `/telecom/v2/…/calls`. Past
   sessions called `/telecom/v3/…/calls` four times. Research only: compare
   the v2 and v3 responses and params, and report whether `list_calls` should
   move to v3. Do not change `list_calls`.

**Live checks:** status for each candidate path. For a 403, record the scope
name the docs say is required, so the user can enable it.

**Acceptance**

- For each candidate: either a tested tool or lookup kind, or a clear report
  entry saying why not (status code and required scope).
- Tests assert exact paths and param names for anything built.
- If nothing can be built, commit nothing and report. That is a valid outcome.

---

## Wave 2 work items (after Wave 1 is merged)

Branch both from the updated `feat/usage-driven-improvements`. Run W2-A, merge
it, then run W2-B.

### W2-A: Filter-name audit across all typed tools

- **Branch:** `fix/filter-name-audit`
- **Test file:** `tests/test_filter_audit.py`
- **Files:** `servicetitan_mcp/server.py`

**Why:** ServiceTitan ignores unknown query params. A typed tool with a
misspelled filter returns unfiltered data and looks like it worked. W1-D
checks two suspects; this item checks the rest.

**Change**

1. List every query-param name each typed `list_*` tool can send. Skip the
   tools Wave 1 already verified: `list_locations`, `list_jobs`,
   `list_appointments`, `list_calls`, `list_installed_equipment`,
   `list_attributed_leads`, and anything built in W1-H.
2. For each name, check it against the endpoint's param list in the docs.
3. For every name the docs do not list, check live whether it filters.
4. Fix names that are wrong. Keep the Python parameter names stable so callers
   do not break; change only what is sent to the API. If a Python parameter
   name is now misleading (says "on or before" but the API is exclusive
   "before"), fix the docstring rather than renaming.
5. Put the full audit table in your report: tool, Python parameter, API param
   sent, verdict (`docs-confirmed`, `live-confirmed`, `fixed`, or
   `could not verify` with the reason).

**Pace yourself:** this is roughly 35 tools. Batch the docs lookups by API
module. Only make live calls for names the docs do not confirm.

**Acceptance**

- A test for every fixed param asserting the corrected name.
- The audit table is complete: no tool silently left out.

### W2-B: Compact JSON in list output

- **Branch:** `chore/compact-json`
- **Test file:** add cases to `tests/test_pagination.py` (the one exception to
  the new-file rule, because that file owns `_fmt`)
- **Files:** `servicetitan_mcp/server.py` (`_fmt` and `_fmt_export` only)

**Why:** every list response is pretty-printed with `indent=2`. The reader is
a model, not a person, and the indentation costs tokens on every call.

**Change**

1. In `_fmt` and `_fmt_export`, serialize the item list with
   `json.dumps(items, separators=(",", ":"), default=str)`. Keep one record
   per line so output stays greppable: join the per-item dumps with `",\n"`
   inside `[` and `]`.
2. The pagination footer text must stay **byte-for-byte identical**. It is
   load-bearing (see AGENTS.md).
3. Leave every other `json.dumps(..., indent=2)` in the file alone: `get_*`
   single records, metadata blobs and error objects are small and read better
   indented.
4. Before and after, measure the character count of a `_fmt` call on a
   200-item synthetic page and put both numbers in your report.

**Acceptance**

- Output of `_fmt` minus the footer parses back with `json.loads` to the
  original items. Same for `_fmt_export`.
- The footer strings are unchanged (existing footer tests pass unedited).
- Any existing test that had to change is listed in your report with the
  reason. A test that only broke on whitespace may be updated; a test that
  broke on content means your change is wrong.

---

## Wave 3: docs and release (parent only)

Do these yourself on `feat/usage-driven-improvements` after Wave 2 is merged.

1. **Tool count.** Run `grep -c "@mcp.tool()" servicetitan_mcp/server.py`.
   Update the count in `AGENTS.md` ("It exposes 95 tools") and the "~90 tools"
   line at the top of `README.md`.
2. **README tool catalog.** Add every new tool and every new filter. Apply the
   exact edits each sub-session listed under "Needed from the parent".
   Document `scripts/smoke_live.py` and state that it should be run before
   each release.
3. **Server instructions** (the `instructions=` string in `server.py`):
   - In the pagination or export guidance, tell the model to use
     `export_feed_to_file` for whole-feed pulls.
   - In item 8 (escape hatch), remove any wording that is now wrong, and
     mention that errors now surface as tool errors.
   - If `call_reasons` was added as a lookup kind, add it to the list in
     item 6.
4. **AGENTS.md corrections.**
   - Add `feed_export.py` to the Layout section.
   - The line saying `MCP_TRANSPORT=sse` switches transport is stale: nothing
     reads that variable. **Ask the user** whether to delete the line or to
     implement the switch. Do not decide this yourself; `Dockerfile` and
     `railway.toml` suggest an HTTP deployment was intended.
   - Add a gotcha: "ServiceTitan ignores unknown query params, so a wrong
     filter name fails silently. Verify new filters live, and run
     `scripts/smoke_live.py` after adding a tool."
5. **`tool_consolidation_notes.md`.** Add a short dated section with the usage
   numbers from this plan's "Why this work exists" and "What is already
   known" sections. Do not act on the consolidation itself.
6. **Version.** Propose `1.2.0` (new tools, no breaking changes) and set it in
   both `pyproject.toml` and `manifest.json` only after the user agrees. They
   must match.
7. **Final checks.** `pytest -q`, then `python scripts/smoke_live.py <tenant>`
   for at least one tenant. Report both results.
8. **Stop.** Report to the user as described in Step 5. Wait for the go-ahead
   before pushing, merging to `main`, or building the bundle.

---

## Not in this plan

- **Tool consolidation** (`get_record`, collapsing the reference `list_*`
  tools). `tool_consolidation_notes.md` covers it. It needs a separate
  decision from the user: the usage data supports it, but it removes tool
  names coworkers may rely on.
- **Write tools.** Nothing here adds POST, PATCH or PUT tools.
- **Moving `list_calls` to telecom v3.** W1-H researches it only.
- **Per-call HTTP timeouts.** W1-F removes the unused argument instead.
- **Releasing.** Pushing, merging to `main` and building the `.mcpb` bundle
  wait for the user.
