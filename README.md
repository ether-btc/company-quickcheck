# Company QuickCheck

Batch Austrian company status checker using the opendata.host API.

Validates company registration status (`registered` = active, `cancelled` = deleted) and adds a `GELÖSCHT` column to spreadsheets.

[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/ether-btc/company-quickcheck)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/ether-btc/company-quickcheck)

## Installation

```bash
pip install -r requirements.txt
```

Or install globally:

```bash
pip install .
```

## Usage

### Check a Single Company

```bash
python -m company_quickcheck check "Alcatel Austria AG"
```

With stealth-core integration (if available):

```bash
USE_STEALTH_CORE=1 python -m company_quickcheck check "Alcatel Austria AG" --stealth
```

### Batch Process Spreadsheet

```bash
python -m company_quickcheck batch input.xlsx output.xlsx
```

Options:

```bash
python -m company_quickcheck batch input.xlsx output.xlsx \
  --limit 100 \          # Process only first 100 rows
  --resume \             # Resume from last checkpoint
  --force-start 150 \    # Start from row 150 (0-based)
  --checkpoint-every 50 \ # Checkpoint every 50 rows
  --stealth               # Use stealth-core for requests
```

#### Input Format

| Column | Description |
|--------|-------------|
| `Firmenname` | Company name |
| `Firmenbuchnr` | Firmenbuch number (FB-Nr) |
| `UID_Nummer` | VAT/tax ID |
| `Hauptadr_Strasse` | Street address |
| `Hauptadr_PLZ` | Postal code |
| `Hauptadr_Ort` | City |

#### Output

Same columns + `GELÖSCHT`:

- `1` = cancelled/deleted
- `0` = registered/active
- `-1` = not found

## API

Uses [opendata.host](https://opendata.host) API (`https://api.opendata.host/1.0/registered-companies/find`).

Set `OPENDATA_API_KEY` in environment:

```bash
export OPENDATA_API_KEY="your-api-key-here"
```

If storing the key in `~/.hermes/config.yaml`, ensure the file has restricted permissions:

```bash
chmod 600 ~/.hermes/config.yaml
```

## stealth-core Integration

When `USE_STEALTH_CORE=1` is set, the tool uses `stealth-core` as an HTTP backend for fingerprint-aware, rate-limited requests. If `stealth-core` is unavailable, it falls back to direct requests.

## Checkpointing

Batch runs save progress every `--checkpoint-every` rows. On interrupt, resume with `--resume`:

```bash
python -m company_quickcheck batch input.xlsx output.xlsx --resume
```

## License

MIT
