"""Operator-reviewed, hash-pinned provenance for historical admissions rows.

A pin authenticates bytes against a separately trusted release configuration;
neither the pin nor this module independently verifies legal or official origin.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

_MAX_BYTES = 32 * 1024 * 1024
_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_ROW = re.compile(r"(yi_fen_yi_duan|admission_scores):[1-9][0-9]*")


class ManifestError(RuntimeError):
    """A release cannot establish approved provenance."""


def canonical_digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("Duplicate manifest key")
        result[key] = value
    return result


def _https_url(value) -> bool:
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password)
    except ValueError:
        return False


def _identifier(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _sha(value) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


@lru_cache(maxsize=2)
def _load(path: str, pin: str, year: int, approval: str) -> dict:
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES or hashlib.sha256(raw).hexdigest() != pin:
            raise ManifestError("Manifest exceeds size limit or does not match release pin")
        doc = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(doc, dict) or set(doc) != {
            "schema", "dataset_version", "year", "approval_ref", "sources", "rows"
        }:
            raise ManifestError("Invalid manifest fields")
        if (
            doc["schema"] != "admissions-lineage/v1"
            or not _identifier(doc["dataset_version"])
            or type(doc["year"]) is not int
            or doc["year"] != year
            or doc["approval_ref"] != approval
        ):
            raise ManifestError("Manifest release identity mismatch")
        sources, rows = doc["sources"], doc["rows"]
        if not isinstance(sources, dict) or not sources or not isinstance(rows, dict) or not rows:
            raise ManifestError("Missing reviewed sources or rows")
        for source_id, source in sources.items():
            if (
                not _identifier(source_id)
                or not isinstance(source, dict)
                or set(source) != {"url", "file_sha256"}
                or not _https_url(source["url"])
                or not _sha(source["file_sha256"])
            ):
                raise ManifestError("Invalid reviewed source")
        tables = set()
        for row_id, row in rows.items():
            if (
                not _ROW.fullmatch(row_id)
                or not isinstance(row, dict)
                or set(row) != {"source_id", "locator", "row_sha256"}
                or not _identifier(row["source_id"])
                or row["source_id"] not in sources
                or not isinstance(row["locator"], str)
                or not 1 <= len(row["locator"].strip()) <= 256
                or not _sha(row["row_sha256"])
            ):
                raise ManifestError("Invalid reviewed row lineage")
            tables.add(row_id.split(":", 1)[0])
        if tables != {"yi_fen_yi_duan", "admission_scores"}:
            raise ManifestError("Both rank and school-level evidence required")
        return doc
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ManifestError("Cannot load reviewed manifest") from exc


def approved_manifest(year: int) -> tuple[str, dict]:
    path = os.getenv("GAOBAO_ADMISSIONS_MANIFEST_PATH", "")
    pin = os.getenv("GAOBAO_ADMISSIONS_MANIFEST_SHA256", "")
    approval = os.getenv("GAOBAO_DATA_APPROVAL_REF", "")
    if not path or not _sha(pin) or not _identifier(approval):
        raise ManifestError("Pinned reviewed manifest required")
    return pin, _load(path, pin, year, approval)


def row_values(evidence: dict) -> dict[str, dict]:
    """Canonical public fact payloads; approval binds actual values, not just IDs."""
    scope = {key: evidence[key] for key in ("year", "province", "subject")}
    values = {
        f"yi_fen_yi_duan:{evidence['rank_id']}": {
            **scope, "score": evidence["score"], "rank": evidence["rank"]
        }
    }
    schools = set()
    for row in evidence["rows"]:
        key = f"admission_scores:{row['id']}"
        logical_key = (row["school_id"], row["batch"])
        if key in values or logical_key in schools:
            raise ManifestError("Ambiguous school-level evidence")
        schools.add(logical_key)
        values[key] = {**scope, **row}
    return values


def verified_lineage(evidence: dict, manifest: tuple[str, dict]) -> dict:
    pin, doc = manifest
    if evidence.get("year") != doc["year"]:
        raise ManifestError("Evidence year not approved")
    records = []
    for row_id, values in row_values(evidence).items():
        entry = doc["rows"].get(row_id)
        if not entry or entry["row_sha256"] != canonical_digest(values):
            raise ManifestError("Unapproved or changed SQL row")
        source = doc["sources"][entry["source_id"]]
        records.append({
            "row": row_id,
            "source_id": entry["source_id"],
            "url": source["url"],
            "file_sha256": source["file_sha256"],
            "locator": entry["locator"],
            "row_sha256": entry["row_sha256"],
        })
    return {"dataset_version": doc["dataset_version"], "manifest_sha256": pin, "records": records}
