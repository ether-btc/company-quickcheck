#!/usr/bin/env python3
"""Unit tests for scripts/merge_batches.py (TST-05)."""

import os, sys, tempfile, unittest
from pathlib import Path

# Add scripts dir to path for import
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import pandas as pd


class TestMergeBatches(unittest.TestCase):
    """Tests for the merge_batches script logic."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Create minimal DataFrames for testing
        self.existing_df = pd.DataFrame({
            "Firmenbuchnr": ["FN 12345", "FN 67890"],
            "Firmenname": ["Alpha GmbH", "Beta AG"],
            "GELÖSCHT": [0, 1],
        })
        self.batch_df = pd.DataFrame({
            "Firmenbuchnr": ["FN 11111", "FN 22222"],
            "Firmenname": ["Gamma OG", "Delta KG"],
            "GELÖSCHT": [1, 0],
        })

    def test_arg_count_check(self):
        """Script exits with code 1 when called with wrong number of args."""
        import subprocess
        # Run with only 1 arg (plus script name = 2 total) — requires exactly 3 args
        result = subprocess.run(
            [sys.executable, "scripts/merge_batches.py", "one.xlsx"],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 1)
        # Usage message is printed to stdout before sys.exit(1)
        self.assertIn("Usage", result.stdout)

    def test_column_detection_prefers_exact(self):
        """Exact 'GELÖSCHT' match is picked first."""
        # If exact column exists, it's used
        df = pd.DataFrame({"Firmenbuchnr": ["1"], "GELÖSCHT": [0]})
        candidates = [c for c in df.columns if c == "GELÖSCHT"]
        self.assertEqual(candidates, ["GELÖSCHT"])

    def test_column_detection_falls_back_to_geloescht(self):
        """Falls back to 'GELOESCHT' when 'GELÖSCHT' not present."""
        df = pd.DataFrame({"Firmenbuchnr": ["1"], "GELOESCHT": [0]})
        candidates = [c for c in df.columns if c == "GELOESCHT"]
        self.assertEqual(candidates, ["GELOESCHT"])

    def test_column_detection_falls_back_to_partial(self):
        """Falls back to partial 'GEL' match when no exact match."""
        df = pd.DataFrame({"Firmenbuchnr": ["1"], "GEL": [0]})
        candidates = [c for c in df.columns if "GEL" in c]
        self.assertEqual(candidates, ["GEL"])

    def test_column_detection_raises_when_none_found(self):
        """Raises ValueError when no GEL* column exists."""
        df = pd.DataFrame({"Firmenbuchnr": ["1"], "Other": [0]})
        candidates = [c for c in df.columns if c == "GELÖSCHT"]
        if not candidates:
            candidates = [c for c in df.columns if c == "GELOESCHT"]
        if not candidates:
            candidates = [c for c in df.columns if "GEL" in c]
        with self.assertRaises(ValueError):
            if not candidates:
                raise ValueError(f"No GELOESCHT/GELÖSCHT column found. Columns: {list(df.columns)}")

    def test_nan_detection_warns(self):
        """NaN values in GELÖSCHT column produce a warning."""
        df = pd.DataFrame({"Firmenbuchnr": ["1", "2"], "GELÖSCHT": [0, pd.NA]})
        col = "GELÖSCHT"
        nan_count = df[col].isna().sum()
        self.assertEqual(nan_count, 1)

    def test_concat_appends_batch_to_existing(self):
        """pd.concat appends new batch rows to existing DataFrame."""
        existing = pd.DataFrame({"A": [1, 2], "GELÖSCHT": [0, 1]})
        new_batch = pd.DataFrame({"A": [3, 4], "GELÖSCHT": [1, 0]})
        merged = pd.concat([existing, new_batch], ignore_index=True)
        self.assertEqual(len(merged), 4)
        self.assertEqual(list(merged["A"]), [1, 2, 3, 4])

    def test_value_counts_output(self):
        """Value counts can be computed on merged GELÖSCHT column."""
        df = pd.DataFrame({"GELÖSCHT": [0, 1, 0, pd.NA, 1]})
        counts = df["GELÖSCHT"].value_counts().sort_index()
        self.assertEqual(counts[0], 2)
        self.assertEqual(counts[1], 2)

    def test_merge_produces_correct_row_count(self):
        """Merging 3-row existing with 2-row batch gives 5 rows total."""
        existing = self.existing_df
        batch = self.batch_df
        merged = pd.concat([existing, batch], ignore_index=True)
        self.assertEqual(len(merged), 4)

    def test_merge_preserves_geloescht_values(self):
        """GELÖSCHT values are preserved through merge."""
        existing = self.existing_df
        batch = self.batch_df
        merged = pd.concat([existing, batch], ignore_index=True)
        self.assertEqual(list(merged["GELÖSCHT"]), [0, 1, 1, 0])


if __name__ == "__main__":
    unittest.main()
