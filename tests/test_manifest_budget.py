"""Keep the .mcpb settings form under Claude Desktop's save limit.

Claude Desktop rejects an extension-settings save that changes more than 12
values ("error saving"). It counts every secret field the form sends, blank or
not, plus each plain field set to something other than its default. A fresh
install's first save is therefore: all secret fields + Tenant 1's plain fields
+ the export folder if changed. That must stay at or under 12, or nobody can
finish installing.
"""

import json
from pathlib import Path

from servicetitan_mcp import config

SAVE_LIMIT = 12
MANIFEST = json.loads((Path(__file__).parent.parent / "manifest.json").read_text(encoding="utf-8"))


def test_first_save_fits_claude_desktop_limit():
    user_config = MANIFEST["user_config"]
    secrets = [k for k, v in user_config.items() if v.get("sensitive")]
    tenant1_plain = [k for k, v in user_config.items() if k.startswith("tenant1_") and not v.get("sensitive")]
    first_save = len(secrets) + len(tenant1_plain) + 1  # +1: export folder changed
    assert first_save <= SAVE_LIMIT, (
        f"A fresh install's first save changes {first_save} settings; Claude Desktop allows {SAVE_LIMIT}."
    )


def test_manifest_slots_match_config():
    user_config = MANIFEST["user_config"]
    slots = {k.split("_")[0] for k in user_config if k.startswith("tenant")}
    assert len(slots) == config._SLOT_COUNT
    env = MANIFEST["server"]["mcp_config"]["env"]
    for n in range(1, config._SLOT_COUNT + 1):
        for field in ("NAME", "ID", "CLIENT_ID", "CLIENT_SECRET", "APP_KEY"):
            assert f"ST_TENANT_SLOT{n}_{field}" in env
