#!/usr/bin/env python3
"""Unit tests for company_quickcheck.cli module."""

import sys
import json
import unittest
from unittest.mock import patch, Mock
import argparse
from company_quickcheck.cli import check_company, batch_process
from company_quickcheck.api import search_company, is_deleted, format_company
from company_quickcheck.core import process_batch


class TestCLI(unittest.TestCase):
    @patch("company_quickcheck.cli.search_company")
    def test_check_company_success(self, mock_search):
        mock_response = {
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
        mock_search.return_value = mock_response

        # Capture stdout
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            check_company(argparse.Namespace(name="Wienerberger AG", stealth=False))
        output = f.getvalue()

        self.assertIn("Wienerberger AG: aktiv", output)
        self.assertIn("FB-Nr: 77676f", output)

    @patch("company_quickcheck.cli.search_company")
    def test_check_company_not_found(self, mock_search):
        mock_response = {
            "offset": 0,
            "limit": 5,
            "size": 0,
            "errorCode": 1,
            "errorMessage": "No results found"
        }
        mock_search.return_value = mock_response

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            check_company(argparse.Namespace(name="Unknown GmbH", stealth=False))
        output = f.getvalue()

        self.assertIn("Unknown GmbH: nicht gefunden (-1)", output)

    @patch("company_quickcheck.cli.process_batch")
    @patch("sys.exit")
    def test_batch_process(self, mock_exit, mock_process):
        mock_stats = {"checked": 2, "active": 2, "deleted": 0, "not_found": 0, "errors": 0, "fb_backfilled": 0}
        mock_process.return_value = mock_stats
        mock_exit.return_value = None

        result = batch_process(argparse.Namespace(
            input_file="test_input.xlsx",
            output_file="output.xlsx",
            limit=None,
            stealth=False,
            checkpoint_every=25,
            resume=False,
            force_start=None,
            no_adaptive=False,
            correlation_mode="auto",
            correlation_min_confidence=0.70,
        ))

        self.assertIsNone(result)
        self.assertTrue(mock_process.called)


if __name__ == "__main__":
    unittest.main()