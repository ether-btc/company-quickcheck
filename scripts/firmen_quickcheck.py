#!/usr/bin/env python3
"""
Firmen-Quickcheck Österreich: Check Austrian companies for closure/deletion status.
Uses opendata.host API (https://api.opendata.host/1.0/registered-companies/find).

Usage:
    python firmen_quickcheck.py input.xlsx output.xlsx [--limit N] [--resume]

Priority:
  1. GELÖSCHT column: 1=deleted, 0=active, -1=not found
  2. Firmenbuchnr backfill: only when exactly 1 result AND address matches
  3. Firmenname: NEVER updated

Requirements:
    pip install pandas openpyxl requests

Environment: OPENDATA_API_KEY must be set or exported.
"""

import os
import re
import sys
import time
import json
import argparse
import pandas as pd
import requests

from company_quickcheck.api import normalize_address, address_confidence

API_KEY = os.getenv("OPENDATA_API_KEY")
if not API_KEY:
    raise ValueError("OPENDATA_API_KEY not set. Add to ~/.hermes/.env or export before running.")

BASE_URL = "https://api.opendata.host/1.0"


def search_company(name: str, limit: int = 5) -> dict | None:
    """Search opendata.host for Austrian companies."""
    try:
        resp = requests.get(
            f"{BASE_URL}/registered-companies/find",
            params={"company-name": name, "limit": limit},
            auth=(API_KEY, ""),
            timeout=20,
        )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"    [429 rate limited] waiting {wait}s (will not sleep again after)...")
            time.sleep(wait)
            # Do NOT call ourselves again — just return None so caller marks -1
            # and moves on. The rate-limit window is now spent.
            return None
        if resp.status_code == 401:
            raise PermissionError("Invalid API key (401 Unauthorized)")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    [error] {e}")
        return None


def is_deleted(company: dict) -> bool:
    """True if company status is 'cancelled' (gelöscht/geschlossen)."""
    return str(company.get("reg-status", "")).lower() == "cancelled"


def format_company(company: dict) -> str:
    return f"{company.get('business-name', '?')} [{company.get('reg-no', '?')} / {company.get('reg-status', '?')}]"


def process(input_file: str, output_file: str, limit: int = None,
            checkpoint_every: int = 25, resume: bool = False,
            force_start: int = None) -> dict:
    """
    Process companies with address-aware matching and Firmenbuchnr backfill.

    force_start: skip all rows before this index (0-based). Useful for
                 resuming after a partial test run with known bad output.
    """

    # Load data
    df = pd.read_excel(input_file) if not input_file.endswith(".csv") else pd.read_csv(input_file)
    original_idx = df.index.tolist()  # preserve original Excel row numbers

    if limit:
        df = df.head(limit).copy()

    total = len(df)

    # Init columns
    if "GELÖSCHT" not in df.columns:
        df["GELÖSCHT"] = 0
    if "AA" not in df.columns:
        df["AA"] = ""

    # Checkpoint resume
    start_idx = 0
    if resume and force_start is None and os.path.exists(output_file + ".checkpoint.json"):
        with open(output_file + ".checkpoint.json") as f:
            ck = json.load(f)
            start_idx = ck.get("last_idx", 0) + 1
            print(f"[RESUME] Starting from row {start_idx}")
    elif force_start is not None:
        start_idx = force_start
        print(f"[FORCE START] Starting from row {start_idx}")

    stats = {"checked": 0, "deleted": 0, "active": 0, "not_found": 0, "errors": 0, "fb_backfilled": 0}

    for idx, row in df.iterrows():
        if idx < start_idx:
            continue

        firmenname = str(row.get("Firmenname", "")).strip()
        if not firmenname or firmenname == "nan":
            df.at[idx, "GELÖSCHT"] = -1
            stats["errors"] += 1
            continue

        result = search_company(firmenname, limit=5)
        if result is None:
            df.at[idx, "GELÖSCHT"] = -1
            print(f"  [{idx}] {firmenname}: ERROR (no response)")
            stats["errors"] += 1
        # opendata.host returns {"companies": [...]} — no errorCode field
        elif result.get("companies"):
            companies = result["companies"]
            n_results = len(companies)

            # Try firmenbuchnr exact match first
            matched = None
            fb_input = str(row.get("Firmenbuchnr", "")).strip().lower().lstrip("fn").strip()
            for c in companies:
                reg = str(c.get("reg-no", "")).strip().lower().lstrip("fn").strip()
                if fb_input and reg and (fb_input == reg or fb_input in reg or reg in fb_input):
                    matched = c
                    break

            # No firmenbuchnr match → apply address-confidence logic for single-result
            if not matched and n_results == 1:
                company = companies[0]
                addr = company.get("business-address", {}) or {}

                row_street = str(row.get("Hauptadr_Strasse", "")).strip()
                row_plz = str(row.get("Hauptadr_PLZ", "")).strip()
                row_city = str(row.get("Hauptadr_Ort", "")).strip()

                api_street = addr.get("street-address", "")
                api_number = addr.get("street-number", "")
                api_plz = addr.get("postal-code", "")
                api_city = addr.get("city", "")

                confidence = address_confidence(row_street, row_plz, row_city,
                                                api_street, api_number, api_plz, api_city)

                if confidence >= 0.6:
                    matched = company
                    fb_api = company.get("reg-no", "").strip()
                    if fb_api and confidence >= 0.8:
                        df.at[idx, "Firmenbuchnr"] = fb_api
                        stats["fb_backfilled"] += 1
                        print(f"  [{idx}] {firmenname}: addr match (conf={confidence:.1f}) → FB backfill: {fb_api}")
                else:
                    matched = company
                    print(f"  [{idx}] {firmenname}: single result but addr mismatch (conf={confidence:.1f}) → taking anyway")
            elif not matched:
                matched = companies[0]
                print(f"  [{idx}] {firmenname}: {n_results} results, no FB match → using first: {format_company(matched)}")

            # Determine GELÖSCHT status
            if is_deleted(matched):
                df.at[idx, "GELÖSCHT"] = 1
                print(f"  [{idx}] GELÖSCHT | {format_company(matched)}")
                stats["deleted"] += 1
            else:
                df.at[idx, "GELÖSCHT"] = 0
                if n_results > 1 or not matched:
                    print(f"  [{idx}] aktiv | {format_company(matched)}")
                stats["active"] += 1
            stats["checked"] += 1

        else:
            df.at[idx, "GELÖSCHT"] = -1
            print(f"  [{idx}] {firmenname}: keine Daten gefunden (-1)")
            stats["not_found"] += 1

        time.sleep(1.1)

        # Checkpoint every N rows — persist immediately on error too
        if (idx + 1) % checkpoint_every == 0:
            df.to_excel(output_file, index=False)
            with open(output_file + ".checkpoint.json", "w") as f:
                json.dump({"last_idx": idx, **stats}, f)
            print(f"  [checkpoint {idx+1}/{total}]")

    # Final save
    df.to_excel(output_file, index=False)
    if os.path.exists(output_file + ".checkpoint.json"):
        os.remove(output_file + ".checkpoint.json")

    print(f"\n=== DONE ===")
    print(f"Checked:    {stats['checked']}")
    print(f"Active:     {stats['active']}")
    print(f"Deleted:    {stats['deleted']}")
    print(f"Not found:  {stats['not_found']}")
    print(f"Errors:     {stats['errors']}")
    print(f"FB backfill:{stats['fb_backfilled']}")
    print(f"Output: {output_file}")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input_file")
    ap.add_argument("output_file")
    ap.add_argument("--limit", type=int, default=None, help="Limit to N companies (for test runs)")
    ap.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    ap.add_argument("--force-start", type=int, default=None, help="Force start from row N (0-based)")
    ap.add_argument("--checkpoint-every", type=int, default=25)
    args = ap.parse_args()
    process(args.input_file, args.output_file, limit=args.limit,
            checkpoint_every=args.checkpoint_every, resume=args.resume,
            force_start=args.force_start)