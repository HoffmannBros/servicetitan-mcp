# v1.3.0: One "Tenants" Field Instead of Numbered Slots

_Written 2026-11-12. Status: done, released as v1.3.0 on 2026-11-12. Justin
confirmed every **Decided** item. Step 1 showed the secret field keeps `|` and
`;` exactly and turns pasted line breaks into spaces. The client secret shape
check was left out: the stored secrets are encrypted, so the `cs<digit>.`
prefix could not be confirmed._

One session can run this whole plan. It is small enough not to need
sub-sessions.

## Why

The Claude Desktop extension (.mcpb) collects credentials through numbered
tenant slots: five fields per tenant (name, tenant ID, client ID, client
secret, app key). Current Claude Desktop (seen in 5.1231) refuses a settings
save that changes more than **12 values** and shows "error saving". Its check
lives in the app's `setExtensionSettings` path; the warning in the log is
`Did not save the settings sent for "...": it changes more than one box can
show; save fewer changes at a time`. How it counts:

- Every **secret** field the form sends counts, blank or not.
- A plain field counts only when its value differs from the default.
- Values not yet stored that equal their default are not counted.
- Separate limits: 1,000 characters per shown value and 3,000 characters in
  total across the shown (non-secret) changes.

With 5 slots (10 secrets), a fresh install's first save was 13 or more
changes, so nobody could finish installing. v1.2.1 cut the bundle to 4 slots,
which works (confirmed on a coworker's fresh install), but:

- **Four tenants is a hard ceiling.** A fifth slot puts the first save back
  over 12. Buying another company means the extension can't hold it.
- Adding tenants after the first save has to happen one or two per save.

## Goal

Replace the numbered slots with **one secret text field** that holds every
tenant. Any number of tenants is then a single change, and Justin can hand
each coworker one string to paste.

## Decided (confirm with Justin)

1. **Format: one line, `;` between tenants, `|` between fields.**
   ```
   st_louis | <tenant id> | <client id> | <client secret> | <app key> ; nashville | ...
   ```
   Secret fields are almost certainly rendered as a password input, which
   cannot hold line breaks, so the format must work on one line. Also accept
   newlines as a tenant separator (harmless, and nicer in a plain env var).
   Whitespace around separators is ignored. A trailing `;` is fine.
2. **Keep the four slots for one release as a fallback.** Upgrading keeps
   settings only for fields the new manifest still declares, so removing the
   slots would silently wipe every coworker's tenants on upgrade. In 1.3.0:
   - Slots stay, retitled "Legacy: Tenant N — ..." and moved below the new
     field. Tenant 1's fields become `required: false`.
   - The server reads both and merges them. The same name in both places is an
     error.
   - Plan to remove the slots in 1.4.0, after coworkers have pasted their line.
   - Budget check: 8 slot secrets + 1 new secret = 9, plus the export folder =
     10. Under 12.
3. **Errors never contain credential values.** Only the entry number, the
   tenant name (once parsed), and which field is wrong, for example
   `Tenants entry 3 (nashville) has 4 fields; expected 5: name | tenant id |
   client id | client secret | app key`.
4. **Catch swapped columns** with shape checks on values whose format is
   certain: tenant ID is all digits, client ID starts with `cid.`, client
   secret starts with `cs` + digit + `.`. Confirm each prefix against the real
   credentials (check, don't print) before adding it. Skip any check that
   can't be confirmed, such as the app key's.
5. **A helper for Justin builds the line** from his existing classic env vars
   (`ST_TENANTS` + `ST_TENANT_<NAME>_*`) and copies it to the clipboard instead
   of printing it, so secrets don't land in terminal scrollback. `--print`
   opt-in for non-Windows. Lives in `scripts/` (excluded from the bundle).

## Steps

### 1. Verify the two unknowns in Claude Desktop first (30 minutes)

Before writing the parser, pack a throwaway bundle with only a sensitive
`tenants` string field (different `name` so it doesn't touch the real
install) and check on Justin's machine:

- Pasting a long one-line value with `|` and `;` into the secret field keeps
  every character. Read back the env var the server receives (length and
  separator counts only, never the value).
- A fresh install with only that field filled saves without "error saving".
- Whether the field accepts a pasted value with line breaks (expected: no;
  the format doesn't depend on it).

If the secret field mangles `|` or `;`, pick other separators and update
this plan before continuing. Uninstall the throwaway extension afterwards.

### 2. Parser in `servicetitan_mcp/config.py`

- New env var `ST_TENANTS_INLINE`, fed by the manifest from
  `${user_config.tenants}`. (Not `ST_TENANTS`, which already means the
  classic comma-separated roster.) Read it with `_env()` so blank and
  unsubstituted `${user_config.tenants}` count as absent.
- `_parse_inline(raw) -> dict[str, TenantCredentials]`: split on `;` and
  newlines, drop empty entries, split each on `|`, strip, require exactly 5
  non-empty fields, normalize the name the same way slots do (lowercase,
  spaces to `_`, then `_NAME_RE`), apply the shape checks, reject duplicate
  names.
- `load_tenants()` order: `ST_TENANTS` (classic) wins as today. Otherwise
  merge inline + slots; duplicate names across the two raise. Neither present
  keeps the current "No tenants configured" error, updated to mention the new
  field first.
- Update the module docstring, `_SLOT_COUNT` comment, and the `server.py`
  module docstring.

### 3. Manifest

- Add `tenants` first in `user_config`: `type: string`, `sensitive: true`,
  `required: false`, title "Tenants", description with the format and an
  example using placeholders only.
- Map `ST_TENANTS_INLINE: ${user_config.tenants}` in `mcp_config.env`.
- Retitle slot fields "Legacy: Tenant N — ..." and set Tenant 1's to
  `required: false`.
- Bump `manifest.json` and `pyproject.toml` to 1.3.0 together.

### 4. Helper script `scripts/tenants_line.py`

Reads the classic env config through `config.load_tenants()`, builds the line
in roster order, copies it with `clip` on Windows (or `pbcopy`), and prints
only "Copied N tenants: st_louis, nashville, ...". `--only st_louis,nashville`
to build a subset for a coworker who shouldn't get every tenant. Register it
nowhere else; `scripts/` is already in `.mcpbignore`.

### 5. Tests

- New `tests/test_config_inline.py`: one tenant; several; newline and `;`
  separators; whitespace and trailing `;`; wrong field count names the entry;
  empty field names the field; bad name; duplicate within inline; duplicate
  across inline and slots; each shape check; blank and unsubstituted
  placeholder count as absent; `ST_TENANTS` still takes precedence; **no error
  message contains any credential value** (assert on dummy secrets).
- Update `tests/test_manifest_budget.py`: first-save count is all secrets +
  required plain fields + 1, still ≤ 12, and the `tenants` field is mapped.
- Keep `tests/test_config_slots.py` passing unchanged.
- Full suite: `PYTHONPATH=. .venv/Scripts/python -m pytest -q`.

### 6. Docs

- README "Install as a Claude Desktop extension": paste the Tenants line
  (get it from Justin, or build it from the format); the legacy fields are
  only for existing installs. Drop the "two per save" note.
- README "Building the .mcpb bundle": `ST_TENANTS_INLINE` and the slots.
- AGENTS.md "Required env vars": add `ST_TENANTS_INLINE`.

### 7. Verify and release

- `mcpb validate manifest.json`, `mcpb pack . dist/servicetitan-mcp-1.3.0.mcpb`,
  then the README's secret pre-flight (`unzip -l`, the `cs1.|cs2.` grep).
- Fresh install (uninstall first): paste a line with all four tenants, save
  once, confirm `list_tenants` shows all four.
- Upgrade test: install 1.2.1 with slots filled, then 1.3.0; slots still work
  with the Tenants field blank.
- Run `python scripts/smoke_live.py <tenant>` for one tenant.
- Release like 1.2.1: commit to `main`, tag `v1.3.0`, `gh release create`
  with the `.mcpb`. Notes must tell existing users to paste their line now
  because the legacy fields go away in 1.4.0.

## Not in this plan

- Removing the slots (1.4.0, after coworkers migrate).
- A separate install script or a secrets file outside Claude's settings.
  Rejected: it puts secrets on disk and needs per-OS scripts, while Claude
  already encrypts the secret field.
