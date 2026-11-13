"""Keep the .mcpb settings form under Claude Desktop's save limit.

Claude Desktop rejects an extension-settings save that changes more than 12
values ("error saving"). It counts every secret field the form sends, blank or
not, plus each plain field set to something other than its default. A fresh
install's first save is therefore: all secret fields + any required plain
fields + the export folder if changed. That must stay at or under 12, or nobody
can finish installing. Since 1.3.0 every tenant goes in the one secret Tenants
field, so the count no longer grows with the number of tenants.
"""

import json
from pathlib import Path

from servicetitan_mcp import config

SAVE_LIMIT = 12
MANIFEST = json.loads((Path(__file__).parent.parent / "manifest.json").read_text(encoding="utf-8"))


def test_first_save_fits_claude_desktop_limit():
    user_config = MANIFEST["user_config"]
    secrets = [k for k, v in user_config.items() if v.get("sensitive")]
    required_plain = [k for k, v in user_config.items() if v.get("required") and not v.get("sensitive")]
    first_save = len(secrets) + len(required_plain) + 1  # +1: export folder changed
    assert first_save <= SAVE_LIMIT, (
        f"A fresh install's first save changes {first_save} settings; Claude Desktop allows {SAVE_LIMIT}."
    )


def test_tenants_field_is_secret_optional_and_mapped():
    field = MANIFEST["user_config"]["tenants"]
    assert field["type"] == "string"
    assert field["sensitive"] is True
    assert field["required"] is False
    env = MANIFEST["server"]["mcp_config"]["env"]
    assert env[config._INLINE_VAR] == "${user_config.tenants}"


def test_no_tenant_field_is_required():
    """A fresh install fills only the Tenants field, so no slot may be required."""
    required = [k for k, v in MANIFEST["user_config"].items() if v.get("required")]
    assert required == []


def test_manifest_slots_match_config():
    user_config = MANIFEST["user_config"]
    slots = {k.split("_")[0] for k in user_config if k.startswith("tenant") and k[6:7].isdigit()}
    assert len(slots) == config._SLOT_COUNT
    env = MANIFEST["server"]["mcp_config"]["env"]
    for n in range(1, config._SLOT_COUNT + 1):
        for field in ("NAME", "ID", "CLIENT_ID", "CLIENT_SECRET", "APP_KEY"):
            assert f"ST_TENANT_SLOT{n}_{field}" in env
