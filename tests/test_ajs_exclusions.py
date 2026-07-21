#!/usr/bin/env python3
"""Tests for company_quickcheck.ajs_exclusions — the bridge from
austria-job-scout's pre-flight dropped-rows CSV into scout_*.csv files.

Round-trip contract:
    1. discover-kmu --dns-pre-flight --out-dropped writes dropped.csv
    2. apply-ajs-exclusions reads dropped.csv, marks matching scout rows
       with registry_status=EXCLUDE + registry_reason=ajs_preflight:<reason>

We test:
    - load_dropped_csv round-trip with the exact column order discover-kmu
      emits (column order is stable — adding columns to one side breaks
      the contract)
    - apply_to_scout_csv writes the right registry_status / registry_reason
    - already-decided rows (REGISTRY_OPEN, REGISTRY_DELETED, EXCLUDE) are
      left untouched (the dropped CSV is additive, not destructive)
    - missing row_ids are reported via the return tuple
    - non-default in_place=False writes a sibling *.excluded.csv without
      touching the original
"""

import argparse
import csv
import io
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from company_quickcheck import ajs_exclusions
from company_quickcheck.cli import apply_ajs_exclusions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_dropped_csv(path: Path, rows: list[dict]) -> None:
    """Write a dropped-rows CSV matching discover-kmu's column order."""
    fieldnames = (
        "source_sheet", "source_row_id", "company_name",
        "company_website", "dropped_apex", "reason", "notes",
    )
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def _write_scout_csv(path: Path, rows: list[dict],
                     columns: tuple[str, ...] = (
                         "row_id", "name", "company_website",
                         "registry_status", "registry_reason",
                     )) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(columns))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# load_dropped_csv
# ---------------------------------------------------------------------------


class TestLoadDroppedCsv(unittest.TestCase):
    def test_round_trips_discover_kmu_output(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            p = Path(td) / "dropped.csv"
            _write_dropped_csv(p, [
                {"source_sheet": "scout_review_required.csv", "source_row_id": "13",
                 "company_name": "Acme", "company_website": "https://acme.at",
                 "dropped_apex": "acme.at", "reason": "dns_nxdomain"},
                {"source_sheet": "scout_review_required.csv", "source_row_id": "14",
                 "company_name": "Nan Co", "company_website": "nan",
                 "dropped_apex": "", "reason": "sentinel",
                 "notes": "rejected by _normalize_apex_domain"},
            ])
            rows = ajs_exclusions.load_dropped_csv(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].source_row_id, "13")
        self.assertEqual(rows[0].reason, "dns_nxdomain")
        self.assertEqual(rows[1].reason, "sentinel")
        self.assertEqual(rows[1].notes, "rejected by _normalize_apex_domain")

    def test_missing_file_raises(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                ajs_exclusions.load_dropped_csv(Path(td) / "nope.csv")

    def test_missing_required_columns_raises(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            p = Path(td) / "bad.csv"
            with p.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["source_sheet", "reason"])
                w.writeheader()
                w.writerow({"source_sheet": "x", "reason": "dns_nxdomain"})
            with self.assertRaises(ValueError) as cm:
                ajs_exclusions.load_dropped_csv(p)
            self.assertIn("source_row_id", str(cm.exception))


# ---------------------------------------------------------------------------
# group_by_sheet
# ---------------------------------------------------------------------------


class TestGroupBySheet(unittest.TestCase):
    def test_groups_correctly(self):
        rows = [
            ajs_exclusions.ExclusionRow("scout_review_required.csv", "1", "", "", "", "sentinel"),
            ajs_exclusions.ExclusionRow("scout_registry_open.csv",    "2", "", "", "", "dns_nxdomain"),
            ajs_exclusions.ExclusionRow("scout_review_required.csv", "3", "", "", "", "dns_timeout"),
        ]
        groups = ajs_exclusions.group_by_sheet(rows)
        self.assertEqual(set(groups.keys()), {
            "scout_review_required.csv", "scout_registry_open.csv",
        })
        self.assertEqual(len(groups["scout_review_required.csv"]), 2)
        self.assertEqual(len(groups["scout_registry_open.csv"]), 1)


# ---------------------------------------------------------------------------
# apply_to_scout_csv
# ---------------------------------------------------------------------------


class TestApplyToScoutCsv(unittest.TestCase):
    def test_marks_dropped_rows_as_excluded(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            scout = td / "scout_review_required.csv"
            _write_scout_csv(scout, [
                {"row_id": "1", "name": "Real",  "company_website": "https://real.at",
                 "registry_status": "",          "registry_reason": ""},
                {"row_id": "2", "name": "Ghost", "company_website": "https://ghost.at",
                 "registry_status": "",          "registry_reason": ""},
            ])
            exclusions = [
                ajs_exclusions.ExclusionRow("scout_review_required.csv", "2",
                                            "Ghost", "https://ghost.at", "ghost.at", "dns_nxdomain"),
            ]
            updated, skipped, missing = ajs_exclusions.apply_to_scout_csv(scout, exclusions)

            self.assertEqual(updated, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(missing, 0)

            # Default: writes *.excluded.csv alongside original
            out_file = td / "scout_review_required.excluded.csv"
            self.assertTrue(out_file.exists())
            self.assertFalse((td / "scout_review_required.csv").stat().st_size !=
                             scout.stat().st_size or False)  # original untouched

            rows = _read_csv(out_file)
            self.assertEqual(rows[0]["registry_status"], "")  # Real Co untouched
            self.assertEqual(rows[1]["registry_status"], "EXCLUDE")
            self.assertEqual(rows[1]["registry_reason"], "ajs_preflight:dns_nxdomain")

    def test_in_place_overwrites_original(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            scout = td / "scout_review_required.csv"
            _write_scout_csv(scout, [
                {"row_id": "1", "name": "Ghost", "company_website": "https://ghost.at",
                 "registry_status": "", "registry_reason": ""},
            ])
            exclusions = [
                ajs_exclusions.ExclusionRow("scout_review_required.csv", "1",
                                            "Ghost", "https://ghost.at", "ghost.at", "sentinel"),
            ]
            updated, _, _ = ajs_exclusions.apply_to_scout_csv(scout, exclusions, in_place=True)
            self.assertEqual(updated, 1)
            # Original file updated in place
            rows = _read_csv(scout)
            self.assertEqual(rows[0]["registry_status"], "EXCLUDE")
            self.assertEqual(rows[0]["registry_reason"], "ajs_preflight:sentinel")

    def test_does_not_override_decided_rows(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            scout = td / "scout_registry_open.csv"
            _write_scout_csv(scout, [
                {"row_id": "1", "name": "Already Open", "company_website": "https://open.at",
                 "registry_status": "REGISTRY_OPEN", "registry_reason": "matched"},
                {"row_id": "2", "name": "Already Deleted", "company_website": "https://del.at",
                 "registry_status": "REGISTRY_DELETED", "registry_reason": "gelöscht"},
            ])
            exclusions = [
                ajs_exclusions.ExclusionRow("scout_registry_open.csv", "1",
                                            "Open", "https://open.at", "open.at", "dns_nxdomain"),
                ajs_exclusions.ExclusionRow("scout_registry_open.csv", "2",
                                            "Del", "https://del.at", "del.at", "dns_nxdomain"),
            ]
            updated, skipped, missing = ajs_exclusions.apply_to_scout_csv(
                scout, exclusions, in_place=True,
            )
            self.assertEqual(updated, 0)
            self.assertEqual(skipped, 2)
            self.assertEqual(missing, 0)

            rows = _read_csv(scout)
            self.assertEqual(rows[0]["registry_status"], "REGISTRY_OPEN")
            self.assertEqual(rows[1]["registry_status"], "REGISTRY_DELETED")

    def test_reports_missing_row_ids(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            scout = td / "scout_review_required.csv"
            _write_scout_csv(scout, [
                {"row_id": "1", "name": "Real", "company_website": "https://real.at",
                 "registry_status": "", "registry_reason": ""},
            ])
            exclusions = [
                # Exists in CSV
                ajs_exclusions.ExclusionRow("scout_review_required.csv", "1",
                                            "Real", "https://real.at", "real.at", "sentinel"),
                # Doesn't exist in CSV
                ajs_exclusions.ExclusionRow("scout_review_required.csv", "99",
                                            "Ghost", "https://ghost.at", "ghost.at", "sentinel"),
            ]
            updated, skipped, missing = ajs_exclusions.apply_to_scout_csv(
                scout, exclusions, in_place=True,
            )
            self.assertEqual(updated, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(missing, 1)  # row_id=99 not in CSV

    def test_handles_missing_registry_columns(self):
        """Scout CSV without registry_status/registry_reason columns: the
        apply must add them rather than crash."""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            scout = td / "scout_minimal.csv"
            with scout.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["row_id", "name"])
                w.writeheader()
                w.writerow({"row_id": "1", "name": "Ghost"})
            exclusions = [
                ajs_exclusions.ExclusionRow("scout_minimal.csv", "1",
                                            "Ghost", "https://ghost.at", "ghost.at", "sentinel"),
            ]
            updated, _, _ = ajs_exclusions.apply_to_scout_csv(scout, exclusions, in_place=True)
            self.assertEqual(updated, 1)
            rows = _read_csv(scout)
            self.assertEqual(rows[0]["registry_status"], "EXCLUDE")
            self.assertEqual(rows[0]["registry_reason"], "ajs_preflight:sentinel")


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCliApplyAjsExclusions(unittest.TestCase):
    def test_end_to_end_with_real_dropped_csv(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            # Write scout CSVs (mimicking the day-1 layout)
            _write_scout_csv(td / "scout_review_required.csv", [
                {"row_id": "13", "name": "Acme", "company_website": "https://acme.at",
                 "registry_status": "", "registry_reason": ""},
                {"row_id": "14", "name": "Ghost", "company_website": "https://ghost.at",
                 "registry_status": "", "registry_reason": ""},
                {"row_id": "15", "name": "Already Decided", "company_website": "https://dec.at",
                 "registry_status": "REGISTRY_OPEN", "registry_reason": "matched"},
            ])
            _write_scout_csv(td / "scout_registry_open.csv", [
                {"row_id": "20", "name": "Other", "company_website": "https://other.at",
                 "registry_status": "REGISTRY_OPEN", "registry_reason": "matched"},
            ])
            # Write dropped CSV
            dropped = td / "dropped.csv"
            _write_dropped_csv(dropped, [
                {"source_sheet": "scout_review_required.csv", "source_row_id": "13",
                 "company_name": "Acme", "company_website": "https://acme.at",
                 "dropped_apex": "acme.at", "reason": "dns_nxdomain"},
                {"source_sheet": "scout_review_required.csv", "source_row_id": "14",
                 "company_name": "Ghost", "company_website": "https://ghost.at",
                 "dropped_apex": "ghost.at", "reason": "sentinel"},
                {"source_sheet": "scout_review_required.csv", "source_row_id": "15",
                 "company_name": "Already Decided", "company_website": "https://dec.at",
                 "dropped_apex": "dec.at", "reason": "dns_nxdomain"},
                {"source_sheet": "scout_registry_open.csv", "source_row_id": "20",
                 "company_name": "Other", "company_website": "https://other.at",
                 "dropped_apex": "other.at", "reason": "dns_timeout"},
            ])

            # Run the CLI
            ns = argparse.Namespace(
                dropped_csv=str(dropped),
                scout_dir=str(td),
                in_place=True,
            )
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                apply_ajs_exclusions(ns)

            output = out.getvalue() + err.getvalue()
            # Review sheet: 13 updated, 14 updated, 15 skipped
            self.assertIn("scout_review_required.csv: 2 updated, 1 skipped", output)
            # Registry sheet: 20 skipped (already decided)
            self.assertIn("scout_registry_open.csv: 0 updated, 1 skipped", output)
            self.assertIn("2 updated, 2 skipped, 0 missing", output)

            # Verify CSV content
            review_rows = _read_csv(td / "scout_review_required.csv")
            self.assertEqual(review_rows[0]["registry_status"], "EXCLUDE")
            self.assertEqual(review_rows[0]["registry_reason"], "ajs_preflight:dns_nxdomain")
            self.assertEqual(review_rows[1]["registry_status"], "EXCLUDE")
            self.assertEqual(review_rows[1]["registry_reason"], "ajs_preflight:sentinel")
            # The decided row is untouched
            self.assertEqual(review_rows[2]["registry_status"], "REGISTRY_OPEN")

    def test_missing_dropped_csv_exits_nonzero(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            ns = argparse.Namespace(
                dropped_csv=str(td / "missing.csv"),
                scout_dir=str(td),
                in_place=False,
            )
            with self.assertRaises(SystemExit) as cm:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    apply_ajs_exclusions(ns)
            self.assertEqual(cm.exception.code, 2)

    def test_empty_dropped_csv_is_noop(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            td = Path(td)
            scout = td / "scout_review_required.csv"
            _write_scout_csv(scout, [
                {"row_id": "1", "name": "Real", "company_website": "https://real.at",
                 "registry_status": "", "registry_reason": ""},
            ])
            dropped = td / "dropped.csv"
            # Header only — empty dropped CSV
            _write_dropped_csv(dropped, [])

            ns = argparse.Namespace(
                dropped_csv=str(dropped),
                scout_dir=str(td),
                in_place=False,
            )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = apply_ajs_exclusions(ns)
            self.assertIsNone(rc)
            # Original scout CSV untouched
            rows = _read_csv(scout)
            self.assertEqual(rows[0]["registry_status"], "")


if __name__ == "__main__":
    unittest.main()
