#!/usr/bin/env python3
"""Unit tests for company_quickcheck.api module."""

import os
import json
import unittest
from unittest.mock import patch, Mock
from company_quickcheck.api import (
    normalize_address,
    address_confidence,
    search_opendata,
    search_stealth_core,
    search_company,
    is_deleted,
    format_company,
    API_KEY,
)

# Mock API key for testing
os.environ["OPENDATA_API_KEY"] = "test_api_key"


class TestNormalizeAddress(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(normalize_address(""), "")

    def test_basic(self):
        self.assertEqual(normalize_address("Wienerberger AG"), "wienerberger ag")

    def test_umlauts(self):
        self.assertEqual(normalize_address("straße"), "strasse")
        self.assertEqual(normalize_address("Müller"), "mueller")
        self.assertEqual(normalize_address("Köln"), "koeln")

    def test_abbreviations(self):
        self.assertEqual(normalize_address("Hauptstr. 1"), "hauptstrasse 1")
        self.assertEqual(normalize_address("Schulgasse 5"), "schulgasse 5")

    def test_punctuation(self):
        self.assertEqual(normalize_address("Wienerbergerplatz, 1"), "wienerbergerplatz 1")

    def test_case_insensitive(self):
        self.assertEqual(normalize_address("WIENERBERGER AG"), "wienerberger ag")


class TestAddressConfidence(unittest.TestCase):
    def test_no_plz(self):
        confidence = address_confidence("", "", "", "", "", "", "Wien")
        self.assertEqual(confidence, 0.0)

    def test_plz_mismatch(self):
        confidence = address_confidence("Hauptstr 1", "1100", "Wien", "", "", "1200", "Wien")
        self.assertEqual(confidence, 0.0)

    def test_city_mismatch(self):
        confidence = address_confidence("Hauptstr 1", "1100", "Wien", "", "", "1100", "Graz")
        self.assertEqual(confidence, 0.0)

    def test_exact_match(self):
        confidence = address_confidence(
            "Wienerbergerplatz 1",
            "1100",
            "Wien",
            "wienerbergerplatz",
            "1",
            "1100",
            "wien"
        )
        self.assertEqual(confidence, 1.0)

    def test_partial_match(self):
        confidence = address_confidence(
            "Hauptstr 5",
            "1100",
            "Wien",
            "hauptstrasse",
            "5",
            "1100",
            "wien"
        )
        # After normalization, both become "hauptstrasse 5", so exact match
        self.assertEqual(confidence, 1.0)

    def test_street_name_only_match(self):
        confidence = address_confidence(
            "Hauptstr 5",
            "1100",
            "Wien",
            "hauptstrasse",
            "10",
            "1100",
            "wien"
        )
        # After normalization, street names match but numbers differ → 0.75
        self.assertEqual(confidence, 0.75)


class TestSearchOpendata(unittest.TestCase):
    @patch("requests.get")
    def test_success(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
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

        mock_get.return_value = mock_response

        result = search_opendata("Wienerberger AG")
        self.assertIsNotNone(result)
        self.assertEqual(result["size"], 1)
        self.assertTrue(result.get("companies"))

    @patch("requests.get")
    def test_429_rate_limit(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "5"}
        mock_get.return_value = mock_response

        result = search_opendata("Test AG")
        self.assertIsNone(result)

    @patch("requests.get")
    def test_401_invalid_key(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.headers = {}
        mock_get.return_value = mock_response

        # The function should print an error and return None
        result = search_opendata("Test AG")
        self.assertIsNone(result)

    @patch("requests.get")
    def test_exception(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        result = search_opendata("Test AG")
        self.assertIsNone(result)


class TestStealthCoreSearch(unittest.TestCase):
    @patch("company_quickcheck.api.shutil.which")
    @patch("company_quickcheck.api.subprocess.run")
    def test_success(self, mock_run, mock_which):
        mock_which.return_value = True  # stealth-core found in PATH
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "{\"size\": 1, \"companies\": [{\"business-name\": \"Test AG\"}]}"
        mock_run.return_value = mock_result

        result = search_stealth_core("Test AG")
        self.assertIsNotNone(result)
        self.assertEqual(result["size"], 1)

    @patch("company_quickcheck.api.shutil.which")
    @patch("company_quickcheck.api.subprocess.run")
    def test_nonzero_exit(self, mock_run, mock_which):
        mock_which.return_value = True  # stealth-core found in PATH
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Error"
        mock_run.return_value = mock_result

        result = search_stealth_core("Test AG")
        self.assertIsNone(result)

    @patch("company_quickcheck.api.shutil.which")
    @patch("company_quickcheck.api.subprocess.run")
    def test_json_decode_error(self, mock_run, mock_which):
        mock_which.return_value = True  # stealth-core found in PATH
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "invalid json"
        mock_run.return_value = mock_result

        result = search_stealth_core("Test AG")
        self.assertIsNone(result)


class TestIsDeleted(unittest.TestCase):
    def test_deleted(self):
        company = {"reg-status": "cancelled"}
        self.assertTrue(is_deleted(company))

    def test_active(self):
        company = {"reg-status": "registered"}
        self.assertFalse(is_deleted(company))

    def test_other(self):
        company = {"reg-status": "unknown"}
        self.assertFalse(is_deleted(company))


class TestFormatCompany(unittest.TestCase):
    def test_format(self):
        company = {
            "business-name": "Wienerberger AG",
            "reg-no": "77676f",
            "reg-status": "registered"
        }
        result = format_company(company)
        self.assertEqual(result, "Wienerberger AG [77676f / registered]")

    def test_missing_fields(self):
        company = {}
        result = format_company(company)
        self.assertEqual(result, "? [? / ?]")


if __name__ == "__main__":
    unittest.main()