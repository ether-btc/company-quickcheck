#!/usr/bin/env python3
"""Batch processing logic for company status checks."""

import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path

import pandas as pd

from .config import config
from .api import address_confidence, format_company, is_deleted, search_company
from .api import search_with_correlation, build_address_fields
from .rate_limiter import AdaptiveRateLimiter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def process_batch(input_file: str, output_file: str, limit: int = None,
                  checkpoint_every: int = 25, resume: bool = False,
                  force_start: int = None, use_stealth: bool = False,
                  adaptive: bool = True,
                  correlation_mode: str = "auto",
                  correlation_min_confidence: float = 0.70) -> dict:
    """
    Process companies with address-aware matching and Firmenbuchnr backfill.

    force_start: skip all rows before this index (0-based). Useful for
                 resuming after a partial test run with known bad output.
    adaptive:    use AdaptiveRateLimiter instead of fixed delay (default True).
                 Set False to preserve the old fixed-sleep behaviour.
    correlation_mode: CorrelationMatcher mode (auto/strict/lenient, default: auto)
    correlation_min_confidence: minimum composite confidence to accept (default: 0.70)
    """
    # Validate input file exists
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    # Disk space check — prevent corrupt Excel writes when disk is full
    output_dir = Path(output_file).parent
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(str(output_dir))
    min_free = 1 * 1024**3  # 1 GB minimum
    if usage.free < min_free:
        free_gb = usage.free / 1024**3
        raise RuntimeError(
            f"Insufficient disk space: {free_gb:.1f} GB free at {output_dir}, "
            f"need at least 1 GB for Excel output"
        )

    # Load data with timeout (90s)
    if not input_file.endswith(".csv"):
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(pd.read_excel, input_file)
            df = future.result(timeout=90)
    else:
        df = pd.read_csv(input_file)

    # === Row slicing: handle limit, force_start, and their interaction ===
    # BUG FIX: Force-start + limit NOOP bug.
    # Old code: df.head(limit) first (rows 0-99), then force_start skips all (idx 0-99 < 150 → 0 rows).
    # New code: apply force_start slice first, then limit from the remaining rows.
    if force_start is not None and limit is not None:
        df = df.iloc[force_start:force_start + limit].copy()
        # Reindex to match original row numbers (for df.at writes and logging)
        df.index = range(force_start, force_start + len(df))
    elif force_start is not None:
        df = df.iloc[force_start:].copy()
        df.index = range(force_start, force_start + len(df))
    elif limit is not None:
        df = df.head(limit).copy()

    total = len(df)

    # Init columns
    if "GELÖSCHT" not in df.columns:
        df["GELÖSCHT"] = 0
    if "AA" not in df.columns:
        df["AA"] = ""

    # Checkpoint resume — priority 1: .checkpoint.json (interrupted run)
    # Priority 2: existing output file (completed run, checkpoint deleted)
    start_idx = 0
    if resume and force_start is None and os.path.exists(output_file + ".checkpoint.json"):
        with open(output_file + ".checkpoint.json") as f:
            ck = json.load(f)
            start_idx = ck.get("last_idx", 0) + 1
            logger.info(f"[RESUME] Starting from row {start_idx} (checkpoint)")
    elif resume and force_start is None and os.path.exists(output_file):
        # Smart resume: read existing output, skip rows with filled GELÖSCHT
        try:
            if output_file.endswith(".csv"):
                existing_df = pd.read_csv(output_file)
            else:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(pd.read_excel, output_file)
                    existing_df = future.result(timeout=90)
            if "GELÖSCHT" in existing_df.columns:
                filled_mask = existing_df["GELÖSCHT"].notna()
                filled_count = filled_mask.sum()
                # Find last contiguous filled row (gaps indicate incomplete batch)
                last_filled = -1
                for i in range(len(existing_df)):
                    if filled_mask.iloc[i]:
                        last_filled = i
                    else:
                        break  # stop at first gap
                if last_filled >= 0:
                    start_idx = last_filled + 1
                    logger.info(
                        f"[RESUME] Output file has {filled_count}/{len(existing_df)} "
                        f"filled rows, resuming from row {start_idx}"
                    )
                else:
                    logger.info(f"[RESUME] Output file exists but no filled GELÖSCHT rows")
        except Exception as e:
            logger.warning(f"[RESUME] Failed to read existing output: {e} — starting from 0")
    elif force_start is not None:
        start_idx = force_start if force_start < total else 0
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
            logger.warning(f"  [{idx}] Missing company name")
            stats["errors"] += 1
            continue

        result = search_company(firmenname, limit=5, use_stealth=use_stealth,
                                rate_limiter=rate_limiter)
        if result is None:
            df.at[idx, "GELÖSCHT"] = -1
            logger.error(f"  [{idx}] {firmenname}: ERROR (no response)")
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

            # No FB match → apply correlation-enhanced disambiguation
            if not matched:
                if n_results == 1:
                    # Single result: use existing address_confidence
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
                else:
                    # Multiple results: use correlation-enhanced disambiguation
                    uid_input = str(row.get("UID_Nummer", "")).strip()
                    addr_fields = build_address_fields(row)
                    matched_company, match_result = search_with_correlation(
                        name=firmenname,
                        fb_input=fb_input,
                        uid_input=uid_input,
                        address_fields=addr_fields,
                        candidates=companies,
                        mode=correlation_mode,
                        min_confidence=correlation_min_confidence,
                    )
                    if matched_company:
                        matched = matched_company
                        logger.info(f"  [{idx}] {firmenname}: correlation match (conf={match_result.composite_confidence:.2f}, "
                                    f"name={match_result.name_confidence:.2f}, addr={match_result.address_confidence:.2f}, "
                                    f"reason={match_result.fallback_reason}) → {format_company(matched)}")
                        # Backfill FB if confidence >= 0.80
                        if match_result.composite_confidence >= 0.80:
                            fb_api = matched.get("reg-no", "").strip()
                            if fb_api:
                                df.at[idx, "Firmenbuchnr"] = fb_api
                                stats["fb_backfilled"] += 1
                                logger.info(f"  [{idx}] {firmenname}: correlation FB backfill: {fb_api}")
                    else:
                        matched = companies[0]
                        logger.info(f"  [{idx}] {firmenname}: {n_results} results, no FB match, "
                                    f"no correlation above threshold → using first: {format_company(matched)}")

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