"""Helpers for `export_feed_to_file`: output-path resolution, dotted-path
field selection, and streaming JSONL/CSV serialization of export-feed records.

Pure (no async, no network), like `report_export.py`, so the naming and
serialization rules can be unit-tested in isolation. Export-feed records are
nested JSON objects with no fixed column set, unlike report rows (positional
arrays aligned to `fields[]`), so this module has its own writer:

  jsonl  one JSON object per line: the record as returned, or only the
         selected `fields` (keyed by the dotted path as given).
  csv    only with `fields`; the caller chooses the columns. Nested values
         (dicts, lists) are written as compact JSON text in the cell.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

from .report_export import _DEFAULT_EXPORT_DIR, _expand_path, _sanitize

FORMATS = ("jsonl", "csv")
_EXT = {"jsonl": "jsonl", "csv": "csv"}

# `from` accepts a date ("2024-01-01") or a full timestamp; anything else is
# an opaque continuation token.
_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)

_MISSING = object()


def looks_like_date(token: str | None) -> bool:
    """True when `token` is a date/timestamp rather than a continuation token."""
    return bool(token) and bool(_DATE_RE.match(token.strip()))


def auto_filename(category: str, feed: str, from_token: str | None, ext: str) -> str:
    """`feed_<category>_<feed>_<start>.<ext>`.

    `<start>` is the sanitized date when `from_token` looks like one, `full`
    when it is absent, and `resume` for an opaque continuation token (they are
    long and meaningless in a filename). Deterministic, so collision detection
    with `overwrite` is meaningful.
    """
    if not from_token:
        start = "full"
    elif looks_like_date(from_token):
        start = _sanitize(from_token.strip())
    else:
        start = "resume"
    return f"feed_{_sanitize(category)}_{_sanitize(feed)}_{start}.{ext}"


def resolve_feed_output_path(
    *,
    output_path: str | None,
    output_dir: str | None,
    category: str,
    feed: str,
    from_token: str | None,
    fmt: str,
    overwrite: bool,
) -> Path:
    """Resolve the final destination file, creating the parent dir.

    Same precedence as `report_export.resolve_output_path`: `output_path`
    (exact file) > `output_dir` (auto-named) > `ST_OUTPUTS_DIR` (auto-named)
    > the in-repo `report_exports/` default (auto-named). Raises ValueError for
    a bad format or both targets given, FileExistsError if the target exists
    and `overwrite` is False.
    """
    if fmt not in _EXT:
        raise ValueError(f"format must be one of {list(FORMATS)}, got {fmt!r}")
    if output_path and output_dir:
        raise ValueError(
            "Pass at most one of `output_path` (exact file) or `output_dir` "
            "(directory); they are mutually exclusive."
        )

    if output_path:
        final = _expand_path(output_path)
    else:
        if output_dir:
            directory = _expand_path(output_dir)
        elif os.environ.get("ST_OUTPUTS_DIR"):
            directory = _expand_path(os.environ["ST_OUTPUTS_DIR"])
        else:
            directory = _DEFAULT_EXPORT_DIR
        final = directory / auto_filename(category, feed, from_token, _EXT[fmt])

    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists() and not overwrite:
        raise FileExistsError(
            f"{final} already exists. Pass overwrite=True to replace it, or "
            f"choose a different output_path/output_dir."
        )
    return final


def get_path(record: object, path: str) -> object:
    """Value at dotted `path` in nested dicts (`"address.zip"`), else None.

    A missing key, or a non-dict on the way down, yields None.
    """
    current = record
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part, _MISSING)
        if current is _MISSING:
            return None
    return current


def select_fields(record: object, fields: list[str] | None) -> object:
    """The record itself, or a dict of `{path: value}` for the chosen fields."""
    if not fields:
        return record
    return {path: get_path(record, path) for path in fields}


def _csv_cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return value


class FeedFileWriter:
    """Streaming writer for export-feed records in JSONL or CSV.

    CSV requires `fields` (the header row is written on construction).
    `write_records` returns the records after field selection, so the caller
    can build a preview without re-projecting.
    """

    def __init__(self, fileobj, fmt: str, fields: list[str] | None = None):
        if fmt not in _EXT:
            raise ValueError(f"format must be one of {list(FORMATS)}, got {fmt!r}")
        if fmt == "csv" and not fields:
            raise ValueError(
                "format='csv' needs `fields`: export records are nested "
                "objects with no fixed column set."
            )
        self.fmt = fmt
        self.fields = list(fields) if fields else None
        self._fileobj = fileobj
        self._csv = None
        if fmt == "csv":
            self._csv = csv.writer(fileobj)
            self._csv.writerow(self.fields)

    def write_records(self, records: list) -> list:
        selected = [select_fields(r, self.fields) for r in records]
        if self._csv is not None:
            self._csv.writerows(
                [_csv_cell(row.get(path)) for path in self.fields] for row in selected
            )
        else:
            write = self._fileobj.write
            for obj in selected:
                write(json.dumps(obj, default=str))
                write("\n")
        return selected
