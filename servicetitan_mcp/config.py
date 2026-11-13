"""Multi-tenant ServiceTitan credential registry.

Three ways to configure tenants:

1. `ST_TENANTS` (comma-separated names) plus per-tenant namespaced env vars
   `ST_TENANT_<UPPERCASE_NAME>_ID / _CLIENT_ID / _CLIENT_SECRET / _APP_KEY`.
   Names normalize to lowercase for lookups and uppercase for env-key matching.
   When set, it wins and the other two are ignored.
2. `ST_TENANTS_INLINE`: every tenant in one string, fed by the Claude Desktop
   `.mcpb` bundle's single secret "Tenants" field. Tenants are separated by `;`
   (or newlines), fields by `|`:
       name | tenant id | client id | client secret | app key ; name | ...
   One field means any number of tenants is a single settings change, which
   stays under Claude Desktop's 12-changes-per-save limit. (The settings field
   turns pasted line breaks into spaces, so the bundle relies on `;`.)
3. Legacy numbered slots `ST_TENANT_SLOT1_NAME / _ID / _CLIENT_ID /
   _CLIENT_SECRET / _APP_KEY` (up to `_SLOT_COUNT`), from bundles before 1.3.0.
   A slot with an empty name is skipped; gaps between slots are fine. Kept for
   one release so upgrades don't wipe existing settings; removal planned for
   1.4.0.

2 and 3 are merged; the same tenant name in both is an error. Error messages
never include credential values, only entry numbers, names, and field names.
"""

import os
import re
from dataclasses import dataclass

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

_PER_TENANT_VARS = (
    ("tenant_id", "ID"),
    ("client_id", "CLIENT_ID"),
    ("client_secret", "CLIENT_SECRET"),
    ("app_key", "APP_KEY"),
)

_LEGACY_VARS = ("ST_TENANT_ID", "ST_APP_KEY", "ST_CLIENT_ID", "ST_CLIENT_SECRET")

# Number of legacy numbered tenant slots still declared by the .mcpb bundle
# (kept for one release so upgrades from 1.2.x keep their settings). Claude
# Desktop rejects a settings save that changes more than 12 values and counts
# every secret field the form sends, blank or not: 4 slots x 2 secrets + the
# Tenants field + the export folder = 10. New tenants go in the Tenants field
# (ST_TENANTS_INLINE), which has no tenant limit.
_SLOT_COUNT = 4

_INLINE_VAR = "ST_TENANTS_INLINE"
_INLINE_FIELDS = ("name", "tenant id", "client id", "client secret", "app key")
_INLINE_FORMAT = "name | tenant id | client id | client secret | app key"

# Shape checks that catch swapped columns. Only formats confirmed against real
# credentials are checked: tenant IDs are all digits, client IDs start with
# `cid.`. Client secrets and app keys are not checked (prefix unconfirmed).
_SHAPE_CHECKS = (
    ("tenant_id", "tenant id", re.compile(r"^\d+$"), "must be all digits"),
    ("client_id", "client id", re.compile(r"^cid\."), "must start with 'cid.'"),
)


@dataclass(frozen=True)
class TenantCredentials:
    name: str
    tenant_id: str
    client_id: str
    client_secret: str
    app_key: str


class UnknownTenantError(ValueError):
    """Raised when a requested tenant name is not in the registry."""

    def __init__(self, name: str, valid: list[str]):
        self.requested = name
        self.valid = valid
        super().__init__(
            f"Unknown tenant {name!r}. Configured: {', '.join(valid) or '(none)'}. "
            "Call list_tenants for the authoritative list."
        )


_cache: dict[str, TenantCredentials] | None = None


def _parse_roster(raw: str) -> list[str]:
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        raise RuntimeError("ST_TENANTS is set but empty after parsing.")
    for n in names:
        if not _NAME_RE.match(n):
            raise RuntimeError(
                f"Invalid tenant name {n!r} in ST_TENANTS. "
                "Names must be lowercase, start with a letter, "
                "and use only [a-z0-9_-]."
            )
    seen: set[str] = set()
    deduped: list[str] = []
    for n in names:
        if n in seen:
            raise RuntimeError(f"Duplicate tenant name {n!r} in ST_TENANTS.")
        seen.add(n)
        deduped.append(n)
    return deduped


def _env(key: str) -> str | None:
    """Env value, treating empty/whitespace and unsubstituted MCPB placeholders
    as absent (Claude Desktop substitutes '' for blank optional fields, and some
    hosts leave the literal '${user_config.x}' when a field has no value)."""
    val = os.environ.get(key, "").strip()
    if not val or val.startswith("${user_config."):
        return None
    return val


def _normalize_name(raw: str) -> str:
    """Lowercase and turn spaces into underscores ("St Louis" -> "st_louis")."""
    return re.sub(r"\s+", "_", raw.strip().lower())


def _parse_inline(raw: str) -> dict[str, TenantCredentials]:
    """Parse the one-line Tenants value. Never puts a credential in an error."""
    entries = [e.strip() for e in re.split(r"[;\r\n]", raw)]
    entries = [e for e in entries if e]
    if not entries:
        raise RuntimeError(f"The Tenants field has no tenants. Expected: {_INLINE_FORMAT}")
    tenants: dict[str, TenantCredentials] = {}
    for i, entry in enumerate(entries, start=1):
        fields = [f.strip() for f in entry.split("|")]
        name = _normalize_name(fields[0])
        # Show the name only once it is a valid name: with swapped columns the
        # first position could hold a secret.
        label = f"Tenants entry {i}" + (f" ({name})" if _NAME_RE.match(name) else "")
        if len(fields) != len(_INLINE_FIELDS):
            hint = ""
            if len(fields) > len(_INLINE_FIELDS):
                hint = (
                    " Separate tenants with ';' (line breaks pasted into the "
                    "settings field turn into spaces)."
                )
            raise RuntimeError(
                f"{label} has {len(fields)} fields; expected {len(_INLINE_FIELDS)}: "
                f"{_INLINE_FORMAT}.{hint}"
            )
        for value, field_name in zip(fields, _INLINE_FIELDS):
            if not value:
                raise RuntimeError(
                    f"{label} has an empty {field_name}. Expected: {_INLINE_FORMAT}"
                )
        if not _NAME_RE.match(name):
            raise RuntimeError(
                f"Tenants entry {i} has an invalid name. Names must start with a "
                "letter and use only [a-z0-9_-] (uppercase and spaces are "
                f"normalized). Expected: {_INLINE_FORMAT}"
            )
        if name in tenants:
            raise RuntimeError(f"Duplicate tenant name {name!r} in Tenants (entry {i}).")
        values = dict(zip(("tenant_id", "client_id", "client_secret", "app_key"), fields[1:]))
        for key, field_name, pattern, rule in _SHAPE_CHECKS:
            if not pattern.match(values[key]):
                raise RuntimeError(
                    f"{label}: the {field_name} {rule}. Check the field order: "
                    f"{_INLINE_FORMAT}"
                )
        tenants[name] = TenantCredentials(name=name, **values)
    return tenants


def _load_slots() -> dict[str, TenantCredentials] | None:
    """Load tenants from ST_TENANT_SLOT1..N_* vars (Claude Desktop .mcpb path).

    Returns None if no slot has a name. A slot with an empty name is skipped,
    so gaps between filled slots are fine. A named slot missing any credential
    fails fast.
    """
    tenants: dict[str, TenantCredentials] = {}
    for n in range(1, _SLOT_COUNT + 1):
        prefix = f"ST_TENANT_SLOT{n}_"
        name = _env(prefix + "NAME")
        if name is None:
            continue
        # Forgive the two most likely typing habits in the settings form:
        # uppercase and spaces ("St Louis" -> "st_louis").
        name = _normalize_name(name)
        if not _NAME_RE.match(name):
            raise RuntimeError(
                f"Invalid tenant name {name!r} in Tenant {n}. "
                "Names must be lowercase, start with a letter, "
                "and use only [a-z0-9_-]."
            )
        if name in tenants:
            raise RuntimeError(f"Duplicate tenant name {name!r} (Tenant {n}).")
        values: dict[str, str] = {}
        for field, suffix in _PER_TENANT_VARS:
            val = _env(prefix + suffix)
            if val is None:
                raise RuntimeError(
                    f"Tenant {n} ({name!r}) is missing {prefix + suffix}. "
                    "Fill in all five fields for this tenant in the extension "
                    "settings, or clear its name to disable the slot."
                )
            values[field] = val
        tenants[name] = TenantCredentials(name=name, **values)
    return tenants or None


def _load_one(name: str) -> TenantCredentials:
    upper = name.upper()
    values: dict[str, str] = {}
    for field, suffix in _PER_TENANT_VARS:
        key = f"ST_TENANT_{upper}_{suffix}"
        val = os.environ.get(key)
        if not val:
            raise RuntimeError(f"Missing {key} for tenant {name!r}.")
        values[field] = val
    return TenantCredentials(name=name, **values)


def load_tenants() -> dict[str, TenantCredentials]:
    """Load all configured tenants. Cached after first call.

    `ST_TENANTS` (namespaced vars) takes precedence; otherwise merges the
    .mcpb bundle's Tenants field (`ST_TENANTS_INLINE`) with its legacy
    numbered `ST_TENANT_SLOT*` vars. Raises
    RuntimeError on missing/invalid config. Hard-errors if the legacy
    single-tenant env vars are set but `ST_TENANTS` is unset, to steer users
    through the migration.
    """
    global _cache
    if _cache is not None:
        return _cache

    roster = os.environ.get("ST_TENANTS", "").strip()
    if not roster:
        inline_raw = _env(_INLINE_VAR)
        inline_tenants = _parse_inline(inline_raw) if inline_raw is not None else {}
        slot_tenants = _load_slots() or {}
        both = [n for n in inline_tenants if n in slot_tenants]
        if both:
            raise RuntimeError(
                f"Tenant {both[0]!r} is in both the Tenants field and a legacy "
                "Tenant N slot. Keep it in the Tenants field and clear that "
                "slot's name."
            )
        merged = {**inline_tenants, **slot_tenants}
        if merged:
            _cache = merged
            return merged
        legacy_present = [v for v in _LEGACY_VARS if os.environ.get(v)]
        if legacy_present:
            raise RuntimeError(
                "ST_TENANTS is not set, but legacy single-tenant vars are present: "
                f"{', '.join(legacy_present)}. This server is now multi-tenant. "
                "Set ST_TENANTS=<comma-separated names> and namespaced vars "
                "ST_TENANT_<NAME>_ID / _CLIENT_ID / _CLIENT_SECRET / _APP_KEY per tenant. "
                "See README for the full migration."
            )
        raise RuntimeError(
            "No tenants configured. In the Claude Desktop extension, fill in the "
            f"Tenants field ({_INLINE_VAR}) with one line: {_INLINE_FORMAT} ; "
            "next tenant | ... Or set ST_TENANTS=<comma-separated names> plus "
            "ST_TENANT_<NAME>_ID / _CLIENT_ID / _CLIENT_SECRET / _APP_KEY per "
            "tenant. Legacy Tenant N slots (ST_TENANT_SLOT1_*) are still read."
        )

    names = _parse_roster(roster)
    tenants = {name: _load_one(name) for name in names}
    _cache = tenants
    return tenants


def tenant_names() -> list[str]:
    """List of configured tenant names, in roster order."""
    return list(load_tenants().keys())


def get_tenant(name: str) -> TenantCredentials:
    """Look up a tenant by name. Raises UnknownTenantError if not configured."""
    key = name.strip().lower()
    tenants = load_tenants()
    if key not in tenants:
        raise UnknownTenantError(name, list(tenants.keys()))
    return tenants[key]


def _reset_cache_for_tests() -> None:
    """Clear the module cache — tests only."""
    global _cache
    _cache = None
