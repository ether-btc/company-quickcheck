#!/usr/bin/env python3
"""API interactions for opendata.host and stealth-core."""

import os
import time
import json
import re
import requests
from typing import Dict, List, Any, Optional


API_KEY = os.getenv("OPENDATA_API_KEY", "").strip()
if not API_KEY:
    raise ValueError("OPENDATA_API_KEY not set. Add to ~/.hermes/.env or export before running.")


BASE_URL = "https://api.opendata.host/1.0"


def normalize_address(addr: str) -> str:
    """Remove umlauts, punctuation, common abbreviations for fuzzy matching."""
    if not addr:
        return ""
    addr = addr.lower()
    # Umlauts
    addr = addr.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe").replace("ß", "ss")
    # Common abbreviations
    # Match "str." or "str" at the end of a word (before a word boundary)
    addr = re.sub(r"str\.?(?=\b)", "strasse", addr)
    addr = re.sub(r"gasse(?=\b)", "gasse", addr)
    # Remove punctuation, extra spaces
    addr = re.sub(r"[^\w\s]", "", addr)
    addr = re.sub(r"\s+", " ", addr).strip()
    return addr


def address_confidence(row_addr: str, row_plz: str, row_city: str,
                       api_street: str, api_number: str, api_plz: str, api_city: str) -> float:
    """
    Calculate how confident we are that a spreadsheet row matches an API result by address.
    Returns 0.0 (no match) to 1.0 (high confidence).
    """
    if not row_plz or not api_plz:
        return 0.0

    # PLZ must match exactly
    plz_match = row_plz.strip() == api_plz.strip()

    # City must match (normalized)
    city_match = normalize_address(row_city) == normalize_address(api_city)

    # Street: try to match street name (ignore number differences for now)
    api_full_street = f"{api_street} {api_number}".strip() if api_number else api_street
    row_street_norm = normalize_address(row_addr)
    api_street_norm = normalize_address(api_full_street)

    # Exact street match after normalization
    street_exact = row_street_norm == api_street_norm

    # Partial street match (one contains the other and is reasonably long)
    street_partial = (
        len(row_street_norm) >= 5 and len(api_street_norm) >= 5 and
        (row_street_norm in api_street_norm or api_street_norm in row_street_norm)
    )

    # Calculate score
    if not plz_match or not city_match:
        return 0.0

    score = 0.0
    if street_exact:
        score = 1.0
    elif street_partial:
        score = 0.8
    else:
        # Try street name alone (without number) — handles API that omits street number
        row_street_name = " ".join(row_street_norm.split()[:-1]) if row_street_norm.split() else row_street_norm
        api_street_name = " ".join(api_street_norm.split()[:-1]) if api_street_norm.split() else api_street_norm
        if row_street_name and api_street_name and row_street_name == api_street_name:
            score = 0.75

    return score


def search_opendata(name: str, limit: int = 5) -> Optional[Dict]:
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
            print(f"    [429 rate limited] waiting {wait}s")
            time.sleep(wait)
            return None
        if resp.status_code == 401:
            raise PermissionError("Invalid API key (401 Unauthorized)")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    [error] {e}")
        return None


def search_stealth_core(name: str, limit: int = 5) -> Optional[Dict]:
    """Search using stealth-core as subprocess."""
    import subprocess
    import json

    cmd = ["stealth-core", "fetch", f"registered-companies/find?company-name={name}&limit={limit}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"    [stealth-core error] {result.stderr}")
            return None
        return json.loads(result.stdout)
    except Exception as e:
        print(f"    [stealth-core exception] {e}")
        return None


def search_company(name: str, limit: int = 5, use_stealth: bool = False) -> Optional[Dict]:
    """Search for companies using opendata.host or stealth-core."""
    if use_stealth:
        return search_stealth_core(name, limit)
    else:
        return search_opendata(name, limit)


def is_deleted(company: Dict) -> bool:
    """True if company status is 'cancelled' (gelöscht/geschlossen)."""
    return str(company.get("reg-status", "")).lower() == "cancelled"


def format_company(company: Dict) -> str:
    return f"{company.get('business-name', '?')} [{company.get('reg-no', '?')} / {company.get('reg-status', '?')}]"
