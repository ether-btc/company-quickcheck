#!/usr/bin/env python3
"""Batch processing logic for company status checks."""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path

import pandas as pd
import re
from .config import config
from .api import address_confidence, format_company, is_deleted, search_company
from .rate_limiter import AdaptiveRateLimiter

from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def safe_search_company(*args, **kwargs):
    return search_company(*args, **kwargs)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def process_batch(input_file: str, output_file: str, limit: int = None,
                          checkpoint_every: int = 25, resume: bool = False,
                          force_start: int = None, use_stealth: bool = False,
                          adaptive: bool = True) -> dict:
            """
            Process companies with address-aware matching and Firmenbuchnr backfill.
        
            force_start: skip all rows before this index (0-based). Useful for
                         resuming after a partial test run with known bad output.
            adaptive:    use AdaptiveRateLimiter instead of fixed delay (default True).
                         Set False to preserve the old fixed-sleep behaviour.
            """
            # Validate input file exists
            input_path = Path(input_file)
            if not input_path.exists():
                raise FileNotFoundError(f"Input file not found: {input_file}")
        
            # Load data with timeout (90s)
            if not input_file.endswith(".csv"):
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(pd.read_excel, input_file)
                    df = future.result(timeout=90)
            else:
                df = pd.read_csv(input_file)
        
            if limit:
                df = df.head(limit).copy()
        
            required_columns = ["Firmenname", "Firmenbuchnr", "Hauptadr_Strasse", "Hauptadr_PLZ", "Hauptadr_Ort"]
            for col in required_columns:
                if col not in df.columns:
                    raise ValueError(f"Input file missing required column: {col}")
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
                    logger.info(f"[RESUME] Starting from row {start_idx}")
            elif force_start is not None:
                start_idx = force_start
                logger.info(f"[FORCE START] Starting from row {start_idx}")
        
            stats = {"checked": 0, "deleted": 0, "active": 0, "not_found": 0, "errors": 0, "fb_backfilled": 0}
        
            # Adaptive rate limiter — replaces fixed sleep
            rate_limiter = config.build_rate_limiter() if adaptive else None
            if rate_limiter:
                logger.info(f"[rate] adaptive mode | initial={rate_limiter.current_delay:.2f}s")
            else:
                logger.info(f"[rate] fixed delay={config.get_rate_limit_delay():.2f}s")
        
for idx, row in df.iterrows():
    if idx < start_idx:
        continue

    firmenname = str(row.get("Firmenname", "")).strip()
    if not firmenname or firmenname == "nan":
        df.at[idx, "GELÖSCHT"] = -1
        logger.warning(f" [{idx}] Missing company name")
        stats["errors"] += 1
        continue
        
    result = safe_search_company(firmenname, limit=5, use_stealth=use_stealth,
                             rate_limiter=rate_limiter)
    if result is None:
    df.at[idx, "GELÖSCHT"] = -1
    logger.error(f" [{idx}] {firmenname}: ERROR (no response)")
    stats["errors"] += 1
    # opendata.host returns {"companies": [...]} — no errorCode field
    elif result.get("companies"):
            companies = result["companies"]
            for c in companies:
                if "reg-no" not in c:
                    raise ValueError(f"Company missing 'reg-no' field: {c}")
            n_results = len(companies)
            # Try firmenbuchnr exact match first
            matched = None
            fb_input = str(row.get("Firmenbuchnr", "")).strip().lower().lstrip("fn").strip()
            for c in companies:
                reg = str(c.get("reg-no", "")).strip().lower().lstrip("fn").strip()
                if fb_input and reg and (fb_input == reg or fb_input in reg or reg in fb_input):
                    matched = c
                    break
                    raise ValueError(f"Company missing 'reg-no' field: {c}")
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
                                logger.info(f"  [{idx}] {firmenname}: addr match (conf={confidence:.1f}) → FB backfill: {fb_api}")
                        else:
                            matched = company
                            logger.warning(f"  [{idx}] {firmenname}: single result but addr mismatch (conf={confidence:.1f}) → taking anyway")
                    elif not matched:
                        matched = companies[0]
                        logger.info(f"  [{idx}] {firmenname}: {n_results} results, no FB match → using first: {format_company(matched)}")
        
                    # Determine GELÖSCHT status
                    if is_deleted(matched):
                        df.at[idx, "GELÖSCHT"] = 1
                        logger.info(f"  [{idx}] GELÖSCHT | {format_company(matched)}")
                        stats["deleted"] += 1
                    else:
                        df.at[idx, "GELÖSCHT"] = 0
                        if n_results > 1 or not matched:
                            logger.info(f"  [{idx}] aktiv | {format_company(matched)}")
                        stats["active"] += 1
                    stats["checked"] += 1
        
                else:
                    df.at[idx, "GELÖSCHT"] = -1
                    logger.warning(f"  [{idx}] {firmenname}: keine Daten gefunden (-1)")
                    stats["not_found"] += 1
        
                # Sleep: adaptive rate limiter handles it (wait + record), fixed fallback otherwise
                if rate_limiter:
                    pass  # wait + record already done inside search_company
                else:
                    time.sleep(config.get_rate_limit_delay())
        
                # Checkpoint every N rows — write checkpoint BEFORE Excel save (race condition fix)
                if (idx + 1) % checkpoint_every == 0:
                    # Write checkpoint first (so crash after Excel save still allows correct resume)
                    try:
                        with open(output_file + ".checkpoint.json", "w") as f:
                            json.dump({"last_idx": idx, **stats}, f)
                    except OSError as e:
                        logger.error(f"  [checkpoint] Failed to write checkpoint: {e}")
                    # Then save Excel (no timeout here — final save is more important)
                    df.to_excel(output_file, index=False)
                    logger.info(f"  [checkpoint {idx+1}/{total}]")
        
            # Final save (with timeout)
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(df.to_excel, output_file, index=False)
                future.result(timeout=60)
            if os.path.exists(output_file + ".checkpoint.json"):
                os.remove(output_file + ".checkpoint.json")
        
            logger.info(f"=== DONE ===")
            logger.info(f"Checked:    {stats['checked']}")
            logger.info(f"Active:     {stats['active']}")
            logger.info(f"Deleted:    {stats['deleted']}")
            logger.info(f"Not found:  {stats['not_found']}")
    logger.info(f"Errors:     {stats['errors']}")
    logger.info(f"FB backfill:{stats['fb_backfilled']}")
    logger.info(f"Output: {output_file}")
    return stats