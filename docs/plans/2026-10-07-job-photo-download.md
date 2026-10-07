# Job photo download tool (`download_job_photos`)

_Tenant names and record ids are replaced with placeholders in this public copy; the real values are in the gitignored `next_steps.md`._

## Context

Justin wants the "after" photo of completed installs. Field pros upload photos to each job in ServiceTitan, but the API exposes no viewable image URL: the attachment list returns metadata only, and the pixels come only from an authenticated download endpoint. So the MCP server needs to download field photos to a folder with a descriptive filename (tenant, business unit, job type, job number, date completed). A separate process, out of scope here, will view the files and pick the after shot.

Decisions already made by Justin:
- One tool that handles a single job or a batch sweep by completed-date range.
- Download only. No inline image view tool (would need Pillow and downscaling).
- Default filter is field photos only, widenable by parameter.
- Filename date is the completed date in America/Chicago, overridable.

## Verified facts (live, `tenant_a`, 2026-10-07)

- `GET /forms/v2/tenant/{id}/jobs/{jobId}/attachments` returns 200, standard paginated envelope. Items: `id`, `fileName`, `title`, `createdOn`, `createdById`. Supports `page`, `pageSize`, `includeTotal`, `createdOnOrAfter`, `createdBefore`, `sort`.
- The old note that attachments 404 was about the `jpm` path. The resource is under `forms`.
- `GET /forms/v2/tenant/{id}/jobs/attachment/{attachmentId}` returned an empty non-error body through our client, which does not follow redirects. Inferred: a 302 to a temporary signed URL (the Findings API documents the same behavior for its assets). **Not yet confirmed**; see step 1.
- `GET /jpm/v2/tenant/{id}/jobs` accepts `businessUnitId` and `jobStatus=Completed` and `completedOnOrAfter` (63 results for BU <bu_id> since 2026-09-28).
- Test job: **<job_id>** (an HVAC residential install BU, BU `<bu_id>`, completed 2026-09-30T21:34Z). 25 attachments in three groups by `fileName` prefix:
  - `Images/...` (10): pricebook/estimate stock images copied at job creation. Some have no extension.
  - `CapturedDocs/...pdf` (2): signed invoices.
  - `Attaches/..._cdv_photo_...jpg` (13): field pro photos, uploaded 21:20 to 21:24Z.
- Nothing marks a photo as before or after, and upload timestamps cluster, so the tool does not try to pick one.

## Step 0: session setup

1. `git status --short --branch`, then `git pull --ff-only` (skipped during planning because plan mode is read-only).
2. Copy this plan to `docs/plans/2026-10-07-job-photo-download.md` so it lives in the project.

## Step 1: confirm download behavior before writing code

Write a throwaway script in the scratchpad (not the repo) that builds a client via `servicetitan_mcp.server._get_client("tenant_a")` using the env from the `servicetitan-local` MCP config, calls `client._send("GET", f"/forms/v2/tenant/{client.tenant_id}/jobs/attachment/<attachment_id>")`, and prints `status_code`, `headers.get("location")` host only, `content-type`, and `len(content)`.

Also confirm with `servicetitan_api_call` that `jpm/jobs` honors `completedBefore` and `jobTypeId` (check that results actually narrow, not just 200).

Branch on the result:
- **302 + Location** (expected): implement as below.
- **200 with bytes**: `download()` returns the body directly; the redirect branch stays as a harmless fallback.
- Anything else: stop and report.

## Step 2: `servicetitan_mcp/client.py`

Add one public method next to `get_resource`:

```python
async def download(self, path: str) -> tuple[bytes, str | None]:
    """GET a binary resource. Returns (content, content_type)."""
```

- First hop goes through the existing `_send("GET", path)` so auth, the per-tenant limiter, the semaphore, and retries all apply.
- If the response is a redirect (301/302/303/307/308), fetch `Location` with `self._client.get(location)` under `self._sem`, **with no ServiceTitan headers**. The bearer token and app key must not go to the storage host. Apply `_raise_with_body` and the same `RETRY_STATUS` backoff loop.
- If the first hop is 200 with content, return it.
- Empty body with no redirect raises `RuntimeError` with the status code.

Do not turn on `follow_redirects` on the shared client; httpx would forward headers and it changes behavior for every other call.

## Step 3: new module `servicetitan_mcp/attachment_export.py`

Pure helpers, no network, mirroring `report_export.py`. Reuse `_expand_path`, `_sanitize`, and `_DEFAULT_EXPORT_DIR` from `report_export.py` rather than duplicating them.

- `IMAGE_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "webp", "gif"}`
- `classify(file_name) -> str`: `"field_photo"` (prefix `Attaches/` and image ext), `"image"` (any other image ext, or prefix `Images/`), else `"document"`.
- `selected(kind, include) -> bool` for `include` in `field_photos | images | all`.
- `local_date(completed_on: str, tz: str) -> str`: parse the ISO UTC timestamp, convert with `zoneinfo.ZoneInfo(tz)`, return `YYYY-MM-DD`. Unknown tz raises `ValueError`.
- `build_filename(tenant, bu_name, job_type_name, job_number, date, seq, attachment_id, ext) -> str`:

  ```
  {tenant}__{bu}__{job_type}__{job_number}__{date}__{seq:02d}_{attachment_id}.{ext}
  tenant_a__<BusinessUnit>__<JobType>__<job_id>__2026-09-30__01_<attachment_id>.jpg
  ```

  Each field passes through `_sanitize`. `seq` is the 1-based position among the selected attachments of that job sorted by `createdOn`, so it reads in capture order. `attachment_id` guarantees uniqueness and makes names deterministic across re-runs. Extension comes from `fileName`, falling back to the download content type, then `bin`.
- `resolve_photo_dir(output_dir) -> Path`: `output_dir` if given, else `$ST_OUTPUTS_DIR/job_photos`, else `report_exports/job_photos` (already gitignored). `mkdir(parents=True, exist_ok=True)`.

## Step 4: `servicetitan_mcp/server.py`

Replace the stale "ATTACHMENTS intentionally NOT wrapped" comment near line 722 with a one-line pointer to the new tools. Add both tools in the Forms section, following the `list_customers` pattern (`tenant` first, `_resolve`, when to use / when NOT, closing `tenant:` docstring line).

### `list_job_attachments(tenant, job_id, page=1, page_size=200) -> str`

`client.list_resource("forms", f"jobs/{job_id}/attachments", page, page_size)` then `_fmt(data)`. Docstring explains the three `fileName` prefixes and that there is no image URL in the response.

### `download_job_photos(...) -> str`

```python
async def download_job_photos(
    tenant: str,
    job_id: int | None = None,
    completed_on_or_after: str | None = None,
    completed_before: str | None = None,
    business_unit_id: int | None = None,
    job_type_id: int | None = None,
    include: str = "field_photos",       # field_photos | images | all
    output_dir: str | None = None,
    timezone: str = "America/Chicago",
    overwrite: bool = False,
    max_jobs: int = 200,
) -> str:
```

Flow:
1. Validate. Exactly one of `job_id` or `completed_on_or_after`; `include` and `timezone` must be valid. Return `"Error: ..."` strings for bad input, as `run_report_to_file` does.
2. Resolve jobs.
   - Single: `client.get_resource("jpm", "jobs", job_id)`. If it has no `completedOn`, return an error saying the job is not completed.
   - Batch: page `client.list_resource("jpm", "jobs", ...)` with `jobStatus=Completed`, `completedOnOrAfter`, `completedBefore`, `businessUnitId`, `jobTypeId` until `hasMore` is false or `max_jobs` is reached. If capped, set `truncated: true` in the result.
3. Name lookups, once per call: `client.list_resource("settings", "business-units", 1, 500)` and `client.list_resource("jpm", "job-types", 1, 500)`, paged to completion, into id-to-name dicts. Unknown ids fall back to `BU<id>` / `JT<id>`.
4. Per job: page the attachment list, filter with `classify`/`selected`, sort by `createdOn`, assign `seq`.
5. Per attachment: build the target path. If it exists and `overwrite` is false, count it as skipped. Otherwise `client.download(...)`, write to `<name>.partial`, then `os.replace`. Remove the `.partial` on any failure.
6. Append one line per written file to `manifest.jsonl` in the output dir: tenant, job id and number, business unit, job type, `completedOn`, attachment id, attachment `createdOn`, original `fileName`, `title`, saved filename, bytes. This is the only place the upload timestamp survives, and the downstream after-shot picker will want it.
7. Download jobs sequentially and files within a job sequentially. The main bucket is 30 rps, so this is not the bottleneck, and it keeps memory flat.

Error handling: bad input and job-level failures (job lookup, attachment listing) propagate as usual. A failure on an individual file is recorded in an `errors` list in the result (job id, attachment id, message) and the sweep continues, the same way `get_lookup_tables` reports per-kind errors. One expired or deleted attachment should not discard a 60-job run, and the error is still visible to the LLM.

Return `json.dumps` of:

```
output_dir, manifest_path, include, timezone,
jobs_matched, jobs_with_files, jobs_without_files: [job ids],
files_written, files_skipped_existing, bytes_written,
errors: [...], truncated
```

`jobs_without_files` is deliberate: it answers "which installs have no field photos."

## Step 5: tests, `tests/test_job_attachments.py`

Use the existing `httpx.MockTransport` + `_wire_client` + `_stub_token` pattern from `tests/test_report_to_file.py`.

- `list_job_attachments` hits `/forms/v2/tenant/123/jobs/55/attachments` and returns the `_fmt` footer.
- `client.download` follows a 302, returns the bytes, and the request to the redirect host carries **no** `Authorization` or `ST-App-Key` header.
- `client.download` returns a direct 200 body unchanged.
- `classify` across the three real prefixes, including the extensionless `Images/Material/...` names.
- `local_date`: `2026-10-01T02:30:00Z` in America/Chicago is `2026-09-30`; bad tz raises.
- `build_filename` sanitizes spaces and slashes and zero-pads `seq`.
- Single-job run writes only the `Attaches/` images, in `createdOn` order, with expected names, and writes `manifest.jsonl`.
- `include="all"` also writes the PDF.
- Re-run without `overwrite` writes nothing and reports `files_skipped_existing`.
- A 500 on one file leaves no `.partial`, records an `errors` entry, and the other files still land.
- Batch mode maps `jobStatus`, `completedOnOrAfter`, `completedBefore`, `businessUnitId`, `jobTypeId` onto the jobs query, and `max_jobs` sets `truncated`.
- Validation: both or neither of `job_id` / `completed_on_or_after` returns an error string.
- Schema test that the tool exposes the expected parameters.

## Step 6: docs and packaging

- `pyproject.toml`: add `tzdata; sys_platform == "win32"` (stdlib `zoneinfo` has no tz database on Windows, and `.mcpb` recipients may be on it).
- `README.md`: add both tools to the Forms catalog section, bump the tool count (95 to 97), note that `ST_OUTPUTS_DIR` also hosts `job_photos/`, and update the documented bundle contents from five `servicetitan_mcp/*.py` modules to six (10 files to 11).
- `manifest.json`: broaden the `outputs_dir` description to mention job photos. Add the two tools if the manifest enumerates tools.
- `AGENTS.md`: tool count, `attachment_export.py` in the layout list, `client.download` as the sanctioned way to fetch binary, and a gotcha that the signed download URL must not receive ServiceTitan auth headers.
- `next_steps.md`: correct the "attachments 404 dead end" entry and record the new tools.
- No version bump or release in this change unless Justin asks; note in `next_steps.md` that the next release needs both `manifest.json` and `pyproject.toml` bumped.

## Verification

1. `pytest` passes in full, including the new file.
2. Reconnect the `servicetitan-local` MCP server so the editable install reloads.
3. `list_job_attachments(tenant="tenant_a", job_id=<job_id>)` returns 25 items.
4. `download_job_photos(tenant="tenant_a", job_id=<job_id>)` reports `files_written: 13`. Confirm 13 `.jpg` files in `report_exports/job_photos/` named `tenant_a__<BusinessUnit>__...__<job_id>__2026-09-30__NN_<id>.jpg`, no `.partial` files, and 13 lines in `manifest.jsonl`. Open one with the Read tool to confirm it is a real photo.
5. Run the same call again: `files_written: 0`, `files_skipped_existing: 13`.
6. Batch: `download_job_photos(tenant="tenant_a", completed_on_or_after="2026-09-28", completed_before="2026-10-05", business_unit_id=<bu_id>)`. Check `jobs_matched` against a `list_jobs`-style count for the same filter, and review `jobs_without_files` and `errors`.
7. Repeat a single-job run on `tenant_b` (find a completed HVAC install via its business units) to confirm the paths hold on a second tenant.

## Critical files

- `servicetitan_mcp/client.py` (new `download`)
- `servicetitan_mcp/attachment_export.py` (new)
- `servicetitan_mcp/report_export.py` (reuse `_expand_path`, `_sanitize`, `_DEFAULT_EXPORT_DIR`)
- `servicetitan_mcp/server.py` (two tools, stale comment near line 722)
- `tests/test_job_attachments.py` (new)
- `README.md`, `AGENTS.md`, `manifest.json`, `pyproject.toml`, `next_steps.md`
