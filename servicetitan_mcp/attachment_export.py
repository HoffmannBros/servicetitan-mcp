"""Helpers for `download_job_photos`: attachment classification, filename
construction, and output-directory resolution.

Pure (no async, no network), like `report_export.py`, so the naming and
filtering rules can be unit-tested in isolation. ServiceTitan's job attachment
list carries only metadata (`fileName`, `title`, `createdOn`); the `fileName`
prefix is the only signal of where a file came from:

  Attaches/...      uploaded on the job, field photos among them
  Images/...        pricebook/estimate stock images copied at job creation
                    (some have no extension)
  CapturedDocs/...  generated documents such as signed invoices (PDF)
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .report_export import _DEFAULT_EXPORT_DIR, _expand_path, _sanitize

IMAGE_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "webp", "gif"}
INCLUDE_MODES = ("field_photos", "images", "all")

_CONTENT_TYPE_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/pdf": "pdf",
}


def file_ext(file_name: str | None) -> str | None:
    """Lowercased extension of the last path segment, or None if it has none."""
    base = (file_name or "").rsplit("/", 1)[-1]
    if "." not in base:
        return None
    ext = base.rsplit(".", 1)[-1].lower()
    return ext if re.fullmatch(r"[a-z0-9]{1,5}", ext) else None


def ext_from_content_type(content_type: str | None) -> str:
    """Extension for a download whose `fileName` had none; `bin` if unknown."""
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(mime, "bin")


def classify(file_name: str | None) -> str:
    """Return `field_photo`, `image`, or `document` for an attachment."""
    name = file_name or ""
    is_image = file_ext(name) in IMAGE_EXTS
    if name.startswith("Attaches/") and is_image:
        return "field_photo"
    if is_image or name.startswith("Images/"):
        return "image"
    return "document"


def selected(kind: str, include: str) -> bool:
    """Whether an attachment of `kind` is wanted under the `include` mode."""
    if include == "field_photos":
        return kind == "field_photo"
    if include == "images":
        return kind in ("field_photo", "image")
    if include == "all":
        return True
    raise ValueError(f"include must be one of {list(INCLUDE_MODES)}, got {include!r}")


def local_date(completed_on: str, tz: str) -> str:
    """Convert an ISO UTC timestamp to a `YYYY-MM-DD` date in timezone `tz`."""
    try:
        zone = ZoneInfo(tz)
    except Exception as exc:  # ZoneInfoNotFoundError is a KeyError, not a ValueError
        raise ValueError(f"unknown timezone {tz!r}") from exc
    # Python 3.10's fromisoformat rejects "Z" and 7-digit fractions, both of
    # which ServiceTitan emits; the fraction never changes the date.
    text = re.sub(r"\.\d+", "", completed_on.strip()).replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).strftime("%Y-%m-%d")


def build_stem(
    tenant: str,
    bu_name: str,
    job_type_name: str,
    job_number: object,
    date: str,
    seq: int,
    attachment_id: object,
) -> str:
    """Filename without extension. `attachment_id` keeps it unique and
    deterministic across re-runs; `seq` reads in capture order."""
    head = "__".join(
        _sanitize(part) for part in (tenant, bu_name, job_type_name, job_number, date)
    )
    return f"{head}__{seq:02d}_{_sanitize(attachment_id)}"


def build_filename(
    tenant: str,
    bu_name: str,
    job_type_name: str,
    job_number: object,
    date: str,
    seq: int,
    attachment_id: object,
    ext: str,
) -> str:
    """`{tenant}__{bu}__{job_type}__{job_number}__{date}__{seq:02d}_{id}.{ext}`"""
    stem = build_stem(
        tenant, bu_name, job_type_name, job_number, date, seq, attachment_id
    )
    return f"{stem}.{_sanitize(ext)}"


def resolve_photo_dir(output_dir: str | None) -> Path:
    """`output_dir` if given, else `$ST_OUTPUTS_DIR/job_photos`, else the
    in-repo `report_exports/job_photos`. Creates the directory."""
    if output_dir:
        directory = _expand_path(output_dir)
    elif os.environ.get("ST_OUTPUTS_DIR"):
        directory = _expand_path(os.environ["ST_OUTPUTS_DIR"]) / "job_photos"
    else:
        directory = _DEFAULT_EXPORT_DIR / "job_photos"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
