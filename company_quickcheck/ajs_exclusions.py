#!/usr/bin/env python3
"""ajs_exclusions — bridge austria-job-scout's pre-flight dropped-rows
into company-quickcheck's scout CSV status.

austria-job-scout's ``discover-kmu --dns-pre-flight`` produces a
``dropped.csv`` listing rows that *cannot* possibly produce a valid
KMU job page (sentinel input like ``https://nan``, NXDOMAIN apex,
missing name). This module reads that CSV and rewrites the matching
rows in the corresponding ``scout_*.csv`` files to set
``registry_status="EXCLUDE"`` and ``registry_reason`` to a stable
marker that downstream consumers can recognise.

Marker conventions (stable contract):
    registry_status = "EXCLUDE"
    registry_reason = "ajs_preflight:<reason>"   e.g. "ajs_preflight:dns_nxdomain"
                                                      "ajs_preflight:sentinel"
                                                      "ajs_preflight:missing_name"

A row already excluded by other means (``registry_status in {"EXCLUDE",
"REGISTRY_OPEN", "REGISTRY_OPEN_WITH_WEBSITE", "REGISTRY_DELETED"}``)
is left untouched — the dropped CSV is an additive signal, not a
destructive override.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Stable reason prefix — DO NOT change without bumping downstream readers.
AJS_PREFLIGHT_PREFIX = "ajs_preflight:"

# Statuses that mean "the row is already decided" — we should NOT touch
# these. A dropped CSV can add EXCLUDE on top of REVIEW_REQUIRED / blank,
# but never override REGISTRY_OPEN / REGISTRY_DELETED.
_DECIDED_STATUSES: frozenset[str] = frozenset({
    "EXCLUDE",
    "REGISTRY_OPEN",
    "REGISTRY_OPEN_WITH_WEBSITE",
    "REGISTRY_DELETED",
})


@dataclass(frozen=True)
class ExclusionRow:
    """One row from the austria-job-scout dropped.csv."""
    source_sheet: str      # e.g. "scout_review_required.csv"
    source_row_id: str     # e.g. "13"
    company_name: str
    company_website: str
    dropped_apex: str
    reason: str            # bare reason, no prefix (e.g. "dns_nxdomain")
    notes: str = ""

    @property
    def registry_reason(self) -> str:
        """The value to write into ``registry_reason`` column."""
        return f"{AJS_PREFLIGHT_PREFIX}{self.reason}"


def load_dropped_csv(path: Path | str) -> list[ExclusionRow]:
    """Read a dropped-rows CSV (output of ``discover-kmu --out-dropped``).

    Required columns: ``source_sheet``, ``source_row_id``, ``reason``.
    Other columns (company_name, company_website, dropped_apex, notes) are
    optional and default to empty.

    Raises:
        FileNotFoundError: if *path* does not exist.
        ValueError: if required columns are missing.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"dropped CSV not found: {p}")
    with p.open(newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames is None:
            raise ValueError(f"dropped CSV has no header: {p}")
        required = {"source_sheet", "source_row_id", "reason"}
        missing = required - set(rd.fieldnames)
        if missing:
            raise ValueError(f"dropped CSV missing required columns {missing}: {p}")
        return [ExclusionRow(
            source_sheet=row.get("source_sheet", "").strip(),
            source_row_id=row.get("source_row_id", "").strip(),
            company_name=row.get("company_name", "").strip(),
            company_website=row.get("company_website", "").strip(),
            dropped_apex=row.get("dropped_apex", "").strip(),
            reason=row.get("reason", "").strip(),
            notes=row.get("notes", "").strip(),
        ) for row in rd]


def group_by_sheet(rows: list[ExclusionRow]) -> dict[str, list[ExclusionRow]]:
    """Group dropped rows by ``source_sheet`` filename for batched apply."""
    out: dict[str, list[ExclusionRow]] = defaultdict(list)
    for r in rows:
        out[r.source_sheet].append(r)
    return dict(out)


def apply_to_scout_csv(
    scout_csv: Path | str,
    exclusions: list[ExclusionRow],
    *,
    in_place: bool = False,
) -> tuple[int, int, int]:
    """Mark excluded rows in *scout_csv*.

    Updates ``registry_status="EXCLUDE"`` and
    ``registry_reason="ajs_preflight:<reason>"`` on every matching
    row whose ``row_id`` appears in *exclusions* and whose
    ``registry_status`` is not already a decided one.

    Parameters
    ----------
    scout_csv
        Path to a ``scout_*.csv`` (must have ``row_id`` and
        ``registry_status`` columns).
    exclusions
        Pre-filtered list (caller should pass only the entries whose
        ``source_sheet`` matches this CSV's filename).
    in_place
        If True, overwrite the original file. If False (default), write
        to ``<stem>.excluded.csv`` alongside the original so the caller
        can diff before swapping.

    Returns
    -------
    (updated, skipped_decided, missing) tuple:
        updated         — rows whose status was changed
        skipped_decided — rows whose status was already decided (untouched)
        missing         — exclusion row_ids not found in this CSV
    """
    p = Path(scout_csv)
    if not p.exists():
        raise FileNotFoundError(f"scout CSV not found: {p}")

    # {row_id: ExclusionRow} for O(1) lookup; target_ids for the membership test.
    by_row_id = {e.source_row_id: e for e in exclusions}
    if not by_row_id:
        return (0, 0, 0)

    out_path = p if in_place else p.with_name(f"{p.stem}.excluded.csv")

    # Read source into memory before opening the output. When in_place=True,
    # out_path is the same file — opening it for writing would truncate
    # the source before we could read it. Files are small (≤ a few hundred
    # rows × ~20 columns) so the memory cost is negligible.
    with p.open(newline="") as fin:
        rd = csv.DictReader(fin)
        if rd.fieldnames is None:
            raise ValueError(f"scout CSV has no header: {p}")
        out_fields = list(rd.fieldnames)
        for col in ("registry_status", "registry_reason"):
            if col not in out_fields:
                out_fields.append(col)
        source_rows = list(rd)

    updated = skipped = missing = 0
    seen_row_ids: set[str] = set()
    with out_path.open("w", newline="") as fout:
        wr = csv.DictWriter(fout, fieldnames=out_fields)
        wr.writeheader()
        for row in source_rows:
            row_id = str(row.get("row_id", "")).strip()
            excl = by_row_id.get(row_id)
            if excl is None:
                wr.writerow(row)
                continue

            seen_row_ids.add(row_id)
            current_status = row.get("registry_status", "").strip()
            if current_status in _DECIDED_STATUSES:
                wr.writerow(row)
                skipped += 1
                continue

            row["registry_status"] = "EXCLUDE"
            row["registry_reason"] = excl.registry_reason
            wr.writerow(row)
            updated += 1
            logger.info(
                "ajs_exclusions: %s row_id=%s → EXCLUDE (%s)",
                p.name, row_id, excl.reason,
            )

    # Exclusions whose row_id was never seen in the source — caller passed
    # in a row_id that doesn't exist in this CSV. Useful signal for
    # catching typos in upstream callers.
    missing = len(by_row_id) - len(seen_row_ids)

    if not in_place:
        logger.info("ajs_exclusions: wrote %s (use --in-place to swap)", out_path)
    return (updated, skipped, missing)
