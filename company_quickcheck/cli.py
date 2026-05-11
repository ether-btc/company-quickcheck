#!/usr/bin/env python3
"""Command-line interface for company-quickcheck."""

import argparse
import sys
from .core import process_batch
from .api import search_company, is_deleted, format_company
from . import __version__


def check_company(args: argparse.Namespace) -> None:
    """Check a single company and print the result."""
    name = args.name
    use_stealth = args.stealth
    result = search_company(name, limit=5, use_stealth=use_stealth)
    if result is None:
        print(f"Error: Could not fetch data for {name}")
        sys.exit(1)
    if result.get("companies"):
        company = result["companies"][0]
        deleted = is_deleted(company)
        status = "GELÖSCHT" if deleted else "aktiv"
        print(f"{name}: {status}")
        print(f"  Firma: {company.get('business-name', '?')}")
        print(f"  FB-Nr: {company.get('reg-no', '?')}")
        print(f"  Status: {company.get('reg-status', '?')}")
        print(f"  Adresse: {company.get('business-address', {}).get('street-address', '?')} {company.get('business-address', {}).get('street-number', '?')}, {company.get('business-address', {}).get('postal-code', '?')} {company.get('business-address', {}).get('city', '?')}")
    else:
        print(f"{name}: nicht gefunden (-1)")


def batch_process(args: argparse.Namespace) -> None:
    """Process a batch of companies from input file to output file."""
    input_file = args.input_file
    output_file = args.output_file
    limit = args.limit
    use_stealth = args.stealth
    checkpoint_every = args.checkpoint_every
    resume = args.resume
    force_start = args.force_start
    adaptive = not args.no_adaptive  # flag is --no-adaptive, default True
    correlation_mode = args.correlation_mode
    correlation_min_confidence = args.correlation_min_confidence

    stats = process_batch(
        input_file,
        output_file,
        limit=limit,
        checkpoint_every=checkpoint_every,
        resume=resume,
        force_start=force_start,
        use_stealth=use_stealth,
        adaptive=adaptive,
        correlation_mode=correlation_mode,
        correlation_min_confidence=correlation_min_confidence,
    )
    sys.exit(0 if stats else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Company QuickCheck Austria: Check Austrian company status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check a single company
  python -m company_quickcheck check "Alcatel Austria AG"

  # Batch process spreadsheet
  python -m company_quickcheck batch input.xlsx output.xlsx

  # Resume from checkpoint
  python -m company_quickcheck batch input.xlsx output.xlsx --resume

  # Use stealth-core for requests
  USE_STEALTH_CORE=1 python -m company_quickcheck batch input.xlsx output.xlsx
        """
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # check command
    check_parser = subparsers.add_parser("check", help="Check a single company")
    check_parser.add_argument("name", help="Company name to check")
    check_parser.add_argument("--stealth", action="store_true", help="Use stealth-core for request")
    check_parser.set_defaults(func=check_company)

    # batch command
    batch_parser = subparsers.add_parser("batch", help="Batch process companies from spreadsheet")
    batch_parser.add_argument("input_file", help="Input Excel/CSV file")
    batch_parser.add_argument("output_file", help="Output Excel file")
    batch_parser.add_argument("--limit", type=int, help="Limit to N companies (for testing)")
    batch_parser.add_argument("--stealth", action="store_true", help="Use stealth-core for requests")
    batch_parser.add_argument("--checkpoint-every", type=int, default=25, help="Save checkpoint every N rows")
    batch_parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    batch_parser.add_argument("--force-start", type=int, help="Force start from row N (0-based)")
    batch_parser.add_argument("--no-adaptive", action="store_true",
                              help="Disable adaptive rate limiting (use fixed delay)")
    batch_parser.add_argument("--correlation-mode",
                              choices=["auto", "strict", "lenient"],
                              default="auto",
                              help="Correlation matching mode (default: auto)")
    batch_parser.add_argument("--correlation-min-confidence", type=float,
                              default=0.70,
                              help="Minimum correlation confidence threshold (default: 0.70)")
    batch_parser.set_defaults(func=batch_process)

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Call the appropriate function
    args.func(args)


if __name__ == "__main__":
    main()
