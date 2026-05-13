#!/usr/bin/env python3
"""API interactions for opendata.host and stealth-core."""

import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import base64
from typing import Dict, List, Any, Optional, Tuple

import requests
from requests.exceptions import RequestException

from .config import config
from .correlation import build_matcher, MatchResult as CorrelationMatchResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Use config for base URL and API key
BASE_URL = config.get_base_url()
API_KEY = config.get_api_key()
if not API_KEY:
    raise ValueError("OPENDATA_API_KEY not set. Add to ~/.hermes/config.yaml or export before running.")


def normalize_address(addr: str, country: str = "AT") -> str:
    """Remove umlauts, punctuation, common abbreviations for fuzzy matching.
    Country parameter determines which normalization rules to apply.
    """
    if not addr:
        return ""
    addr = addr.lower()

    # Apply country-specific normalization
    if country == "AT" or country == "DE":  # German-speaking countries
        # Umlauts
        addr = addr.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe").replace("ß", "ss")
        # Common abbreviations
        # Match "str." or "str" at the end of a word (before a word boundary)
        addr = re.sub(r"str\.?(?=\b)", "strasse", addr)

    # Remove punctuation, extra spaces (common to all countries)
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


def search_opendata(name: str, limit: int = 5,
                    rate_limiter=None) -> Optional[Dict]:
    """Search opendata.host for companies.

    Args:
        name: Company name to search.
        limit: Max results to return (default 5).
        rate_limiter: Optional AdaptiveRateLimiter instance. If provided,
                      wait() is called before the request and the response
                      is recorded afterward for adaptive delay adjustment.
    """
    try:
        logger.info(f"Searching opendata for: {name}")

        # Adaptive wait before request
        if rate_limiter is not None:
            rate_limiter.wait()

        resp = requests.get(
            f"{BASE_URL}/registered-companies/find",
            params={"company-name": name, "limit": limit},
            auth=(API_KEY, ""),
            timeout=20,
        )

        # Record response for adaptive rate limiting
        if rate_limiter is not None:
            rate_limiter.record_response(resp.status_code, dict(resp.headers))

        if resp.status_code == 429:
            logger.warning("Rate limited (429) — backing off via rate limiter")
            return None
        if resp.status_code == 401:
            logger.error("Invalid API key (401 Unauthorized)")
            raise PermissionError("Invalid API key (401 Unauthorized)")
        resp.raise_for_status()
        result = resp.json()
        logger.info(f"Opendata search successful: {len(result.get('companies', []))} results")
        return result
    except PermissionError:
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error in opendata search: {e}")
        return None
    except (requests.exceptions.JSONDecodeError, ValueError) as e:
        logger.error(f"Invalid JSON from opendata API: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in opendata search: {e}")
        return None


def search_stealth_core(name: str, limit: int = 5) -> Optional[Dict]:
    """Search using stealth-core as subprocess.

    API credentials are passed via a temporary file (mode 0o600) to avoid
    exposing them in /proc/*/cmdline.
    """
    import json

    # Check if stealth-core is available in PATH
    if not shutil.which("stealth-core"):
        logger.error("stealth-core binary not found in PATH")
        return None

    # Get stealth config path
    stealth_config = config.get_stealth_core_config_path()
    api_key = config.get_api_key()
    if not api_key:
        logger.error("API key not found for stealth-core integration")
        return None

    # URL-encode the name parameter
    encoded_name = urllib.parse.quote(name, safe='')
    full_url = f"{BASE_URL}/registered-companies/find?company-name={encoded_name}&limit={limit}"

    # Create Authorization header
    auth_header = f"Basic {base64.b64encode(f'{api_key}:'.encode()).decode()}"
    headers_json = json.dumps({"Authorization": auth_header})

    # Write headers to a restricted temp file to avoid exposing the API key
    # on the command line (visible in /proc/*/cmdline to other users).
    hdr_path: Optional[str] = None
    try:
        import tempfile
        hdr_fd, hdr_path = tempfile.mkstemp(suffix=".json", prefix="sc_headers_")
        os.write(hdr_fd, headers_json.encode())
        os.close(hdr_fd)
        os.chmod(hdr_path, 0o600)

        cmd = [
            "stealth-core",
            "-c", stealth_config,
            "fetch",
            full_url,
            "--headers-file", hdr_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (AttributeError, TypeError, FileNotFoundError):
        # stealth-core does not support --headers-file; fall back to --headers
        logger.warning("stealth-core --headers-file not supported, falling back to --headers")
        cmd = [
            "stealth-core",
            "-c", stealth_config,
            "fetch",
            full_url,
            "--headers", headers_json,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    finally:
        if hdr_path:
            try:
                os.unlink(hdr_path)
            except OSError:
                pass

    if result.returncode != 0:
        logger.error(f"Stealth-core error: {result.stderr}")
        return None

    # Parse the stdout to extract JSON body
    output_lines = result.stdout.splitlines()
    json_start = None
    for i, line in enumerate(output_lines):
        stripped = line.strip()
        if stripped.startswith('{') or stripped.startswith('['):
            json_start = i
            break

    if json_start is None:
        logger.error("No JSON body found in stealth-core output")
        return None

    json_str = '\n'.join(output_lines[json_start:])
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        return None


def search_company(name: str, limit: int = 5, use_stealth: bool = False,
                   rate_limiter=None) -> Optional[Dict]:
    """Search for companies using opendata.host or stealth-core.

    Args:
        name: Company name to search.
        limit: Max results to return (default 5).
        use_stealth: Route through stealth-core subprocess (default False).
        rate_limiter: Optional AdaptiveRateLimiter for adaptive delay.
                      Only used for direct opendata requests (not stealth-core).
    """
    if use_stealth:
        return search_stealth_core(name, limit)
    else:
        return search_opendata(name, limit, rate_limiter=rate_limiter)


def is_deleted(company: Dict) -> bool:
    """True if company status is 'cancelled' (gelöscht/geschlossen)."""
    return str(company.get("reg-status", "")).lower() == "cancelled"


def format_company(company: Dict) -> str:
    return f"{company.get('business-name', '?')} [{company.get('reg-no', '?')} / {company.get('reg-status', '?')}]"


# ── Correlation-Enhanced Search ───────────────────────────────────────────────

def build_address_fields(row: Dict) -> Dict:
    """Extract address fields from a spreadsheet row dict."""
    return {
        "name": str(row.get("Firmenname", "")).strip(),
        "street": str(row.get("Hauptadr_Strasse", "")).strip(),
        "number": "",  # Spreadsheet may not separate number from street
        "plz": str(row.get("Hauptadr_PLZ", "")).strip(),
        "city": str(row.get("Hauptadr_Ort", "")).strip(),
    }


def search_with_correlation(name: str,
                            fb_input: str,
                            uid_input: str,
                            address_fields: Dict,
                            candidates: List[Dict],
                            mode: str = "auto",
                            min_confidence: float = 0.70) -> Tuple[Optional[Dict], CorrelationMatchResult]:
    """
    Correlation-enhanced disambiguation of API candidates.

    Uses CorrelationMatcher to score all candidates by weighted multi-field
    confidence (name similarity + street + city + plz). Falls back to exact
    FB/UID match before correlation scoring.

    Args:
        name: company name from input
        fb_input: firmenbuchnr from input
        uid_input: uid from input
        address_fields: dict with keys: name, street, number, plz, city
        candidates: list of API result dicts
        mode: matching mode (auto/strict/lenient)
        min_confidence: minimum composite confidence to accept

    Returns:
        Tuple of (matched_company_or_None, CorrelationMatchResult)
    """
    # Build matcher with defaults from correlation module
    matcher = build_matcher(mode=mode, min_confidence=min_confidence)

    result = matcher.match(
        candidates=candidates,
        fb_input=fb_input,
        uid_input=uid_input,
        address_fields=address_fields,
        mode=mode,
        min_confidence=min_confidence,
    )

    return result.company, result