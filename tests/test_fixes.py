#!/usr/bin/env python3
"""Unit tests for firmen-quickcheck bug fixes.

Tests:
- Force-start + limit NOOP fix (core.py row slicing)
- Smart resume from existing output file
- Disk space pre-check
"""

import os
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch, Mock

import pandas as pd

from company_quickcheck.core import process_batch


class TestForceStartLimitFix(unittest.TestCase):
    """Test that --force-start N --limit M processes rows N..N+M-1, not 0 rows."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        # Create a 10-row input file
        self.input_df = pd.DataFrame({
            "Firmenname": [f"Company {i}" for i in range(10)],
            "Firmenbuchnr": [""] * 10,
            "UID_Nummer": [""] * 10,
            "Hauptadr_Strasse": [""] * 10,
            "Hauptadr_PLZ": [""] * 10,
            "Hauptadr_Ort": [""] * 10,
        })
        self.input_file = os.path.join(self.test_dir, "input.xlsx")
        self.output_file = os.path.join(self.test_dir, "output.xlsx")
        self.input_df.to_excel(self.input_file, index=False)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        for f in ["input.xlsx", "output.xlsx", "output.xlsx.checkpoint.json"]:
            if os.path.exists(f):
                os.remove(f)

    @patch("company_quickcheck.core.search_company")
    def test_force_start_and_limit_no_noop(self, mock_search):
        """--force-start 5 --limit 3 should process rows 5,6,7 (not 0 rows)."""
        mock_search.return_value = {
            "companies": [{
                "reg-no": "123",
                "reg-status": "registered",
                "business-name": "Test",
                "legal-form": "GmbH",
                "business-address": {},
            }]
        }
        stats = process_batch(
            self.input_file,
            self.output_file,
            force_start=5,
            limit=3,
            checkpoint_every=10,
            adaptive=False,
        )
        # 3 rows processed (5,6,7)
        self.assertEqual(stats["checked"], 3)
        # Output should have 3 rows
        out = pd.read_excel(self.output_file)
        self.assertEqual(len(out), 3)

    @patch("company_quickcheck.core.search_company")
    def test_force_start_only(self, mock_search):
        """--force-start 7 should process rows 7-9."""
        mock_search.return_value = {
            "companies": [{
                "reg-no": "123",
                "reg-status": "registered",
                "business-name": "Test",
                "legal-form": "GmbH",
                "business-address": {},
            }]
        }
        stats = process_batch(
            self.input_file,
            self.output_file,
            force_start=7,
            checkpoint_every=10,
            adaptive=False,
        )
        self.assertEqual(stats["checked"], 3)  # rows 7,8,9

    @patch("company_quickcheck.core.search_company")
    def test_limit_only(self, mock_search):
        """--limit 4 should process rows 0-3."""
        mock_search.return_value = {
            "companies": [{
                "reg-no": "123",
                "reg-status": "registered",
                "business-name": "Test",
                "legal-form": "GmbH",
                "business-address": {},
            }]
        }
        stats = process_batch(
            self.input_file,
            self.output_file,
            limit=4,
            checkpoint_every=10,
            adaptive=False,
        )
        self.assertEqual(stats["checked"], 4)
        out = pd.read_excel(self.output_file)
        self.assertEqual(len(out), 4)


class TestSmartResume(unittest.TestCase):
    """Test that --resume with existing output skips already-processed rows."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.input_df = pd.DataFrame({
            "Firmenname": [f"Company {i}" for i in range(10)],
            "Firmenbuchnr": [""] * 10,
            "UID_Nummer": [""] * 10,
            "Hauptadr_Strasse": [""] * 10,
            "Hauptadr_PLZ": [""] * 10,
            "Hauptadr_Ort": [""] * 10,
        })
        self.input_file = os.path.join(self.test_dir, "input.xlsx")
        self.output_file = os.path.join(self.test_dir, "output.xlsx")
        self.input_df.to_excel(self.input_file, index=False)

        # Create an existing output with rows 0-4 filled, 5-9 NaN
        self.existing_df = self.input_df.copy()
        self.existing_df["GELÖSCHT"] = [0, 1, 0, -1, 0, float("nan"), float("nan"), float("nan"), float("nan"), float("nan")]
        self.existing_df.to_excel(self.output_file, index=False)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("company_quickcheck.core.search_company")
    def test_smart_resume_skips_filled_rows(self, mock_search):
        """Resuming from output with 5 filled rows should process only rows 5-9."""
        mock_search.return_value = {
            "companies": [{
                "reg-no": "123",
                "reg-status": "registered",
                "business-name": "Test",
                "legal-form": "GmbH",
                "business-address": {},
            }]
        }
        stats = process_batch(
            self.input_file,
            self.output_file,
            resume=True,
            checkpoint_every=10,
            adaptive=False,
        )
        # Only rows 5-9 processed (5 rows)
        self.assertEqual(stats["checked"], 5)

    @patch("company_quickcheck.core.search_company")
    def test_smart_resume_with_gap(self, mock_search):
        """Resume should stop at first NaN gap, not skip to end."""
        # Fill rows 0-2, leave row 3 NaN, fill rows 4-5
        self.existing_df["GELÖSCHT"] = [0, 1, 0, float("nan"), 0, float("nan")] + [float("nan")] * 4
        self.existing_df.to_excel(self.output_file, index=False)

        mock_search.return_value = {
            "companies": [{
                "reg-no": "123",
                "reg-status": "registered",
                "business-name": "Test",
                "legal-form": "GmbH",
                "business-address": {},
            }]
        }
        stats = process_batch(
            self.input_file,
            self.output_file,
            resume=True,
            checkpoint_every=10,
            adaptive=False,
        )
        # Should resume from row 3 (first gap), process rows 3-9 (7 rows)
        self.assertEqual(stats["checked"], 7)


class TestDiskSpaceCheck(unittest.TestCase):
    """Test that process_batch aborts when disk space is insufficient."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.input_df = pd.DataFrame({
            "Firmenname": ["Company 0"],
            "Firmenbuchnr": [""],
            "UID_Nummer": [""],
            "Hauptadr_Strasse": [""],
            "Hauptadr_PLZ": [""],
            "Hauptadr_Ort": [""],
        })
        self.input_file = os.path.join(self.test_dir, "input.xlsx")
        self.output_file = os.path.join(self.test_dir, "output.xlsx")
        self.input_df.to_excel(self.input_file, index=False)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("company_quickcheck.core.shutil.disk_usage")
    def test_insufficient_disk_space_raises(self, mock_disk_usage):
        """Should raise RuntimeError when disk space < 1 GB."""
        mock_disk_usage.return_value = Mock(free=500 * 1024**2)  # 500 MB
        with self.assertRaises(RuntimeError) as ctx:
            process_batch(self.input_file, self.output_file)
        self.assertIn("Insufficient disk space", str(ctx.exception))

    @patch("company_quickcheck.core.shutil.disk_usage")
    @patch("company_quickcheck.core.search_company")
    def test_sufficient_disk_space_proceeds(self, mock_search, mock_disk_usage):
        """Should proceed normally when disk space >= 1 GB."""
        mock_disk_usage.return_value = Mock(free=2 * 1024**3)  # 2 GB
        mock_search.return_value = {
            "companies": [{
                "reg-no": "123",
                "reg-status": "registered",
                "business-name": "Test",
                "legal-form": "GmbH",
                "business-address": {},
            }]
        }
        stats = process_batch(
            self.input_file,
            self.output_file,
            checkpoint_every=10,
            adaptive=False,
        )
        self.assertEqual(stats["checked"], 1)


if __name__ == "__main__":
    unittest.main()
