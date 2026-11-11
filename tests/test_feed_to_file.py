"""Tests for `export_feed_to_file` and its pure helpers in `feed_export.py`.

The tool walks an export feed's continuation tokens server-side and streams
every record to a file. These lock in: the exact request path and query-param
names, the three stop reasons, the `.partial` then `os.replace` contract, input
validation, and dotted-path field selection. All data is made up.
"""

from __future__ import annotations

import csv
import io
import json

import httpx
import pytest

from servicetitan_mcp import auth, feed_export, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket
from servicetitan_mcp.feed_export import (
    FeedFileWriter,
    auto_filename,
    get_path,
    looks_like_date,
    resolve_feed_output_path,
    select_fields,
)


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


def _rec(i: int, **extra) -> dict:
    return {"id": i, "name": f"Rec {i}", "address": {"zip": f"0000{i}"}, **extra}


def _pager(pages: dict, requests: list):
    """Handler serving `pages[from_param]` -> (records, hasMore, continueFrom)."""

    def handler(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        key = req.url.params.get("from")
        records, has_more, cont = pages[key]
        return httpx.Response(
            200, json={"data": records, "hasMore": has_more, "continueFrom": cont}
        )

    return handler


def _meta(result: str) -> dict:
    assert not result.startswith("Error"), result
    return json.loads(result)


def _lines(path) -> list[dict]:
    return [json.loads(line) for line in open(path, encoding="utf-8").read().splitlines()]


# ── Tool: paging and stop reasons ──────────────────────────────────────


async def test_multi_page_writes_all_pages(monkeypatch, tmp_path):
    requests: list = []
    pages = {
        "2026-01-01": ([_rec(1), _rec(2)], True, "tokA"),
        "tokA": ([_rec(3), _rec(4)], True, "tokB"),
        "tokB": ([_rec(5)], False, "tokC"),
    }
    _wire_client(monkeypatch, _pager(pages, requests))

    meta = _meta(await server.export_feed_to_file(
        "t", "jpm", "jobs", from_token="2026-01-01", output_dir=str(tmp_path),
        include_recent_changes=True,
    ))

    # Exact path and query-param names on every page.
    assert [r.url.path for r in requests] == ["/jpm/v2/tenant/123/export/jobs"] * 3
    assert [r.url.params.get("from") for r in requests] == ["2026-01-01", "tokA", "tokB"]
    for r in requests:
        assert set(r.url.params.keys()) == {"from", "includeRecentChanges"}
        assert r.url.params["includeRecentChanges"] == "true"

    assert meta["row_count"] == 5
    assert meta["pages_fetched"] == 3
    assert meta["continue_from"] == "tokC"
    assert meta["caught_up"] is True
    assert meta["stopped_reason"] == "caught_up"
    assert meta["format"] == "jsonl"
    assert meta["columns"] == ["id", "name", "address"]
    assert [p["id"] for p in meta["preview"]] == [1, 2, 3]

    final = tmp_path / "feed_jpm_jobs_2026-01-01.jsonl"
    assert meta["file_path"] == str(final)
    assert [r["id"] for r in _lines(final)] == [1, 2, 3, 4, 5]
    assert not (tmp_path / (final.name + ".partial")).exists()


async def test_first_page_caught_up(monkeypatch, tmp_path):
    requests: list = []
    pages = {None: ([_rec(1)], False, "tokZ")}
    _wire_client(monkeypatch, _pager(pages, requests))

    meta = _meta(await server.export_feed_to_file(
        "t", "crm", "customers/contacts", output_dir=str(tmp_path)
    ))

    assert len(requests) == 1
    # No `from` and no includeRecentChanges when neither is requested.
    assert requests[0].url.path == "/crm/v2/tenant/123/export/customers/contacts"
    assert dict(requests[0].url.params) == {}
    assert meta["pages_fetched"] == 1
    assert meta["caught_up"] is True
    assert meta["stopped_reason"] == "caught_up"
    assert meta["continue_from"] == "tokZ"
    assert meta["file_path"] == str(tmp_path / "feed_crm_customers_contacts_full.jsonl")


async def test_max_pages_stop_returns_resume_token(monkeypatch, tmp_path):
    requests: list = []
    pages = {
        None: ([_rec(1)], True, "tokA"),
        "tokA": ([_rec(2)], True, "tokB"),
        "tokB": ([_rec(3)], False, "tokC"),
    }
    _wire_client(monkeypatch, _pager(pages, requests))

    meta = _meta(await server.export_feed_to_file(
        "t", "crm", "locations", output_dir=str(tmp_path), max_pages=2
    ))

    assert len(requests) == 2
    assert meta["pages_fetched"] == 2
    assert meta["row_count"] == 2
    assert meta["stopped_reason"] == "max_pages"
    assert meta["caught_up"] is False
    assert meta["continue_from"] == "tokB"


async def test_resume_token_names_file_resume(monkeypatch, tmp_path):
    requests: list = []
    pages = {"opaque-token-abc123": ([_rec(1)], False, "tokNext")}
    _wire_client(monkeypatch, _pager(pages, requests))

    meta = _meta(await server.export_feed_to_file(
        "t", "crm", "locations", from_token="opaque-token-abc123",
        output_dir=str(tmp_path),
    ))
    assert meta["file_path"] == str(tmp_path / "feed_crm_locations_resume.jsonl")


async def test_token_did_not_advance_stops(monkeypatch, tmp_path):
    requests: list = []
    pages = {
        None: ([_rec(1)], True, "tokA"),
        "tokA": ([_rec(2)], True, "tokA"),  # hasMore but same token: loop guard
    }
    _wire_client(monkeypatch, _pager(pages, requests))

    meta = _meta(await server.export_feed_to_file(
        "t", "crm", "customers", output_dir=str(tmp_path)
    ))

    assert len(requests) == 2
    assert meta["stopped_reason"] == "token_did_not_advance"
    assert meta["caught_up"] is False
    assert meta["continue_from"] == "tokA"
    assert meta["row_count"] == 2


async def test_exception_mid_stream_leaves_no_files(monkeypatch, tmp_path):
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200, json={"data": [_rec(1)], "hasMore": True, "continueFrom": "tokA"}
            )
        return httpx.Response(500, text="boom")

    _wire_client(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        await server.export_feed_to_file("t", "jpm", "jobs", output_dir=str(tmp_path))

    assert list(tmp_path.iterdir()) == []


async def test_existing_file_without_overwrite_is_untouched(monkeypatch, tmp_path):
    requests: list = []
    _wire_client(monkeypatch, _pager({None: ([_rec(1)], False, "t")}, requests))
    target = tmp_path / "out.jsonl"
    target.write_text("keep me", encoding="utf-8")

    result = await server.export_feed_to_file(
        "t", "jpm", "jobs", output_path=str(target)
    )

    assert result.startswith("Error:")
    assert "overwrite" in result
    assert target.read_text(encoding="utf-8") == "keep me"
    assert requests == []

    # overwrite=True replaces it.
    meta = _meta(await server.export_feed_to_file(
        "t", "jpm", "jobs", output_path=str(target), overwrite=True
    ))
    assert meta["row_count"] == 1
    assert _lines(target) == [_rec(1)]


async def test_csv_without_fields_is_error(monkeypatch, tmp_path):
    requests: list = []
    _wire_client(monkeypatch, _pager({None: ([_rec(1)], False, "t")}, requests))

    result = await server.export_feed_to_file(
        "t", "jpm", "jobs", format="csv", output_dir=str(tmp_path)
    )

    assert result.startswith("Error:")
    assert "fields" in result
    assert requests == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"format": "xlsx"},
        {"output_path": "a.jsonl", "output_dir": "d"},
        {"max_pages": 0},
        {"fields": ["ok", ""]},
    ],
    ids=["bad-format", "path-and-dir", "max-pages-zero", "empty-field"],
)
async def test_input_validation_errors(monkeypatch, tmp_path, kwargs):
    requests: list = []
    _wire_client(monkeypatch, _pager({None: ([], False, "t")}, requests))
    monkeypatch.chdir(tmp_path)

    result = await server.export_feed_to_file("t", "jpm", "jobs", **kwargs)

    assert result.startswith("Error:")
    assert requests == []


async def test_fields_dotted_path_and_missing_key_jsonl(monkeypatch, tmp_path):
    requests: list = []
    pages = {None: ([_rec(1), {"id": 2, "name": "No address"}], False, "t")}
    _wire_client(monkeypatch, _pager(pages, requests))

    meta = _meta(await server.export_feed_to_file(
        "t", "crm", "locations", fields=["id", "address.zip", "missing"],
        output_dir=str(tmp_path),
    ))

    assert meta["columns"] == ["id", "address.zip", "missing"]
    assert meta["preview"][0] == {"id": 1, "address.zip": "00001", "missing": None}
    assert _lines(meta["file_path"]) == [
        {"id": 1, "address.zip": "00001", "missing": None},
        {"id": 2, "address.zip": None, "missing": None},
    ]


async def test_fields_dotted_path_and_missing_key_csv(monkeypatch, tmp_path):
    requests: list = []
    pages = {None: ([_rec(1, tags=["a", "b"]), {"id": 2}], False, "t")}
    _wire_client(monkeypatch, _pager(pages, requests))

    meta = _meta(await server.export_feed_to_file(
        "t", "crm", "locations", format="csv",
        fields=["id", "address.zip", "address", "tags"],
        output_dir=str(tmp_path),
    ))

    assert meta["file_path"].endswith("feed_crm_locations_full.csv")
    with open(meta["file_path"], encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [
        ["id", "address.zip", "address", "tags"],
        ["1", "00001", '{"zip":"00001"}', '["a","b"]'],
        ["2", "", "", ""],
    ]


# ── Pure helpers ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "token, expected",
    [
        ("2026-01-01", True),
        ("2026-01-01T08:30:00Z", True),
        ("2026-01-01T08:30:00.123+00:00", True),
        ("eyJhbGciOi-opaque", False),
        ("", False),
        (None, False),
    ],
)
def test_looks_like_date(token, expected):
    assert looks_like_date(token) is expected


def test_auto_filename():
    assert auto_filename("jpm", "jobs", None, "jsonl") == "feed_jpm_jobs_full.jsonl"
    assert (
        auto_filename("crm", "customers/contacts", "2026-01-01", "csv")
        == "feed_crm_customers_contacts_2026-01-01.csv"
    )
    assert (
        auto_filename("crm", "locations", "2026-01-01T00:00:00Z", "jsonl")
        == "feed_crm_locations_2026-01-01T00_00_00Z.jsonl"
    )
    assert auto_filename("crm", "leads", "long-opaque-token", "jsonl") == "feed_crm_leads_resume.jsonl"


def test_resolve_feed_output_path_precedence(tmp_path, monkeypatch):
    common = dict(category="jpm", feed="jobs", from_token=None, fmt="jsonl", overwrite=False)

    exact = tmp_path / "sub" / "x.jsonl"
    assert resolve_feed_output_path(output_path=str(exact), output_dir=None, **common) == exact
    assert exact.parent.is_dir()

    d = tmp_path / "dir"
    assert (
        resolve_feed_output_path(output_path=None, output_dir=str(d), **common)
        == d / "feed_jpm_jobs_full.jsonl"
    )

    monkeypatch.setenv("ST_OUTPUTS_DIR", str(tmp_path / "env"))
    assert (
        resolve_feed_output_path(output_path=None, output_dir=None, **common)
        == tmp_path / "env" / "feed_jpm_jobs_full.jsonl"
    )

    monkeypatch.delenv("ST_OUTPUTS_DIR")
    monkeypatch.setattr(feed_export, "_DEFAULT_EXPORT_DIR", tmp_path / "default")
    assert (
        resolve_feed_output_path(output_path=None, output_dir=None, **common)
        == tmp_path / "default" / "feed_jpm_jobs_full.jsonl"
    )


def test_resolve_feed_output_path_errors(tmp_path):
    common = dict(category="jpm", feed="jobs", from_token=None, overwrite=False)
    with pytest.raises(ValueError):
        resolve_feed_output_path(output_path=None, output_dir=str(tmp_path), fmt="xlsx", **common)
    with pytest.raises(ValueError):
        resolve_feed_output_path(
            output_path=str(tmp_path / "a.jsonl"), output_dir=str(tmp_path), fmt="jsonl", **common
        )
    existing = tmp_path / "e.jsonl"
    existing.write_text("x")
    with pytest.raises(FileExistsError):
        resolve_feed_output_path(output_path=str(existing), output_dir=None, fmt="jsonl", **common)


def test_get_path_and_select_fields():
    rec = {"a": 1, "b": {"c": {"d": 4}, "n": None}, "l": [1]}
    assert get_path(rec, "a") == 1
    assert get_path(rec, "b.c.d") == 4
    assert get_path(rec, "b.c") == {"d": 4}
    assert get_path(rec, "b.n") is None
    assert get_path(rec, "missing") is None
    assert get_path(rec, "a.x") is None  # non-dict on the way down
    assert get_path(rec, "l.0") is None  # lists are not indexed
    assert select_fields(rec, None) is rec
    assert select_fields(rec, ["a", "b.c.d", "zz"]) == {"a": 1, "b.c.d": 4, "zz": None}


def test_writer_jsonl_whole_record():
    buf = io.StringIO()
    w = FeedFileWriter(buf, "jsonl")
    out = w.write_records([{"id": 1, "x": {"y": 2}}])
    assert out == [{"id": 1, "x": {"y": 2}}]
    assert buf.getvalue() == '{"id": 1, "x": {"y": 2}}\n'


def test_writer_csv_header_and_cells():
    buf = io.StringIO()
    w = FeedFileWriter(buf, "csv", ["id", "x.y", "z"])
    w.write_records([{"id": 1, "x": {"y": "a,b"}, "z": {"k": [1, 2]}}])
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    assert rows == [["id", "x.y", "z"], ["1", "a,b", '{"k":[1,2]}']]


def test_writer_rejects_csv_without_fields_and_bad_format():
    with pytest.raises(ValueError):
        FeedFileWriter(io.StringIO(), "csv")
    with pytest.raises(ValueError):
        FeedFileWriter(io.StringIO(), "xml", ["a"])
