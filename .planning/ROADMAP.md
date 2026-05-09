# Company QuickCheck — Roadmap

**Last updated:** 2026-05-09

## Phases

### Phase 1: Project Foundation
- [x] Copy existing firmen_quickcheck.py from skill to scripts/
- [ ] Create SPEC.md, ROADMAP.md, STATE.md
- [ ] requirements.txt
- [ ] README.md
- [ ] Git init + push to GitHub

### Phase 2: CLI + stealth-core Integration
- [ ] Refactor firmen_quickcheck.py into proper Python module (`company_quickcheck/`)
- [ ] Add CLI argument parsing (argparse or click)
- [ ] Subcommands: check, batch, status
- [ ] Integrate stealth-core as HTTP backend (subprocess call to `stealth-core fetch`)
- [ ] Fallback to direct requests if stealth-core unavailable

### Phase 3: Multi-Country Support
- [ ] UK: Companies House API integration
- [ ] US: SEC EDGAR company search
- [ ] Unified output format across countries

### Phase 4: Polish + Release
- [ ] Versioned release (v1.0.0)
- [ ] pip installable package
- [ ] Comprehensive README with examples

---

## Progress

| Phase | Target | Status |
|-------|--------|--------|
| 1 | 2026-05-09 | In Progress |
| 2 | TBD | TODO |
| 3 | TBD | TODO |
| 4 | TBD | TODO |

**Overall: 0%**