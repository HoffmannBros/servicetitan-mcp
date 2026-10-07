"""Tests for `list_job_attachments`, `download_job_photos`, `client.download`,
and the pure helpers in `attachment_export`.

The attachment download endpoint answers with a 302 to a signed storage URL,
so the load-bearing check here is that the second hop carries no ServiceTitan
credentials. The rest mirrors tests/test_report_to_file.py: MockTransport
integration tests for naming, filtering, re-run skipping, and per-file errors.
"""

from __future__ import annotations

import json

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.attachment_export import (
    build_filename,
    classify,
    ext_from_content_type,
    local_date,
    resolve_photo_dir,
    selected,
)
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket

BLOB_HOST = "blobs.example.net"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture(autouse=True)
def _stub_token(monkeypatch):
    async def fake_get_token(*_a, **_kw):
        return "fake-token"

    monkeypatch.setattr(auth.token_manager, "get_token", fake_get_token)
    yield


def _wire_client(monkeypatch, handler) -> ServiceTitanClient:
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


def _page(items: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "page": 1,
            "pageSize": 200,
            "hasMore": False,
            "totalCount": len(items),
            "data": items,
        },
    )


# Out of createdOn order on purpose; mirrors the three real fileName groups.
_ATTACHMENTS = [
    {"id": 903, "fileName": "Attaches/b_cdv_photo_002.jpg", "title": None,
     "createdOn": "2026-09-30T21:22:00Z"},
    {"id": 700, "fileName": "Images/Material/abc123", "title": "Stock",
     "createdOn": "2026-09-29T15:00:00Z"},
    {"id": 800, "fileName": "CapturedDocs/invoice_1.pdf", "title": "Invoice",
     "createdOn": "2026-09-30T21:30:00Z"},
    {"id": 901, "fileName": "Attaches/a_cdv_photo_001.jpg", "title": None,
     "createdOn": "2026-09-30T21:20:00Z"},
    {"id": 902, "fileName": "Attaches/c_cdv_photo_003.JPG", "title": None,
     "createdOn": "2026-09-30T21:21:00Z"},
]

_JOB = {
    "id": 55,
    "jobNumber": "55",
    "businessUnitId": 7,
    "jobTypeId": 9,
    "completedOn": "2026-10-01T02:30:00Z",  # 2026-09-30 in America/Chicago
}


def _make_handler(seen: list[httpx.Request], *, fail_ids: set[int] = frozenset(),
                  jobs: list[dict] | None = None):
    """Fake ServiceTitan + storage host. Records every request in `seen`."""

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        path = req.url.path
        if req.url.host == BLOB_HOST:
            att_id = int(path.rsplit("/", 1)[-1])
            if att_id in fail_ids:
                return httpx.Response(500, text="boom")
            return httpx.Response(
                200, content=JPEG + str(att_id).encode(),
                headers={"content-type": "application/octet-stream"},
            )
        if "/jobs/attachment/" in path:
            att_id = path.rsplit("/", 1)[-1]
            return httpx.Response(
                302, headers={"location": f"https://{BLOB_HOST}/c/{att_id}?sig=SECRET"}
            )
        if path.endswith("/attachments"):
            return _page(_ATTACHMENTS)
        if path.endswith("/settings/v2/tenant/123/business-units"):
            return _page([{"id": 7, "name": "East - HVAC Residential Install"}])
        if path.endswith("/jpm/v2/tenant/123/job-types"):
            return _page([{"id": 9, "name": "Install/Full System"}])
        if path.endswith("/jpm/v2/tenant/123/jobs/55"):
            return httpx.Response(200, json=_JOB)
        if path.endswith("/jpm/v2/tenant/123/jobs"):
            return _page(jobs if jobs is not None else [_JOB])
        return httpx.Response(404, text=f"unexpected {path}")

    return handler


_PREFIX = "acme__East_-_HVAC_Residential_Install__Install_Full_System__55__2026-09-30__"


# ── Unit: helpers ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "file_name,kind",
    [
        ("Attaches/735a_cdv_photo_003-qloagje4dca.jpg", "field_photo"),
        ("Attaches/x.HEIC", "field_photo"),
        ("Attaches/notes.pdf", "document"),
        ("Images/Material/abc123", "image"),  # extensionless stock image
        ("Images/Equipment/unit.png", "image"),
        ("CapturedDocs/invoice.pdf", "document"),
        ("Other/pic.webp", "image"),
        (None, "document"),
    ],
)
def test_classify(file_name, kind):
    assert classify(file_name) == kind


def test_selected_modes():
    assert selected("field_photo", "field_photos")
    assert not selected("image", "field_photos")
    assert selected("image", "images") and selected("field_photo", "images")
    assert not selected("document", "images")
    assert selected("document", "all")
    with pytest.raises(ValueError):
        selected("image", "everything")


def test_local_date_converts_utc_to_chicago():
    assert local_date("2026-10-01T02:30:00Z", "America/Chicago") == "2026-09-30"
    assert local_date("2026-10-01T02:30:00Z", "UTC") == "2026-10-01"
    # 7-digit fraction, as ServiceTitan sometimes emits.
    assert local_date("2026-10-01T02:30:00.1234567Z", "America/Chicago") == "2026-09-30"


def test_local_date_rejects_unknown_timezone():
    with pytest.raises(ValueError):
        local_date("2026-10-01T02:30:00Z", "Mars/Olympus")


def test_build_filename_sanitizes_and_zero_pads():
    name = build_filename(
        "acme", "East - HVAC Residential Install", "Install/Full System",
        "1234567", "2026-09-30", 1, 7654321, "jpg",
    )
    assert name == (
        "acme__East_-_HVAC_Residential_Install__Install_Full_System__"
        "1234567__2026-09-30__01_7654321.jpg"
    )


def test_ext_from_content_type():
    assert ext_from_content_type("image/jpeg; charset=binary") == "jpg"
    assert ext_from_content_type("application/octet-stream") == "bin"
    assert ext_from_content_type(None) == "bin"


def test_resolve_photo_dir_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("ST_OUTPUTS_DIR", str(tmp_path / "env"))
    assert resolve_photo_dir(None) == tmp_path / "env" / "job_photos"
    assert resolve_photo_dir(str(tmp_path / "explicit")) == tmp_path / "explicit"
    assert (tmp_path / "explicit").is_dir()


# ── client.download ───────────────────────────────────────────────────


async def test_download_follows_redirect_without_servicetitan_headers(monkeypatch):
    seen: list[httpx.Request] = []
    c = _wire_client(monkeypatch, _make_handler(seen))

    content, content_type = await c.download("/forms/v2/tenant/123/jobs/attachment/901")

    assert content == JPEG + b"901"
    assert content_type == "application/octet-stream"
    first, second = seen
    assert first.url.host == "api.servicetitan.io"
    assert first.headers["Authorization"] == "Bearer fake-token"
    assert second.url.host == BLOB_HOST
    assert "authorization" not in second.headers
    assert "st-app-key" not in second.headers


async def test_download_returns_direct_200_body(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, content=b"PDFBYTES",
                              headers={"content-type": "application/pdf"})

    c = _wire_client(monkeypatch, handler)
    assert await c.download("/forms/v2/tenant/123/jobs/attachment/1") == (
        b"PDFBYTES", "application/pdf",
    )
    assert len(seen) == 1


async def test_download_empty_body_raises(monkeypatch):
    c = _wire_client(monkeypatch, lambda req: httpx.Response(200, content=b""))
    with pytest.raises(RuntimeError):
        await c.download("/forms/v2/tenant/123/jobs/attachment/1")


async def test_download_storage_error_hides_signed_query(monkeypatch):
    seen: list[httpx.Request] = []
    c = _wire_client(monkeypatch, _make_handler(seen, fail_ids={901}))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await c.download("/forms/v2/tenant/123/jobs/attachment/901")
    assert "SECRET" not in str(exc.value)
    assert BLOB_HOST in str(exc.value)


# ── list_job_attachments ──────────────────────────────────────────────


async def test_list_job_attachments_path_and_footer(monkeypatch):
    seen: list[httpx.Request] = []
    _wire_client(monkeypatch, _make_handler(seen))

    out = await server.list_job_attachments(tenant="acme", job_id=55)

    assert seen[0].url.path == "/forms/v2/tenant/123/jobs/55/attachments"
    assert "Attaches/a_cdv_photo_001.jpg" in out
    assert "(Showing 5 of 5 results" in out and "hasMore=False" in out


# ── download_job_photos: single job ───────────────────────────────────


async def test_single_job_writes_field_photos_in_capture_order(monkeypatch, tmp_path):
    seen: list[httpx.Request] = []
    _wire_client(monkeypatch, _make_handler(seen))

    out = json.loads(
        await server.download_job_photos(
            tenant="acme", job_id=55, output_dir=str(tmp_path)
        )
    )

    files = sorted(p.name for p in tmp_path.iterdir() if p.name != "manifest.jsonl")
    assert files == [
        _PREFIX + "01_901.jpg",
        _PREFIX + "02_902.jpg",
        _PREFIX + "03_903.jpg",
    ]
    assert (tmp_path / files[0]).read_bytes() == JPEG + b"901"
    assert out["files_written"] == 3
    assert out["jobs_matched"] == 1 and out["jobs_with_files"] == 1
    assert out["jobs_without_files"] == [] and out["errors"] == []
    assert out["truncated"] is False
    assert out["bytes_written"] == sum(len(JPEG) + 3 for _ in files)

    lines = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text().splitlines()
    ]
    assert [m["attachment_id"] for m in lines] == [901, 902, 903]
    assert lines[0]["saved_as"] == files[0]
    assert lines[0]["attachment_created_on"] == "2026-09-30T21:20:00Z"
    assert lines[0]["file_name"] == "Attaches/a_cdv_photo_001.jpg"
    assert lines[0]["business_unit"] == "East - HVAC Residential Install"
    assert lines[0]["completed_on"] == "2026-10-01T02:30:00Z"


async def test_include_all_writes_pdf_and_extensionless_image(monkeypatch, tmp_path):
    seen: list[httpx.Request] = []
    _wire_client(monkeypatch, _make_handler(seen))

    out = json.loads(
        await server.download_job_photos(
            tenant="acme", job_id=55, include="all", output_dir=str(tmp_path)
        )
    )

    assert out["files_written"] == 5
    names = {p.name for p in tmp_path.iterdir()}
    # Sorted by createdOn across all kinds: 700, 901, 902, 903, 800.
    assert _PREFIX + "01_700.bin" in names
    assert _PREFIX + "05_800.pdf" in names

    again = json.loads(
        await server.download_job_photos(
            tenant="acme", job_id=55, include="all", output_dir=str(tmp_path)
        )
    )
    assert again["files_written"] == 0 and again["files_skipped_existing"] == 5


async def test_rerun_skips_existing_unless_overwrite(monkeypatch, tmp_path):
    seen: list[httpx.Request] = []
    _wire_client(monkeypatch, _make_handler(seen))
    kwargs = dict(tenant="acme", job_id=55, output_dir=str(tmp_path))

    await server.download_job_photos(**kwargs)
    downloads_before = sum(1 for r in seen if r.url.host == BLOB_HOST)
    again = json.loads(await server.download_job_photos(**kwargs))

    assert again["files_written"] == 0
    assert again["files_skipped_existing"] == 3
    assert sum(1 for r in seen if r.url.host == BLOB_HOST) == downloads_before
    assert len((tmp_path / "manifest.jsonl").read_text().splitlines()) == 3

    forced = json.loads(await server.download_job_photos(**kwargs, overwrite=True))
    assert forced["files_written"] == 3 and forced["files_skipped_existing"] == 0


async def test_one_failed_file_is_reported_and_others_land(monkeypatch, tmp_path):
    seen: list[httpx.Request] = []
    _wire_client(monkeypatch, _make_handler(seen, fail_ids={902}))

    out = json.loads(
        await server.download_job_photos(
            tenant="acme", job_id=55, output_dir=str(tmp_path)
        )
    )

    assert out["files_written"] == 2
    assert len(out["errors"]) == 1
    err = out["errors"][0]
    assert err["job_id"] == 55 and err["attachment_id"] == 902
    assert "500" in err["error"] and "SECRET" not in err["error"]
    names = {p.name for p in tmp_path.iterdir()}
    assert not any(n.endswith(".partial") for n in names)
    assert _PREFIX + "01_901.jpg" in names and _PREFIX + "03_903.jpg" in names


async def test_single_job_not_completed_returns_error(monkeypatch, tmp_path):
    def handler(req):
        return httpx.Response(200, json={**_JOB, "completedOn": None})

    _wire_client(monkeypatch, handler)
    out = await server.download_job_photos(
        tenant="acme", job_id=55, output_dir=str(tmp_path)
    )
    assert out.startswith("Error:") and "not completed" in out


# ── download_job_photos: batch ────────────────────────────────────────


async def test_batch_maps_filters_onto_jobs_query(monkeypatch, tmp_path):
    seen: list[httpx.Request] = []
    _wire_client(monkeypatch, _make_handler(seen))

    out = json.loads(
        await server.download_job_photos(
            tenant="acme",
            completed_on_or_after="2026-09-28",
            completed_before="2026-10-05",
            business_unit_id=7,
            job_type_id=9,
            output_dir=str(tmp_path),
        )
    )

    jobs_req = next(r for r in seen if r.url.path == "/jpm/v2/tenant/123/jobs")
    q = jobs_req.url.params
    assert q["jobStatus"] == "Completed"
    assert q["completedOnOrAfter"] == "2026-09-28"
    assert q["completedBefore"] == "2026-10-05"
    assert q["businessUnitId"] == "7"
    assert q["jobTypeId"] == "9"
    assert out["jobs_matched"] == 1 and out["files_written"] == 3
    assert out["truncated"] is False


async def test_batch_max_jobs_truncates(monkeypatch, tmp_path):
    seen: list[httpx.Request] = []
    jobs = [{**_JOB, "id": 55 + i, "jobNumber": str(55 + i)} for i in range(3)]
    _wire_client(monkeypatch, _make_handler(seen, jobs=jobs))

    out = json.loads(
        await server.download_job_photos(
            tenant="acme",
            completed_on_or_after="2026-09-28",
            max_jobs=2,
            output_dir=str(tmp_path),
        )
    )

    assert out["jobs_matched"] == 2
    assert out["truncated"] is True
    assert out["files_written"] == 6


async def test_batch_reports_jobs_without_matching_files(monkeypatch, tmp_path):
    def handler(req):
        path = req.url.path
        if path.endswith("/attachments"):
            return _page([_ATTACHMENTS[2]])  # PDF only
        if path.endswith("/jobs"):
            return _page([_JOB])
        return _page([])

    _wire_client(monkeypatch, handler)
    out = json.loads(
        await server.download_job_photos(
            tenant="acme", completed_on_or_after="2026-09-28", output_dir=str(tmp_path)
        )
    )
    assert out["jobs_without_files"] == [55]
    assert out["jobs_with_files"] == 0 and out["files_written"] == 0


# ── Validation and schema ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({}, "exactly one"),
        ({"job_id": 55, "completed_on_or_after": "2026-09-28"}, "exactly one"),
        ({"job_id": 55, "business_unit_id": 7}, "batch mode"),
        ({"job_id": 55, "include": "everything"}, "include"),
        ({"job_id": 55, "timezone": "Mars/Olympus"}, "timezone"),
        ({"completed_on_or_after": "2026-09-28", "max_jobs": 0}, "max_jobs"),
    ],
)
async def test_validation_errors(monkeypatch, tmp_path, kwargs, needle):
    seen: list[httpx.Request] = []
    _wire_client(monkeypatch, _make_handler(seen))

    out = await server.download_job_photos(
        tenant="acme", output_dir=str(tmp_path), **kwargs
    )

    assert out.startswith("Error:") and needle in out
    assert seen == []  # rejected before any API call


async def test_tools_expose_expected_parameters():
    tools = await server.mcp.list_tools()
    by_name = {t.name: t for t in tools}

    props = by_name["download_job_photos"].inputSchema["properties"]
    assert set(props) == {
        "tenant", "job_id", "completed_on_or_after", "completed_before",
        "business_unit_id", "job_type_id", "include", "output_dir",
        "timezone", "overwrite", "max_jobs",
    }
    assert by_name["download_job_photos"].inputSchema["required"] == ["tenant"]
    assert by_name["list_job_attachments"].inputSchema["required"] == [
        "tenant", "job_id",
    ]
