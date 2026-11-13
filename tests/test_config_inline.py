"""Tests for the one-line Tenants field (ST_TENANTS_INLINE, .mcpb bundle 1.3.0+).

Format: `name | tenant id | client id | client secret | app key ; name | ...`.
Locks in parsing, merging with the legacy slots, precedence of ST_TENANTS, and
that no error message ever contains a credential value.
"""

from __future__ import annotations

import os

import pytest

from servicetitan_mcp import config

SECRET = "cs1.SUPERSECRETVALUE"
APP_KEY = "ak.APPKEYVALUE"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("ST_"):
            monkeypatch.delenv(key, raising=False)
    config._reset_cache_for_tests()
    yield
    config._reset_cache_for_tests()


def _entry(name: str, tid: str = "1234567", cid: str | None = None,
           secret: str = SECRET, key: str = APP_KEY) -> str:
    cid = cid if cid is not None else f"cid.{name.replace(' ', '')}"
    return f"{name} | {tid} | {cid} | {secret} | {key}"


def _inline(monkeypatch, value: str) -> None:
    monkeypatch.setenv("ST_TENANTS_INLINE", value)


def _load_error(monkeypatch, value: str) -> str:
    _inline(monkeypatch, value)
    with pytest.raises(RuntimeError) as exc:
        config.load_tenants()
    msg = str(exc.value)
    for secret in (SECRET, APP_KEY, "SUPERSECRET", "APPKEYVALUE"):
        assert secret not in msg, f"credential leaked into error: {msg}"
    return msg


def test_one_tenant(monkeypatch):
    _inline(monkeypatch, _entry("acme"))
    t = config.get_tenant("acme")
    assert (t.tenant_id, t.client_id, t.client_secret, t.app_key) == (
        "1234567", "cid.acme", SECRET, APP_KEY,
    )


def test_several_tenants_keep_order(monkeypatch):
    _inline(monkeypatch, " ; ".join(_entry(n) for n in ("st_louis", "nashville", "blue_sky")))
    assert list(config.load_tenants()) == ["st_louis", "nashville", "blue_sky"]


def test_newline_and_semicolon_separators(monkeypatch):
    _inline(monkeypatch, f"{_entry('a')}\n{_entry('b')}\r\n{_entry('c')} ; {_entry('d')}")
    assert list(config.load_tenants()) == ["a", "b", "c", "d"]


def test_whitespace_and_trailing_semicolon(monkeypatch):
    _inline(monkeypatch, f"  acme|1234567|cid.x|{SECRET}|{APP_KEY} ;  ;\n other  |  42  | cid.y | s | k ;  ")
    tenants = config.load_tenants()
    assert list(tenants) == ["acme", "other"]
    assert tenants["other"].tenant_id == "42"
    assert tenants["other"].client_secret == "s"


def test_name_normalized(monkeypatch):
    _inline(monkeypatch, _entry("St Louis"))
    assert list(config.load_tenants()) == ["st_louis"]


def test_wrong_field_count_names_entry(monkeypatch):
    bad = f"nashville | 1234567 | cid.n | {SECRET}"
    msg = _load_error(monkeypatch, f"{_entry('acme')} ; {bad}")
    assert "Tenants entry 2 (nashville) has 4 fields; expected 5" in msg
    assert "name | tenant id | client id | client secret | app key" in msg


def test_line_breaks_turned_to_spaces_hint(monkeypatch):
    """Claude Desktop turns pasted line breaks into spaces, merging entries."""
    msg = _load_error(monkeypatch, f"{_entry('acme')} {_entry('other')}")
    assert "has 9 fields" in msg
    assert "';'" in msg


def test_empty_field_names_the_field(monkeypatch):
    msg = _load_error(monkeypatch, f"acme | 1234567 | cid.a |  | {APP_KEY}")
    assert "Tenants entry 1 (acme) has an empty client secret" in msg


def test_empty_name(monkeypatch):
    msg = _load_error(monkeypatch, f" | 1234567 | cid.a | {SECRET} | {APP_KEY}")
    assert "Tenants entry 1 has an empty name" in msg


def test_bad_name(monkeypatch):
    msg = _load_error(monkeypatch, _entry("9bad!"))
    assert "Tenants entry 1 has an invalid name" in msg
    assert "9bad" not in msg


def test_secret_in_name_position_not_echoed(monkeypatch):
    """Swapped columns can put a secret first; it must not appear in the error."""
    msg = _load_error(monkeypatch, f"{SECRET} | 1234567 | cid.a | acme | {APP_KEY}")
    assert "Tenants entry 1" in msg


def test_duplicate_within_inline(monkeypatch):
    msg = _load_error(monkeypatch, f"{_entry('acme')} ; {_entry('ACME')}")
    assert "Duplicate tenant name 'acme'" in msg
    assert "entry 2" in msg


def test_duplicate_across_inline_and_slots(monkeypatch):
    for field, val in (("NAME", "acme"), ("ID", "1"), ("CLIENT_ID", "cid.1"),
                       ("CLIENT_SECRET", "s"), ("APP_KEY", "k")):
        monkeypatch.setenv(f"ST_TENANT_SLOT1_{field}", val)
    msg = _load_error(monkeypatch, _entry("acme"))
    assert "'acme' is in both the Tenants field and a legacy" in msg


def test_merges_with_slots(monkeypatch):
    for field, val in (("NAME", "legacy"), ("ID", "1"), ("CLIENT_ID", "cid.1"),
                       ("CLIENT_SECRET", "s"), ("APP_KEY", "k")):
        monkeypatch.setenv(f"ST_TENANT_SLOT2_{field}", val)
    _inline(monkeypatch, _entry("acme"))
    assert set(config.load_tenants()) == {"acme", "legacy"}


def test_tenant_id_must_be_digits(monkeypatch):
    msg = _load_error(monkeypatch, f"acme | cid.a | 1234567 | {SECRET} | {APP_KEY}")
    assert "Tenants entry 1 (acme): the tenant id must be all digits" in msg
    assert "Check the field order" in msg


def test_client_id_must_start_with_cid(monkeypatch):
    msg = _load_error(monkeypatch, f"acme | 1234567 | {SECRET} | cid.a | {APP_KEY}")
    assert "the client id must start with 'cid.'" in msg


@pytest.mark.parametrize("value", ["", "   ", "${user_config.tenants}"])
def test_blank_or_placeholder_counts_as_absent(monkeypatch, value):
    _inline(monkeypatch, value)
    with pytest.raises(RuntimeError, match="No tenants configured"):
        config.load_tenants()


def test_only_separators_is_an_error(monkeypatch):
    msg = _load_error(monkeypatch, " ; ;\n ")
    assert "no tenants" in msg


def test_st_tenants_takes_precedence(monkeypatch):
    monkeypatch.setenv("ST_TENANTS", "roster")
    for suffix in ("ID", "CLIENT_ID", "CLIENT_SECRET", "APP_KEY"):
        monkeypatch.setenv(f"ST_TENANT_ROSTER_{suffix}", "x")
    _inline(monkeypatch, "this is not | valid")  # ignored, so no parse error
    assert list(config.load_tenants()) == ["roster"]


def test_no_config_error_mentions_tenants_field_first():
    with pytest.raises(RuntimeError) as exc:
        config.load_tenants()
    msg = str(exc.value)
    assert msg.index("ST_TENANTS_INLINE") < msg.index("ST_TENANTS=")
