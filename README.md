# Company QuickCheck

Batch Austrian company status checker using the opendata.host API.

Validates company registration status (`registered` = active, `cancelled` = deleted) and adds a `GELÖSCHT` column to spreadsheets.

## Quick Start

```bash
pip install -r requirements.txt

# Single company check
python scripts/firmen_quickcheck.py --check "Alcatel Austria AG"

# Batch process spreadsheet
python scripts/firmen_quickcheck.py input.xlsx output.xlsx --checkpoint-every 25

# Resume from checkpoint
python scripts/firmen_quickcheck.py input.xlsx output.xlsx --resume
```

## Input Format

| Column | Description |
|--------|-------------|
| `Firmenname` | Company name |
| `Firmenbuchnr` | Firmenbuch number (FB-Nr) |
| `UID_Nummer` | VAT/tax ID |
| `Hauptadr_Strasse` | Street address |
| `Hauptadr_PLZ` | Postal code |
| `Hauptadr_Ort` | City |

## Output

Same columns + `GELÖSCHT`:
- `1` = cancelled/deleted
- `0` = registered/active
- `-1` = not found

## API

Uses [opendata.host](https://opendata.host) API (`https://api.opendata.host/1.0/registered-companies/find`).

Set `OPENDATA_API_KEY` in environment:
```bash
export OPENDATA_API_KEY="your_api_key_here"
```

## stealth-core Integration

Uses `stealth-core` as HTTP backend when available:
```bash
export USE_STEALTH_CORE=1
```

Falls back to direct requests if stealth-core is unavailable.

## Checkpointing

Batch runs save progress every 25 rows. On interrupt, resume with `--resume`:
```bash
python scripts/firmen_quickcheck.py input.xlsx output.xlsx --resume
```

## License

MIT