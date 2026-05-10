#!/usr/bin/env python3
"""
Fast batch processor: single-pass opendata.host with NO waiting on 429.
Collects -1 firms for VIES/web retry phases.
Designed for overnight autonomous run.
"""

import json, logging, os, sys, time, signal
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

PROJECT_DIR = "/home/hermes-pi/company-quickcheck"
VENV_PY = f"{PROJECT_DIR}/venv/bin/python"
ENV_FILE = "/home/hermes-pi/.hermes/.env"

INPUT_FILE = "/srv/sync/batch_input_1.xlsx"
OUTPUT_FILE = "/srv/sync/batch_output_1.xlsx"
CHECKPOINT_FILE = OUTPUT_FILE + ".checkpoint.json"
RETRY_QUEUE_FILE = "/srv/sync/retry_queue.json"
FINAL_OUTPUT = "/srv/sync/Unternehmen_checked.xlsx"
MERGED_FILE = "/srv/sync/Unternehmen_merged.xlsx"

CHECKPOINT_EVERY = 25
REQUEST_TIMEOUT = 6  # seconds — fast fail on 429

# Load env
def load_env():
    env = os.environ.copy()
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env

ENV = load_env()

# ─── VIES ───────────────────────────────────────────────────────────────────

def check_vies(uid: str) -> dict:
    import re
    uid_clean = uid.strip().upper()
    if not uid_clean.startswith("ATU"):
        return {"valid": False, "active": False, "error": "not ATU"}
    uid_number = uid_clean[2:]
    try:
        import requests as req
        soap_body = '<?xml version="1.0" encoding="UTF-8"?>\n<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types"><soapenv:Body><urn:checkVat><urn:countryCode>AT</urn:countryCode><urn:vatNumber>' + uid_number + '</urn:vatNumber></urn:checkVat></soapenv:Body></soapenv:Envelope>'
        resp = req.post(
            "https://ec.europa.eu/taxation_customs/vies/services/checkVat",
            data=soap_body.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": ""},
            timeout=6
        )
        if resp.status_code != 200:
            return {"valid": False, "active": False, "error": "HTTP " + str(resp.status_code)}
        text = resp.text
        valid_match = re.search(r"<(\w+:)?valid>(true|false)</\1valid>", text, re.IGNORECASE)
        name_match = re.search(r"<(\w+:)?name>([^<]+)</\1name>", text)
        address_match = re.search(r"<(\w+:)?address>([^<]+)</\1address>", text)
        is_valid = valid_match and valid_match.group(2).lower() == "true"
        return {
            "valid": is_valid,
            "active": is_valid,
            "name": name_match.group(2) if name_match else None,
            "address": address_match.group(2) if address_match else None,
            "error": None
        }
    except Exception as e:
        return {"valid": False, "active": False, "error": str(e)}


# ─── Retry Queue ─────────────────────────────────────────────────────────────

def load_retry_queue() -> list:
    if os.path.exists(RETRY_QUEUE_FILE):
        with open(RETRY_QUEUE_FILE) as f:
            return json.load(f)
    return []

def save_retry_queue(queue: list):
    with open(RETRY_QUEUE_FILE, "w") as f:
        json.dump(queue, f)


# ─── Checkpoint ──────────────────────────────────────────────────────────────

def get_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {"last_idx": -1, "checked": 0, "deleted": 0, "active": 0, "not_found": 0, "errors": 0}

def save_checkpoint(idx: int, stats: dict):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"last_idx": idx, **stats}, f)


# ─── Phase 1: Fast API batch (no waiting on 429) ─────────────────────────────

def run_phase1():
    """Fast pass: process all firms, skip 429 immediately, collect for retry."""
    import pandas as pd
    import requests as req

    logger.info("=== PHASE 1: Fast API batch (no 429 waiting) ===")

    df = pd.read_excel(INPUT_FILE)
    total = len(df)
    logger.info(f"Total firms to process: {total}")

    # Ensure output columns exist
    if "GELÖSCHT" not in df.columns:
        df["GELÖSCHT"] = -1

    # Resume support
    ckpt = get_checkpoint()
    start_idx = max(0, ckpt.get("last_idx", -1) + 1)

    if start_idx > 0:
        logger.info(f"[RESUME] Starting from row {start_idx}")
        # Load existing output if available
        if os.path.exists(OUTPUT_FILE):
            existing = pd.read_excel(OUTPUT_FILE)
            for _, row in existing.iterrows():
                fb = str(row.get("Firmenbuchnr", "")).strip()
                if fb and fb != "nan":
                    mask = df["Firmenbuchnr"].astype(str).str.strip().str.lower() == fb.lower()
                    if mask.any():
                        idx_val = df[mask].index[0]
                        df.at[idx_val, "GELÖSCHT"] = row.get("GELÖSCHT", -1)

    stats = {"checked": 0, "deleted": 0, "active": 0, "not_found": 0, "errors": 0, "skipped_429": 0}
    api_key = ENV.get("OPENDATA_API_KEY", "")
    base_url = "https://api.opendata.host/1.0/registered-companies/find"

    retry_queue = []

    for idx, row in df.iterrows():
        row_idx = df.index.get_loc(idx)
        if row_idx < start_idx:
            continue

        firmenname = str(row.get("Firmenname", "")).strip()
        firmenbuchnr = str(row.get("Firmenbuchnr", "")).strip()
        uid = str(row.get("UID_Nummer", "")).strip()

        if not firmenname or firmenname == "nan":
            df.at[idx, "GELÖSCHT"] = -1
            stats["errors"] += 1
            continue

        try:
            resp = req.get(
                base_url,
                params={"company-name": firmenname, "limit": 1},
                auth=(api_key, ""),
                timeout=REQUEST_TIMEOUT
            )

            if resp.status_code == 429:
                # 429 → skip immediately, add to retry queue
                logger.info(f"[429] {firmenname} → queuing for retry")
                df.at[idx, "GELÖSCHT"] = -1
                stats["skipped_429"] += 1
                retry_queue.append({"idx": idx, "fb": firmenbuchnr, "name": firmenname, "uid": uid})
                # NO WAITING — continue immediately
                # Small sleep to avoid hammering
                time.sleep(0.3)

            elif resp.status_code == 200:
                data = resp.json()
                companies = data.get("companies", [])
                if companies:
                    # Check first result
                    company = companies[0]
                    status = str(company.get("reg-status", "")).lower()
                    if status == "cancelled":
                        df.at[idx, "GELÖSCHT"] = 1
                        stats["deleted"] += 1
                        logger.info(f"[DEL] {firmenname}")
                    else:
                        df.at[idx, "GELÖSCHT"] = 0
                        stats["active"] += 1
                        logger.info(f"[ACT] {firmenname}")
                    stats["checked"] += 1
                else:
                    # No results — add to retry queue (VIES/web later)
                    df.at[idx, "GELÖSCHT"] = -1
                    stats["not_found"] += 1
                    retry_queue.append({"idx": idx, "fb": firmenbuchnr, "name": firmenname, "uid": uid})
                    logger.info(f"[-1] {firmenname} → retry queue")

            else:
                df.at[idx, "GELÖSCHT"] = -1
                stats["errors"] += 1
                retry_queue.append({"idx": idx, "fb": firmenbuchnr, "name": firmenname, "uid": uid})
                logger.info(f"[ERR " + str(resp.status_code) + "] " + firmenname + " → retry queue")

        except Exception as e:
            logger.info("[EXC] " + firmenname + ": " + str(e))
            df.at[idx, "GELÖSCHT"] = -1
            stats["errors"] += 1
            retry_queue.append({"idx": idx, "fb": firmenbuchnr, "name": firmenname, "uid": uid})
            time.sleep(0.5)

        # Checkpoint
        if (row_idx + 1) % CHECKPOINT_EVERY == 0:
            save_checkpoint(row_idx, stats)
            df.to_excel(OUTPUT_FILE, index=False)
            save_retry_queue(retry_queue)
            logger.info(f"[CKPT {row_idx+1}/{total}] checked={stats['checked']} del={stats['deleted']} act={stats['active']} -1={stats['not_found']} err={stats['errors']} 429={stats['skipped_429']} retry_queue={len(retry_queue)}")

        # Small delay between requests (avoid 429 burst)
        time.sleep(1.0)

    # Final save
    save_checkpoint(row_idx, stats)
    df.to_excel(OUTPUT_FILE, index=False)
    save_retry_queue(retry_queue)

    logger.info("=== PHASE 1 DONE ===")
    logger.info("Stats: " + str(stats))
    logger.info("Retry queue: " + str(len(retry_queue)) + " firms")
    return retry_queue


# ─── Phase 2: VIES retry ─────────────────────────────────────────────────────

def run_phase2(retry_queue: list):
    """Process retry queue with VIES."""
    import pandas as pd

    if not retry_queue:
        logger.info("No retry queue — skipping VIES phase")
        return

    logger.info("=== PHASE 2: VIES retry (" + str(len(retry_queue)) + " firms) ===")

    df = pd.read_excel(OUTPUT_FILE)
    updated = 0

    for item in retry_queue:
        idx = item["idx"]
        fb = item["fb"]
        name = item["name"]
        uid = item["uid"]

        # Skip if already has valid status
        current = df.at[idx, "GELÖSCHT"]
        if current in [0, 1]:
            continue

        if not uid or uid == "nan" or not uid.startswith("ATU"):
            logger.info("[SKIP] " + fb + " no UID")
            continue

        logger.info("[VIES] " + fb + " / " + uid)
        vies = check_vies(uid)
        logger.info("[VIES] valid=" + str(vies["valid"]) + " active=" + str(vies["active"]) + " err=" + str(vies["error"]))

        if vies["valid"]:
            df.at[idx, "GELÖSCHT"] = 0  # Active
            updated += 1
            logger.info("[VIES->ACT] " + fb)
        elif vies["error"]:
            # Keep -1 for web scrape phase
            logger.info("[VIES->-1] " + fb + " err=" + str(vies["error"]))

        time.sleep(1.5)  # VIES rate limit

    df.to_excel(OUTPUT_FILE, index=False)
    logger.info("VIES updated: " + str(updated) + " firms")


# ─── Phase 3: Web scrape retry ───────────────────────────────────────────────

def run_phase3():
    """Final retry for remaining -1 using web search."""
    import pandas as pd

    if not os.path.exists(OUTPUT_FILE):
        logger.warning("No output file — skipping web phase")
        return

    df = pd.read_excel(OUTPUT_FILE)
    remaining = df[df["GELÖSCHT"].isna() | (df["GELÖSCHT"] == -1)]
    if remaining.empty:
        logger.info("No -1 remaining — skipping web phase")
        return

    logger.info("=== PHASE 3: Web scrape (" + str(len(remaining)) + " firms) ===")

    import subprocess

    for idx, row in remaining.iterrows():
        fb = str(row.get("Firmenbuchnr", "")).strip()
        name = str(row.get("Firmenname", "")).strip()
        uid = str(row.get("UID_Nummer", "")).strip()

        if not fb or fb == "nan":
            continue

        logger.info("[WEB] " + fb + " / " + name)

        # Try firmenbuch.at via curl
        fb_clean = fb.lstrip("fn").lower()
        url = "https://www.firmenbuch.at/firma/" + fb_clean

        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "--max-time", "8", "-A",
                 "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
                 url],
                capture_output=True, text=True, timeout=12
            )
            html = result.stdout.lower()

            geloscht_kw = ["gelöscht", "cancelled", "erloschen", "wurde gelöscht", "ohne aktive"]
            aktiv_kw = ["aktiv", "eingetragen", "bestehend", "active"]

            found_status = None
            for kw in geloscht_kw:
                if kw in html:
                    found_status = 1
                    break
            if not found_status:
                for kw in aktiv_kw:
                    if kw in html:
                        found_status = 0
                        break

            if found_status is not None:
                df.at[idx, "GELÖSCHT"] = found_status
                logger.info("[WEB->" + str(found_status) + "] " + fb)
            else:
                logger.info("[WEB->?]" + fb + " status unclear")

        except Exception as e:
            logger.info("[WEB ERR] " + fb + ": " + str(e))

        time.sleep(2.0)  # Be polite to web servers

    df.to_excel(OUTPUT_FILE, index=False)


# ─── Merge to final ──────────────────────────────────────────────────────────

def merge_to_final():
    import pandas as pd

    logger.info("=== MERGE TO FINAL ===")

    merged = pd.read_excel(MERGED_FILE)
    batch = pd.read_excel(OUTPUT_FILE)

    # Build lookup from batch
    batch_lookup = {}
    for _, row in batch.iterrows():
        fb = str(row.get("Firmenbuchnr", "")).strip().lower().lstrip("fn")
        status = row.get("GELÖSCHT", -1)
        if fb and fb != "nan":
            batch_lookup[fb] = status

    updated = 0
    for idx, row in merged.iterrows():
        fb = str(row.get("Firmenbuchnr", "")).strip().lower().lstrip("fn")
        if fb and fb != "nan" and fb in batch_lookup:
            new_status = batch_lookup[fb]
            current = merged.at[idx, "GELÖSCHT"]
            if (pd.isna(current) or current == -1) and not pd.isna(new_status) and new_status in [0, 1]:
                merged.at[idx, "GELÖSCHT"] = new_status
                updated += 1

    merged.to_excel(FINAL_OUTPUT, index=False)

    logger.info("Updated: " + str(updated))
    logger.info("Final output: " + FINAL_OUTPUT)
    logger.info("GELÖSCHT distribution:\n" + str(merged["GELÖSCHT"].value_counts(dropna=False).sort_index()))


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    logger.info("Starting autonomous batch run")
    logger.info("PID: " + str(os.getpid()))

    # Phase 1: fast API batch
    retry_queue = run_phase1()

    # Phase 2: VIES
    run_phase2(retry_queue)

    # Phase 3: Web scrape
    run_phase3()

    # Phase 4: Merge
    merge_to_final()

    logger.info("=== ALL DONE ===")


if __name__ == "__main__":
    main()