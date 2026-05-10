#!/usr/bin/env python3
#!/usr/bin/env python3
"""Batch processing logic for company status checks."""

import pandas as pd
import time
import json
import os
import logging
from .config import config
from .api import search_company, is_deleted, format_company, address_confidence

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def process_batch(input_file: str, output_file: str, limit: int = None,
                  checkpoint_every: int = 25, resume: bool = False,
                  force_start: int = None, use_stealth: bool = False) -> dict:
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
            print(f"  [{idx}] Missing company name")
            stats["errors"] += 1
            continue

        result = search_company(firmenname, limit=5, use_stealth=use_stealth)
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

        time.sleep(config.get_rate_limit_delay())

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