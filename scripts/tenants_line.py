"""Build the one-line Tenants value for the Claude Desktop extension.

Reads the classic env config (`ST_TENANTS` + `ST_TENANT_<NAME>_*`) through
`config.load_tenants()` and copies the line to the clipboard, so secrets never
land in terminal scrollback. Prints only the tenant names.

    python scripts/tenants_line.py                         # every tenant
    python scripts/tenants_line.py --only st_louis,nashville
    python scripts/tenants_line.py --print                 # no clipboard tool

Paste the result into the extension's "Tenants" settings field.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from servicetitan_mcp import config  # noqa: E402


def build_line(tenants: list[config.TenantCredentials]) -> str:
    for t in tenants:
        for value in (t.tenant_id, t.client_id, t.client_secret, t.app_key):
            if any(c in value for c in "|;\r\n"):
                raise SystemExit(f"Tenant {t.name!r} has a value containing | ; or a line break; cannot encode it.")
    return " ; ".join(
        " | ".join((t.name, t.tenant_id, t.client_id, t.client_secret, t.app_key)) for t in tenants
    )


def copy_to_clipboard(text: str) -> bool:
    for cmd in (["clip"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated tenant names to include (default: all)")
    parser.add_argument("--print", action="store_true", help="print the line instead of copying it (shows secrets)")
    args = parser.parse_args()

    if not config._env("ST_TENANTS"):
        raise SystemExit("ST_TENANTS is not set. Run this from a shell with the classic tenant env vars.")
    tenants = config.load_tenants()
    if args.only:
        wanted = [n.strip().lower() for n in args.only.split(",") if n.strip()]
        missing = [n for n in wanted if n not in tenants]
        if missing:
            raise SystemExit(f"Not configured: {', '.join(missing)}. Configured: {', '.join(tenants)}")
        chosen = [tenants[n] for n in wanted]
    else:
        chosen = list(tenants.values())

    line = build_line(chosen)
    names = ", ".join(t.name for t in chosen)
    if args.print:
        print(line)
    elif copy_to_clipboard(line):
        print(f"Copied {len(chosen)} tenants: {names}")
    else:
        raise SystemExit("No clipboard tool found (clip, pbcopy, wl-copy, xclip). Rerun with --print.")


if __name__ == "__main__":
    main()
