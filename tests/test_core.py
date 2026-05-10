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
        
        def side_effect(name, limit, use_stealth):
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
        
        def side_effect(name, limit, use_stealth):
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


if __name__ == "__main__":
    unittest.main()