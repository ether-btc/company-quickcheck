#!/usr/bin/env python3
"""Unit tests for company_quickcheck.core module."""

import os
import json
import unittest
from unittest.mock import patch, Mock
import pandas as pd
from company_quickcheck.core import process_batch
from company_quickcheck.api import search_company, is_deleted, format_company, address_confidence


class TestProcessBatch(unittest.TestCase):
    def setUp(self):
        # Create a test Excel file
        self.test_df = pd.DataFrame({
            "Firmenname": ["Wienerberger AG", "Erste Group Bank AG", "Nonexistent GmbH"],
            "Firmenbuchnr": ["", "", ""],
            "UID_Nummer": ["", "", ""],
            "Hauptadr_Strasse": ["", "", ""],
            "Hauptadr_PLZ": ["", "", ""],
            "Hauptadr_Ort": ["", "", ""]
        })
        self.test_df.to_excel("test_input.xlsx", index=False)
        
        # Mock API responses
        self.success_response = {
            "offset": 0,
            "limit": 5,
            "size": 1,
            "errorCode": 0,
            "companies": [
                {
                    "country": "at",
                    "reg-no-class": "at-fb",
                    "reg-no": "77676f",
                    "reg-status": "registered",
                    "business-name": "Wienerberger AG",
                    "legal-form": "Aktiengesellschaft",
                    "business-address": {
                        "country": "AUT",
                        "postal-code": "1100",
                        "city": "Wien",
                        "street-address": "Wienerbergerplatz",
                        "street-number": "1"
                    }
                }
            ]
        }
        
        self.deleted_response = {
            "offset": 0,
            "limit": 5,
            "size": 1,
            "errorCode": 0,
            "companies": [
                {
                    "country": "at",
                    "reg-no-class": "at-fb",
                    "reg-no": "12345",
                    "reg-status": "cancelled",
                    "business-name": "Cancelled GmbH",
                    "legal-form": "Gesellschaft mit beschränkter Haftung",
                    "business-address": {
                        "country": "AUT",
                        "postal-code": "1010",
                        "city": "Wien",
                        "street-address": "Examplestr",
                        "street-number": "1"
                    }
                }
            ]
        }
        
        self.not_found_response = {
            "offset": 0,
            "limit": 5,
            "size": 0,
            "errorCode": 1,
            "errorMessage": "No results found"
        }
    
    def tearDown(self):
        # Clean up test files
        for file_name in ["test_input.xlsx", "test_output.xlsx", "test_output.xlsx.checkpoint.json"]:
            if os.path.exists(file_name):
                os.remove(file_name)
    
    @patch("company_quickcheck.core.search_company")
    def test_process_batch_success(self, mock_search):
        # Mock first company as active, second as active, third as not found
        call1 = Mock(return_value=self.success_response)
        call2 = Mock(return_value=self.success_response)
        call3 = Mock(return_value=self.not_found_response)
        
        def side_effect(name, limit, use_stealth, rate_limiter=None):
            if "Wienerberger" in name:
                return call1()
            elif "Erste" in name:
                return call2()
            else:
                return call3()
        
        mock_search.side_effect = side_effect
        
        # Process batch
        stats = process_batch("test_input.xlsx", "test_output.xlsx", limit=3, checkpoint_every=10, resume=False)
        
        # Check stats
        self.assertEqual(stats["checked"], 2)
        self.assertEqual(stats["active"], 2)
        self.assertEqual(stats["not_found"], 1)
        self.assertEqual(stats["deleted"], 0)
        self.assertEqual(stats["errors"], 0)
        
        # Check output file
        df_out = pd.read_excel("test_output.xlsx")
        self.assertEqual(len(df_out), 3)
        self.assertEqual(df_out.iloc[0]["GELÖSCHT"], 0)
        self.assertEqual(df_out.iloc[1]["GELÖSCHT"], 0)
        self.assertEqual(df_out.iloc[2]["GELÖSCHT"], -1)
    
    @patch("company_quickcheck.core.search_company")
    def test_process_batch_with_deleted(self, mock_search):
        # Mock first as active, second as deleted
        call1 = Mock(return_value=self.success_response)
        call2 = Mock(return_value=self.deleted_response)
        
        def side_effect(name, limit, use_stealth, rate_limiter=None):
            if "Wienerberger" in name:
                return call1()
            else:
                return call2()
        
        mock_search.side_effect = side_effect
        
        stats = process_batch("test_input.xlsx", "test_output.xlsx", limit=2, checkpoint_every=10)
        
        self.assertEqual(stats["checked"], 2)
        self.assertEqual(stats["active"], 1)
        self.assertEqual(stats["deleted"], 1)
        self.assertEqual(stats["not_found"], 0)
    
    @patch("company_quickcheck.core.search_company")
    def test_process_batch_api_error(self, mock_search):
        mock_search.return_value = None
        
        stats = process_batch("test_input.xlsx", "test_output.xlsx", limit=2, checkpoint_every=10)
        
        self.assertEqual(stats["checked"], 0)
        self.assertEqual(stats["errors"], 2)
        self.assertEqual(stats["not_found"], 0)
    
    @patch("company_quickcheck.core.search_company")
    def test_process_batch_resume(self, mock_search):
        # Create a checkpoint file
        checkpoint_data = {
            "last_idx": 1,
            "checked": 1,
            "active": 1,
            "deleted": 0,
            "not_found": 0,
            "errors": 0,
            "fb_backfilled": 0
        }
        with open("test_output.xlsx.checkpoint.json", "w") as f:
            json.dump(checkpoint_data, f)

        # Mock responses
        mock_search.return_value = self.success_response

        stats = process_batch("test_input.xlsx", "test_output.xlsx", limit=3, checkpoint_every=10, resume=True)

        # Should start from row 2
        self.assertEqual(stats["checked"], 1)

    def test_workers_zero_or_negative_raises(self):
        """workers < 1 must raise ValueError — caught early, before disk check."""
        # Use force-start and limit to skip the disk check
        with patch("company_quickcheck.core.search_company"):
            with self.assertRaises(ValueError):
                process_batch("test_input.xlsx", "test_output.xlsx",
                              limit=1, checkpoint_every=100, workers=0)
            with self.assertRaises(ValueError):
                process_batch("test_input.xlsx", "test_output.xlsx",
                              limit=1, checkpoint_every=100, workers=-1)

    @patch("company_quickcheck.core.search_company")
    def test_process_batch_parallel_matches_sequential(self, mock_search):
        """Parallel mode (workers=4) must produce identical GELÖSCHT values
        to sequential mode (workers=1) for the same input."""
        # Mock: each company returns its own result deterministically
        def side_effect(name, limit, use_stealth, rate_limiter=None):
            if "Wienerberger" in name:
                return self.success_response
            elif "Erste" in name:
                return self.deleted_response
            else:
                return self.not_found_response

        mock_search.side_effect = side_effect

        # Build a slightly larger test set so the parallel path is exercised
        parallel_df = pd.DataFrame({
            "Firmenname": [
                "Wienerberger AG", "Erste Group Bank AG", "Nonexistent GmbH",
                "Wienerberger AG", "Erste Group Bank AG", "Nonexistent GmbH",
            ],
            "Firmenbuchnr": ["", "", "", "", "", ""],
            "UID_Nummer": ["", "", "", "", "", ""],
            "Hauptadr_Strasse": ["", "", "", "", "", ""],
            "Hauptadr_PLZ": ["", "", "", "", "", ""],
            "Hauptadr_Ort": ["", "", "", "", "", ""],
        })
        parallel_df.to_excel("test_input_parallel.xlsx", index=False)

        try:
            stats_seq = process_batch(
                "test_input_parallel.xlsx", "test_output_seq.xlsx",
                limit=6, checkpoint_every=100, workers=1,
            )
            stats_par = process_batch(
                "test_input_parallel.xlsx", "test_output_par.xlsx",
                limit=6, checkpoint_every=100, workers=4,
            )
            # Identical stats
            for k in ("checked", "deleted", "active", "not_found", "errors"):
                self.assertEqual(stats_seq[k], stats_par[k],
                                 f"stat {k} differs: seq={stats_seq[k]} par={stats_par[k]}")
            # Identical GELÖSCHT column on disk
            df_seq = pd.read_excel("test_output_seq.xlsx")
            df_par = pd.read_excel("test_output_par.xlsx")
            self.assertEqual(
                df_seq["GELÖSCHT"].tolist(),
                df_par["GELÖSCHT"].tolist(),
            )
        finally:
            for f in ("test_input_parallel.xlsx", "test_output_seq.xlsx",
                      "test_output_par.xlsx",
                      "test_output_seq.xlsx.checkpoint.json",
                      "test_output_par.xlsx.checkpoint.json"):
                if os.path.exists(f):
                    os.remove(f)

    @patch("company_quickcheck.core.search_company")
    def test_process_batch_parallel_handles_worker_exception(self, mock_search):
        """If a worker raises, the row is recorded as error (-1) and
        other rows still complete. The batch must not crash."""
        # First call raises, subsequent calls return success
        responses = [self.success_response] * 3
        call_count = {"n": 0}

        def side_effect(name, limit, use_stealth, rate_limiter=None):
            n = call_count["n"]
            call_count["n"] += 1
            if n == 1:
                raise RuntimeError("simulated worker failure")
            return responses[min(n - 1, len(responses) - 1)]

        mock_search.side_effect = side_effect

        parallel_df = pd.DataFrame({
            "Firmenname": ["Alpha AG", "Beta GmbH", "Gamma KG"],
            "Firmenbuchnr": ["", "", ""],
            "UID_Nummer": ["", "", ""],
            "Hauptadr_Strasse": ["", "", ""],
            "Hauptadr_PLZ": ["", "", ""],
            "Hauptadr_Ort": ["", "", ""],
        })
        parallel_df.to_excel("test_input_exc.xlsx", index=False)
        try:
            stats = process_batch(
                "test_input_exc.xlsx", "test_output_exc.xlsx",
                limit=3, checkpoint_every=100, workers=3,
            )
            # 1 row had a worker exception → errors
            self.assertGreaterEqual(stats["errors"], 1)
            # Other rows still completed
            self.assertGreaterEqual(stats["checked"], 1)
        finally:
            for f in ("test_input_exc.xlsx", "test_output_exc.xlsx",
                      "test_output_exc.xlsx.checkpoint.json"):
                if os.path.exists(f):
                    os.remove(f)

    @patch("company_quickcheck.core.search_company")
    def test_process_batch_parallel_actually_concurrent(self, mock_search):
        """workers=4 with 4 rows of 0.3s simulated latency should finish
        in ~0.3-0.5s, NOT ~1.2s (which would indicate serial execution).
        This is the key behavioural proof that parallelism works."""
        import time as time_mod
        per_call_delay = 0.3

        def slow_search(name, limit, use_stealth, rate_limiter=None):
            time_mod.sleep(per_call_delay)
            return self.success_response

        mock_search.side_effect = slow_search

        parallel_df = pd.DataFrame({
            "Firmenname": [f"Company {i}" for i in range(4)],
            "Firmenbuchnr": [""] * 4,
            "UID_Nummer": [""] * 4,
            "Hauptadr_Strasse": [""] * 4,
            "Hauptadr_PLZ": [""] * 4,
            "Hauptadr_Ort": [""] * 4,
        })
        parallel_df.to_excel("test_input_concur.xlsx", index=False)
        try:
            start = time_mod.monotonic()
            stats = process_batch(
                "test_input_concur.xlsx", "test_output_concur.xlsx",
                limit=4, checkpoint_every=100, workers=4,
            )
            elapsed = time_mod.monotonic() - start

            # Sequential would take 4 * 0.3 = 1.2s.
            # Parallel with 4 workers should take ~0.3-0.5s.
            # Allow generous slack for CI/test environments.
            self.assertLess(
                elapsed, 0.9,
                f"workers=4 took {elapsed:.2f}s — should be ~0.3-0.5s, "
                f"not {4 * per_call_delay:.1f}s (which would mean serial)"
            )
            # All 4 rows processed
            self.assertEqual(stats["checked"], 4)
        finally:
            for f in ("test_input_concur.xlsx", "test_output_concur.xlsx",
                      "test_output_concur.xlsx.checkpoint.json"):
                if os.path.exists(f):
                    os.remove(f)

    @patch("company_quickcheck.core.search_company")
    def test_process_batch_parallel_no_adaptive_shares_fixed_delay(self, mock_search):
        """With --no-adaptive, the fixed sleep happens inside the main
        loop's _maybe_sleep. Parallel mode skips _maybe_sleep but
        sleep should still occur inside each search_company call.
        Verify parallel mode with --no-adaptive doesn't crash and
        processes all rows."""
        def side_effect(name, limit, use_stealth, rate_limiter=None):
            return self.success_response

        mock_search.side_effect = side_effect

        parallel_df = pd.DataFrame({
            "Firmenname": [f"Company {i}" for i in range(3)],
            "Firmenbuchnr": [""] * 3,
            "UID_Nummer": [""] * 3,
            "Hauptadr_Strasse": [""] * 3,
            "Hauptadr_PLZ": [""] * 3,
            "Hauptadr_Ort": [""] * 3,
        })
        parallel_df.to_excel("test_input_noad.xlsx", index=False)
        try:
            # workers=3, adaptive=False (so rate_limiter=None)
            stats = process_batch(
                "test_input_noad.xlsx", "test_output_noad.xlsx",
                limit=3, checkpoint_every=100, workers=3, adaptive=False,
            )
            self.assertEqual(stats["checked"], 3)
        finally:
            for f in ("test_input_noad.xlsx", "test_output_noad.xlsx",
                      "test_output_noad.xlsx.checkpoint.json"):
                if os.path.exists(f):
                    os.remove(f)


if __name__ == "__main__":
    unittest.main()